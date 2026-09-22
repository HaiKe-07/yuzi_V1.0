"""T4-02 定时提醒测试。

覆盖：
1. parse_time：相对时间 / 今天明天后天 / 周X / 时段+X点（点/分/点半）/ 无时间
2. ReminderManager：新增、parse_and_add、取消、修改、列表、到期触发（check_due/poll）
3. 持久化读写
4. LLM schema、config 读取
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.reminder import ReminderManager, parse_time  # noqa: E402

NOW = datetime(2026, 9, 22, 10, 0, 0)  # 星期二


# ---------- parse_time ----------
def test_relative_minutes():
    at, cleaned = parse_time("提醒我5分钟后开会", NOW)
    assert at == NOW + timedelta(minutes=5)
    assert cleaned == "开会"
    print("  [✓] 相对时间（X分钟后）")


def test_relative_half_hour():
    at, cleaned = parse_time("半小时后吃药", NOW)
    assert at == NOW + timedelta(minutes=30)
    assert cleaned == "吃药"
    print("  [✓] 半小时后")


def test_tomorrow_morning():
    at, cleaned = parse_time("提醒我明天上午9点浇水", NOW)
    assert at == datetime(2026, 9, 23, 9, 0)
    assert cleaned == "浇水"
    print("  [✓] 明天上午X点")


def test_today_afternoon_past_rolls():
    # 今天 17 点：未来
    at, _ = parse_time("今天下午5点跑步", NOW)
    assert at == datetime(2026, 9, 22, 17, 0)
    # 只有时间且已过（今早8点），推到明天
    at2, _ = parse_time("提醒我早上8点喝水", NOW)
    assert at2 == datetime(2026, 9, 23, 8, 0)
    print("  [✓] 今天/顺延")


def test_hour_minute_thirty():
    at1, _ = parse_time("下午3点30分开会", NOW)
    assert at1 == datetime(2026, 9, 22, 15, 30)
    at2, _ = parse_time("晚上8点半看电视", NOW)
    assert at2 == datetime(2026, 9, 22, 20, 30)
    print("  [✓] X点Y分 / X点半")


def test_weekday():
    at, _ = parse_time("周一下午3点开会", NOW)  # 本周周一已过 → 下周一
    assert at.weekday() == 0
    assert at.hour == 15
    print("  [✓] 周X")


def test_no_time():
    at, _ = parse_time("提醒我把今天的文件发出去", NOW)
    assert at is None
    print("  [✓] 无时间 → None")


# ---------- ReminderManager ----------
def _manager():
    d = Path(tempfile.mkdtemp())
    return ReminderManager(now_fn=lambda: NOW, state_path=d / "rem.json")


def test_add_and_parse_and_add():
    m = _manager()
    ok, msg, r = m.parse_and_add("明天上午9点浇水")
    assert ok is True
    assert r.remind_at == datetime(2026, 9, 23, 9, 0).isoformat()
    assert "浇水" in msg
    # 无时间报错
    ok2, msg2, _ = m.parse_and_add("提醒我把报告发出去")
    assert ok2 is False
    print("  [✓] parse_and_add")


def test_list_sorted_and_unfired():
    m = _manager()
    m.parse_and_add("明天上午9点浇水")
    m.parse_and_add("今天下午5点跑步")
    items = m.list()
    assert len(items) == 2
    assert items[0].text == "跑步"  # 先触发(今天)排前
    assert all(not i.fired for i in items)
    print("  [✓] list 未触发且按时间排序")


def test_check_due_and_poll():
    m = _manager()
    triggered = []
    m._on_trigger = lambda r: triggered.append(r.text)
    m.parse_and_add("明天上午9点浇水")
    m.parse_and_add("5分钟后开会")
    due = m.check_due(NOW + timedelta(minutes=6))  # 只到期“开会”
    assert len(due) == 1 and due[0].text == "开会"
    assert m.list() == [] or all(False for i in m.list() if i.text == "开会")  # 已触发不再列出未触发
    # poll 触发回调
    triggered.clear()
    m.parse_and_add("2分钟后喝水")
    m.poll(NOW + timedelta(minutes=3))
    assert "喝水" in triggered
    print("  [✓] check_due / poll 到期触发")


def test_cancel_by_id_and_keyword():
    m = _manager()
    ok, _, r = m.parse_and_add("明天上午9点浇水")
    assert m.cancel(r.id) is True
    assert m.list() == []
    ok2, _, r2 = m.parse_and_add("明天上午10点开会")
    assert m.cancel("开会") is True  # 关键词
    assert m.list() == []
    assert m.cancel("不存在") is False
    print("  [✓] 取消（id / 关键词）")


def test_modify_text_and_time():
    m = _manager()
    ok, _, r = m.parse_and_add("明天上午9点浇水")
    assert m.modify(r.id, new_text="浇花") is True
    assert m.modify(r.id, new_time=datetime(2026, 9, 24, 8, 0)) is True
    updated = m.list()[0]
    assert updated.text == "浇花"
    assert updated.remind_at == datetime(2026, 9, 24, 8, 0).isoformat()
    print("  [✓] 修改（内容 / 时间）")


def test_persist():
    d = Path(tempfile.mkdtemp())
    sp = d / "rem.json"
    m = ReminderManager(now_fn=lambda: NOW, state_path=sp)
    m.parse_and_add("明天上午9点浇水")
    m2 = ReminderManager(now_fn=lambda: NOW, state_path=sp)
    assert len(m2.list()) == 1
    assert m2.list()[0].text == "浇水"
    # 文件确实写入
    data = json.loads(sp.read_text(encoding="utf-8"))
    assert len(data) == 1
    print("  [✓] 持久化写入/重载")


def test_describe_schema():
    m = ReminderManager(now_fn=lambda: NOW, state_path=Path(tempfile.mkdtemp()) / "r.json")
    d = m.describe()
    assert d["name"] == "set_reminder"
    assert "cancel" in d["parameters"]["properties"]["action"]["enum"]
    print("  [✓] LLM 工具 schema")


def test_config_read():
    from utils.config import config as cfg
    r = cfg.get("tools.reminder", {}) or {}
    assert isinstance(r.get("enabled"), bool)
    assert "check_interval" in r
    print("  [✓] config 读取 tools.reminder")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        test_relative_minutes,
        test_relative_half_hour,
        test_tomorrow_morning,
        test_today_afternoon_past_rolls,
        test_hour_minute_thirty,
        test_weekday,
        test_no_time,
        test_add_and_parse_and_add,
        test_list_sorted_and_unfired,
        test_check_due_and_poll,
        test_cancel_by_id_and_keyword,
        test_modify_text_and_time,
        test_persist,
        test_describe_schema,
        test_config_read,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  [✗] {t.__name__} 失败: {e}")
            traceback.print_exc()

    print(f"\n结果: {passed} 通过, {failed} 失败 / 共 {len(tests)}")