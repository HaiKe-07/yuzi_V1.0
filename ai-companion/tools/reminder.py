"""工具：定时提醒（T4-02）。

用户说"提醒我 X 时间做 Y"，到时间通过语音和桌面通知提醒（对接 T3-06）。
验收：提醒到时间准确触发，支持取消和修改。

设计要点：
1. 中文时间解析：相对时间（X分钟后/X小时后/半小时后）、今天/明天/后天、周X、
   时段（上午/下午/傍晚/晚上…）配合"X点/ X点Y分/ X点半"
2. 后台线程轮询到期提醒并回调（语音 + 桌面通知由上层注入 on_trigger）
3. 注入 now 便于离线单测时间逻辑，不依赖真实睡眠
4. 支持：新增、查询、取消、修改（改内容/改时间）
5. 可持久化到项目 state/reminders.json

配置（config.yaml）：
    tools.reminder.enabled: false
    tools.reminder.check_interval: 1   # 轮询秒数
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from utils.config import config

WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
# 时段 → 默认小时
PERIOD_HOUR = {
    "凌晨": 5, "早上": 8, "清晨": 6, "上午": 9, "白天": 10,
    "中午": 12, "下午": 14, "傍晚": 17, "晚上": 19, "今晚": 19, "深夜": 22,
}
DAY_SHIFT = {"今天": 0, "明天": 1, "后天": 2, "大后天": 3}
VALID_PERIODS = sorted(PERIOD_HOUR, key=len, reverse=True)

# 相对时间：(\d+或半)(分钟|小时|天|秒)后
_REL = re.compile(r"(\d+|半)\s*(分钟|小时|天|秒)\s*(?:后|之后)?")
# 时段：晚上8点 / 上午9点30分 / 下午3点半
_TIME = re.compile(
    r"(" + "|".join(VALID_PERIODS) + r")?\s*(\d{1,2})\s*点\s*(?:(\d{1,2})\s*分(?:钟)?|(半|整))?\s*"
)
# 日（今天/明天/…）或 周X（下/本周可选）
_DAY = re.compile(r"(今天|明天|后天|大后天|周日|周天|周[一二三四五六日]|星期[一二三四五六日天]|(?:下|这)周[一二三四五六日天])")


@dataclass
class Reminder:
    """一条提醒。"""
    id: str
    remind_at: str            # ISO 格式，存字符串便于序列化
    text: str                 # 要做什么
    created_at: str
    fired: bool = False

    @property
    def dt(self) -> datetime:
        return datetime.fromisoformat(self.remind_at)

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 0. 时间解析（纯逻辑，now 可注入以便测试）
# ============================================================
def _next_weekday(now: datetime, weekday: int) -> datetime:
    """返回本周尚未过去的 weekday（否则顺延到下一周）。保留当前时刻（含秒）。"""
    days_ahead = (weekday - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return now + timedelta(days=days_ahead)


def _set_hm(base: datetime, hour: int, minute: int) -> datetime:
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _apply_day(now: datetime, day_expr: str) -> Optional[datetime]:
    """解析日表达式到基准日期（保留时刻）；无法识别返回 None。"""
    d = day_expr or ""
    if d in DAY_SHIFT:
        return now + timedelta(days=DAY_SHIFT[d])
    m = re.match(r"(?:下|这)?周([一二三四五六日天])|星期([一二三四五六日天])", d)
    if m:
        key = m.group(1) or m.group(2)
        return _next_weekday(now, WEEKDAY_MAP[key])
    return None


def parse_time(text: str, now: Optional[datetime] = None) -> tuple:
    """从文本解析提醒时间。

    Returns:
        (remind_at: datetime | None, cleaned: str)：
        - remind_at：解析出的触发时间；未能识别任何时间则 None
        - cleaned：去掉时间表达式与"提醒我"后的剩余文本（要做什么）
    """
    now = now or datetime.now()
    t = (text or "").strip()

    # 1. 相对时间优先
    m_rel = _REL.search(t)
    if m_rel:
        unit = m_rel.group(2)
        val = m_rel.group(1)
        num = 0.5 if val == "半" else int(val)
        delta = {
            "分钟": timedelta(minutes=num),
            "小时": timedelta(hours=num),
            "天": timedelta(days=num),
            "秒": timedelta(seconds=num),
        }[unit]
        body = (t[: m_rel.start()] + t[m_rel.end():]).strip()
        cleaned = re.sub(r"^\s*(提醒我|请提醒我|帮我提醒我)\s*", "", body).strip()
        return now + delta, cleaned

    # 2. 采集日表达式
    m_day = _DAY.search(t)
    base = _apply_day(now, m_day.group(0)) if m_day else None
    day_clean = t
    if m_day:
        day_clean = t[: m_day.start()] + t[m_day.end():]

    # 3. 采集时分
    m_time = _TIME.search(day_clean)
    if not m_time:
        return None, t

    period = m_time.group(1) or ""
    hour = int(m_time.group(2))
    if m_time.group(4) == "半":
        minute = 30
    elif m_time.group(3) is not None:
        minute = int(m_time.group(3))
    else:
        minute = 0

    # 12 小时制 → 24 小时（下午/晚上/傍晚/今晚/深夜 + 小时<12 时 +12）
    if period in ("下午", "晚上", "傍晚", "深夜", "今晚") and 0 < hour < 12:
        hour += 12

    time_clean = day_clean[: m_time.start()] + day_clean[m_time.end():]

    # 4. 组合基准日期
    if base is None:
        placeholder = now.replace(second=0, microsecond=0)
        remind_at = _set_hm(placeholder, hour, minute)
        # 只有时间没给日期，若已过则推到明天
        if remind_at <= now:
            remind_at = remind_at + timedelta(days=1)
    else:
        remind_at = _set_hm(base, hour, minute)
        if remind_at <= now:
            remind_at = remind_at + timedelta(days=1)

    cleaned = re.sub(r"^\s*(提醒我|请提醒我|帮我提醒我)\s*", "", time_clean).strip()
    return remind_at, cleaned


# ============================================================
# 1. 提醒管理器
# ============================================================
class ReminderManager:
    """线程安全地管理提醒；后台线程到点触发回调。"""

    def __init__(
        self,
        now_fn: Optional[Callable[[], datetime]] = None,
        on_trigger: Optional[Callable[[Reminder], None]] = None,
        state_path=None,
    ):
        cfg = config.all()
        rem = (cfg.get("tools", {}).get("reminder", {}) or {})
        self.enabled = bool(rem.get("enabled", False))
        self._check_interval = float(rem.get("check_interval", 1))
        self._now_fn = now_fn or (lambda: datetime.now())
        self._on_trigger = on_trigger or (lambda r: print(f"[reminder] {r.text}"))
        self._reminders: dict[str, Reminder] = {}
        self._lock = threading.Lock()
        self._state_path = state_path or config.path("state", "reminders.json")
        self._thread = None
        self._stop_evt = threading.Event()
        self._load()

    # ---- 增删改查 ----
    def add(self, remind_at: datetime, text: str) -> Reminder:
        now = self._now_fn()
        r = Reminder(
            id="r_" + uuid.uuid4().hex[:10],
            remind_at=remind_at.isoformat(),
            text=text or "提醒",
            created_at=now.isoformat(),
        )
        with self._lock:
            self._reminders[r.id] = r
        self._save()
        return r

    def parse_and_add(self, text: str) -> tuple:
        """解析文本并新增提醒。

        Returns:
            (ok: bool, msg: str, reminder | None)
        """
        remind_at, cleaned = parse_time(text, self._now_fn())
        if remind_at is None:
            return False, "没识别到提醒时间，例如“提醒我明天上午9点喝水”", None
        r = self.add(remind_at, cleaned)
        return True, f"好的，{remind_at.strftime('%m-%d %H:%M')} 提醒你：{cleaned}", r

    def cancel(self, ident: str) -> bool:
        """按 id 或文本关键词取消。返回是否删除了提醒。"""
        with self._lock:
            keys = list(self._reminders)
            target = None
            if ident in self._reminders:
                target = ident
            else:
                # 关键词匹配 text 或 id
                low = ident.lower()
                for k in keys:
                    if low in self._reminders[k].text.lower() or low in k.lower():
                        target = k
                        break
            if target is None:
                return False
            del self._reminders[target]
        self._save()
        return True

    def modify(self, ident: str, new_text: Optional[str] = None, new_time: Optional[datetime] = None) -> bool:
        """修改某条提醒的内容或时间。"""
        with self._lock:
            r = self._reminders.get(ident)
            if r is None:
                # 关键词定位
                low = ident.lower()
                for k, v in self._reminders.items():
                    if low in v.text.lower():
                        r = v
                        break
            if r is None:
                return False
            if new_text is not None:
                r.text = new_text
            if new_time is not None:
                r.remind_at = new_time.isoformat()
            fired_was = r.fired
        if (new_text is not None or new_time is not None) and fired_was:
            with self._lock:
                self._reminders[r.id].fired = False  # 修改后允许再次触发
        self._save()
        return True

    def list(self, include_fired: bool = False) -> List[Reminder]:
        with self._lock:
            items = list(self._reminders.values())
        items.sort(key=lambda x: x.remind_at)
        if not include_fired:
            items = [i for i in items if not i.fired]
        return items

    # ---- 到期触发 ----
    def check_due(self, now: Optional[datetime] = None) -> List[Reminder]:
        """返回到点且未触发的提醒（并标记 fired）。不调用回调。"""
        now = now or self._now_fn()
        due, to_fire = [], []
        with self._lock:
            for r in self._reminders.values():
                if not r.fired and r.dt <= now:
                    to_fire.append(r)
            for r in to_fire:
                r.fired = True
        self._save()
        return to_fire

    def poll(self, now: Optional[datetime] = None) -> List[Reminder]:
        """到期检测并触发回调（语音/通知由 on_trigger 处理）。返回触发的提醒。"""
        due = self.check_due(now)
        for r in due:
            try:
                self._on_trigger(r)
            except Exception as e:  # 回调异常不阻塞轮询
                print(f"[reminder] 触发回调失败 {r.id}: {e}")
        return due

    # ---- 后台线程 ----
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="reminder-scheduler")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.poll()
            except Exception as e:
                print(f"[reminder] 轮询异常: {e}")
            self._stop_evt.wait(self._check_interval)

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2)

    # ---- 持久化 ----
    def _load(self) -> None:
        try:
            if not self._state_path.exists():
                return
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            with self._lock:
                for item in data:
                    self._reminders[item["id"]] = Reminder(**item)
        except Exception as e:
            print(f"[reminder] 加载持久化失败: {e}")

    def _save(self) -> None:
        try:
            with self._lock:
                data = [r.to_dict() for r in self._reminders.values()]
            self._state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[reminder] 保存失败: {e}")

    # ---- LLM 工具声明 ----
    def describe(self) -> dict:
        return {
            "name": "set_reminder",
            "description": "设置/取消/修改定时提醒。到点用语音和桌面通知提醒。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "cancel", "modify", "list"]},
                    "text": {"type": "string", "description": "提醒内容，含时间，如“明天上午9点喝水”"},
                    "reminder_id": {"type": "string", "description": "取消/修改时使用"},
                },
                "required": ["action"],
            },
        }


# 模块级单例
manager = ReminderManager()