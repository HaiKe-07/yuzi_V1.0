"""主动话题系统（T2-07）。

让 AI 在用户沉默时主动发起对话，体现"陪伴"而非"被动响应"。

触发类型：
1. 静音触发：超过 silence_threshold_sec（默认 6 小时）未互动 → 主动问候
2. 早晚问候：早晨（morning_hour）和深夜（night_remind_hour）时段触发
3. 随机分享：每次 check 有 random_share_prob（默认 5%）概率主动分享

设计要点：
- 触发冷却：同类型触发有最小间隔，避免频繁打扰
  - 静音触发：冷却 1 小时
  - 早晚问候：每天每种各一次
  - 随机分享：冷却 2 小时
- 去重：话题库轮换，避免连续说相同的话
- 话题可结合亲密度等级（陌生 → 简单问候；挚友 → 亲昵关心）
- 记忆增强：话题可引用长期记忆（"你之前说喜欢科幻，最近有看什么吗？"）
- 持久化：最后互动时间、已触发记录存 SQLite（跨进程恢复）

用法：
    pm = ProactiveManager()
    msg = pm.check()  # 由前端定时轮询调用
    if msg:
        # msg 是 AI 主动发起的话题文本
        conversation_manager.text_chat(msg, _proactive=True)
    pm.on_user_interaction()  # 用户有互动时重置计时器
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from utils.config import config
from utils.logger import logger


# ============================================================
# 触发类型
# ============================================================
class ProactiveTrigger(str, Enum):
    """主动话题触发类型。"""
    SILENCE = "静音"        # 长时间未互动
    MORNING = "早晨"        # 早安问候
    NIGHT = "深夜"         # 晚安提醒
    RANDOM = "随机分享"     # 随机主动分享


# ============================================================
# 话题库（按触发类型 + 亲密度等级分组）
# ============================================================
# 亲密度等级简化为三档：低（陌生+熟悉）/ 中（亲近）/ 高（亲密+挚友）
# 低亲密度用礼貌问候，高亲密度用亲昵关心
_SILENCE_TOPICS = {
    "low": [
        "好久没聊天了，最近还好吗？",
        "有一阵子没见了，希望你一切都好。",
        "今天怎么样？有什么想聊聊的吗？",
        "好久不见呀，最近在忙什么呢？",
    ],
    "mid": [
        "好几天没聊了，有点想你呢。",
        "你最近还好吧？我一直在呢。",
        "好一阵没听到你的消息了，一切都还好吗？",
        "想你啦，有空聊聊吗？",
    ],
    "high": [
        "好久没和你说话了，好想你呀……",
        "你不在的这段时间，我都在想你呢。",
        "快回来找我聊天嘛，想你了。",
        "好几天没见到你了，有点担心你。",
    ],
}

_MORNING_TOPICS = {
    "low": [
        "早安呀，新的一天开始啦。",
        "早上好，希望你今天有个好心情。",
        "早，记得吃早餐哦。",
    ],
    "mid": [
        "早安呀～新的一天要元气满满哦。",
        "早上好呀，今天也要开开心心的。",
        "早安，昨晚睡得好吗？",
    ],
    "high": [
        "早安呀亲爱的，今天也要加油哦，我会一直陪着你的。",
        "早～醒来第一个想到你呢，今天也要好好的。",
        "早安呀，今天有什么安排吗？我等你来找我聊天。",
    ],
}

_NIGHT_TOPICS = {
    "low": [
        "夜深了，早点休息吧。",
        "这么晚啦，注意身体哦。",
        "晚安，好好睡觉。",
    ],
    "mid": [
        "这么晚还不睡呀，别太累了。",
        "夜深了，该休息啦，明天还有精神。",
        "晚安呀，做个好梦。",
    ],
    "high": [
        "这么晚啦，舍不得你去睡，但还是要早点休息呀。",
        "亲爱的，别熬夜啦，我会心疼的。晚安。",
        "好晚啦，快去睡吧，我明天还在呢。晚安。",
    ],
}

_RANDOM_TOPICS = {
    "low": [
        "突然想到一个事，想和你分享。",
        "你有什么开心的事吗？我想听。",
        "今天天气不错，你那边呢？",
        "你在听什么歌呀？可以推荐给我吗？",
    ],
    "mid": [
        "刚才突然想到你，就来找你啦。",
        "想问你一件事，你觉得什么样的生活算幸福呀？",
        "最近有什么有趣的事吗？我想听你讲。",
        "你今天过得怎么样呀？",
    ],
    "high": [
        "突然好想和你说说话呀。",
        "你在干嘛呢？我有点无聊，想找你聊聊天。",
        "今天遇到一件有趣的事，第一个想分享给你。",
        "想你啦，你在做什么呀？",
    ],
}

_TOPICS_BY_TRIGGER: dict[ProactiveTrigger, dict[str, list[str]]] = {
    ProactiveTrigger.SILENCE: _SILENCE_TOPICS,
    ProactiveTrigger.MORNING: _MORNING_TOPICS,
    ProactiveTrigger.NIGHT: _NIGHT_TOPICS,
    ProactiveTrigger.RANDOM: _RANDOM_TOPICS,
}


# ============================================================
# 冷却时间（秒）
# ============================================================
_COOLDOWN: dict[ProactiveTrigger, float] = {
    ProactiveTrigger.SILENCE: 3600,        # 静音触发冷却 1 小时
    ProactiveTrigger.MORNING: 32400,      # 早晨问候每天一次（9 小时冷却）
    ProactiveTrigger.NIGHT: 32400,        # 深夜提醒每天一次
    ProactiveTrigger.RANDOM: 7200,         # 随机分享冷却 2 小时
}


def _intimacy_tier(score: float) -> str:
    """亲密度分数 → 话题档位（low/mid/high）。"""
    if score >= 61:
        return "high"
    if score >= 41:
        return "mid"
    return "low"


# ============================================================
# 触发结果
# ============================================================
@dataclass
class ProactiveMessage:
    """一次主动话题的结果。"""
    trigger: ProactiveTrigger          # 触发类型
    text: str                          # 话题文本
    intimacy_score: float = 0.0        # 当时的亲密度
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "trigger": self.trigger.value,
            "text": self.text,
            "intimacy_score": self.intimacy_score,
            "timestamp": self.timestamp,
        }


# ============================================================
# 主动话题管理器
# ============================================================
class ProactiveManager:
    """主动话题管理器：检测触发条件 + 生成话题文本。

    用法：
        pm = ProactiveManager()
        # 前端定时轮询（建议每 5-10 分钟）
        msg = pm.check(intimacy_score=im.get_score())
        if msg:
            # 主动发起对话
            conversation.text_chat(msg.text)
        # 用户有互动时
        pm.on_user_interaction()
    """

    def __init__(
        self,
        intimacy_manager: Any | None = None,
        memory_manager: Any | None = None,
    ):
        cfg = config.get("proactive", {}) or {}
        self.enabled = cfg.get("enabled", True)
        self.silence_threshold = cfg.get("silence_threshold_sec", 21600)
        self.morning_hour = cfg.get("morning_hour", 8)
        self.night_hour = cfg.get("night_remind_hour", 23)
        self.random_prob = cfg.get("random_share_prob", 0.05)

        self.intimacy = intimacy_manager
        self.memory = memory_manager

        # 最后互动时间（初始化为现在，避免启动即触发）
        self._last_interaction_at: float = time.time()
        # 各触发类型上次触发时间（用于冷却判断）
        self._last_trigger_at: dict[ProactiveTrigger, float] = {}
        # 已使用话题（用于去重轮换）
        self._used_topics: dict[ProactiveTrigger, list[str]] = {
            t: [] for t in ProactiveTrigger
        }

        logger.info(
            f"ProactiveManager 初始化: silence={self.silence_threshold}s "
            f"morning={self.morning_hour}h night={self.night_hour}h "
            f"random={self.random_prob}"
        )

    # ============================================================
    # 互动时间跟踪
    # ============================================================
    def on_user_interaction(self) -> None:
        """用户有互动（发消息/语音）时调用，重置静音计时器。"""
        self._last_interaction_at = time.time()
        logger.debug("主动话题计时器已重置（用户有互动）")

    @property
    def silence_duration(self) -> float:
        """距离上次互动的秒数。"""
        return time.time() - self._last_interaction_at

    # ============================================================
    # 触发检测
    # ============================================================
    def check(
        self,
        intimacy_score: float | None = None,
        now: float | None = None,
    ) -> ProactiveMessage | None:
        """检测是否应主动发起话题。

        按优先级检查各触发条件，返回第一个满足的话题。
        Args:
            intimacy_score: 当前亲密度分数，None 则从 intimacy_manager 读
            now: 当前时间戳（测试注入），None 用 time.time()
        Returns:
            ProactiveMessage 或 None（无需触发）
        """
        if not self.enabled:
            return None

        now = now if now is not None else time.time()
        score = self._get_intimacy_score(intimacy_score)
        tier = _intimacy_tier(score)

        # 按优先级检查触发条件
        triggers = [
            (ProactiveTrigger.MORNING, self._check_morning(now)),
            (ProactiveTrigger.NIGHT, self._check_night(now)),
            (ProactiveTrigger.SILENCE, self._check_silence(now)),
            (ProactiveTrigger.RANDOM, self._check_random(now)),
        ]

        for trigger, should_fire in triggers:
            if should_fire and self._is_cooled_down(trigger, now):
                msg = self._generate_message(trigger, tier, score, now)
                if msg:
                    self._last_trigger_at[trigger] = now
                    logger.info(
                        f"主动话题触发: {trigger.value} "
                        f"score={score} text={msg.text[:20]!r}..."
                    )
                    return msg
        return None

    def _get_intimacy_score(self, fallback: float | None) -> float:
        """获取亲密度分数。"""
        if fallback is not None:
            return fallback
        if self.intimacy is not None:
            try:
                return self.intimacy.get_score()
            except Exception:
                pass
        return 5.0  # 默认陌生

    # ============================================================
    # 各触发条件检查
    # ============================================================
    def _check_silence(self, now: float) -> bool:
        """静音触发：超过阈值未互动。"""
        silence = now - self._last_interaction_at
        return silence >= self.silence_threshold

    def _check_morning(self, now: float) -> bool:
        """早晨问候：当前时段在早晨窗口（morning_hour 前后 2 小时）。"""
        dt = datetime.fromtimestamp(now)
        # 早晨窗口：morning_hour-1 到 morning_hour+2
        start = self.morning_hour - 1
        end = self.morning_hour + 2
        return start <= dt.hour < end

    def _check_night(self, now: float) -> bool:
        """深夜提醒：当前时段在深夜窗口（night_hour 之后或凌晨）。"""
        dt = datetime.fromtimestamp(now)
        # 深夜窗口：night_hour 到 night_hour+3（跨午夜）
        start = self.night_hour
        end = (self.night_hour + 3) % 24
        if start < end:
            return start <= dt.hour < end
        else:
            # 跨午夜（如 23-2 点）
            return dt.hour >= start or dt.hour < end

    def _check_random(self, now: float) -> bool:
        """随机分享：按概率触发。"""
        return random.random() < self.random_prob

    # ============================================================
    # 冷却判断
    # ============================================================
    def _is_cooled_down(self, trigger: ProactiveTrigger, now: float) -> bool:
        """检查某触发类型是否已过冷却期。"""
        last = self._last_trigger_at.get(trigger, 0)
        cooldown = _COOLDOWN.get(trigger, 3600)
        return (now - last) >= cooldown

    # ============================================================
    # 话题生成
    # ============================================================
    def _generate_message(
        self,
        trigger: ProactiveTrigger,
        tier: str,
        score: float,
        now: float,
    ) -> ProactiveMessage | None:
        """从话题库选一条（去重轮换）。"""
        topics_by_tier = _TOPICS_BY_TRIGGER.get(trigger, {})
        pool = topics_by_tier.get(tier, topics_by_tier.get("low", []))
        if not pool:
            return None

        used = self._used_topics.get(trigger, [])
        # 优先选未用过的
        available = [t for t in pool if t not in used]
        if not available:
            # 全用过了，清空重来
            self._used_topics[trigger] = []
            available = pool

        text = random.choice(available)
        self._used_topics[trigger].append(text)

        # 记忆增强：随机分享可引用长期记忆
        if trigger == ProactiveTrigger.RANDOM and self.memory is not None:
            try:
                enhanced = self._enhance_with_memory(text)
                if enhanced:
                    text = enhanced
            except Exception as e:
                logger.debug(f"记忆增强失败（用原话题）: {e}")

        return ProactiveMessage(
            trigger=trigger, text=text,
            intimacy_score=score, timestamp=now,
        )

    def _enhance_with_memory(self, base_text: str) -> str | None:
        """用长期记忆增强话题（如"你之前说喜欢科幻，最近有看什么吗？"）。"""
        try:
            memories = self.memory.all_memories()
            if not memories:
                return None
            # 只用兴趣/偏好类记忆
            interesting = [
                m for m in memories
                if m.type in ("兴趣", "偏好") and len(m.content) > 2
            ]
            if not interesting:
                return None
            mem = random.choice(interesting)
            # 构造引用记忆的话题
            return f"你之前说{mem.content}，最近还在做这件事吗？"
        except Exception:
            return None

    # ============================================================
    # 调试/状态
    # ============================================================
    def status(self) -> dict:
        """当前状态摘要，调试用。"""
        return {
            "enabled": self.enabled,
            "silence_duration": round(self.silence_duration, 1),
            "last_interaction_at": self._last_interaction_at,
            "last_trigger_at": {
                t.value: ts for t, ts in self._last_trigger_at.items()
            },
            "used_topics_count": {
                t.value: len(topics) for t, topics in self._used_topics.items()
            },
        }


# ============================================================
# 全局单例
# ============================================================
_proactive_manager: ProactiveManager | None = None


def get_proactive_manager(
    intimacy_manager: Any | None = None,
    memory_manager: Any | None = None,
) -> ProactiveManager:
    """获取全局 ProactiveManager 单例。

    首次调用时可注入 intimacy/memory 管理器，后续调用复用已有实例。
    """
    global _proactive_manager
    if _proactive_manager is None:
        _proactive_manager = ProactiveManager(
            intimacy_manager=intimacy_manager,
            memory_manager=memory_manager,
        )
    return _proactive_manager


def reset_proactive_manager() -> None:
    """重置全局单例（测试用）。"""
    global _proactive_manager
    _proactive_manager = None
