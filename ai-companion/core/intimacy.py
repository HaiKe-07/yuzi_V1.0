"""五级亲密度系统（T2-03）。

设计原则：
- 亲密度分数 0~100，划分五级（陌生/熟悉/亲近/亲密/挚友）
- 动态增减：深度交流+2、记住用户事项+3、安慰有效+2、伤害-5~-15、沉默-2、道歉+3、闲聊+0.5
- 特殊规则：
  1. 亲密度越高，提升越慢（边际递减，像真实关系）
  2. 伤害后恢复需要更多正向互动（"伤害恢复缓冲"）
  3. 下降有缓冲，不会一次掉很多（最低保护）
- 持久化：复用 db.intimacy_state 表
- 影响：等级决定称呼风格，分数调节 AI 情绪共鸣强度（T2-02 接入点）

等级与称呼映射：
    0-20   陌生    "你好" / 用户名字
    21-40  熟悉    名字 / "你"
    41-60  亲近    昵称 / 小名
    61-80  亲密    专属昵称 / 亲昵称呼
    81-100 挚友    最亲昵称呼，自然流露
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from utils.config import config
from utils.logger import logger


class IntimacyLevel(str, Enum):
    """亲密度五级。"""
    STRANGER = "陌生"      # 0-20
    FAMILIAR = "熟悉"      # 21-40
    CLOSE = "亲近"        # 41-60
    INTIMATE = "亲密"      # 61-80
    SOULMATE = "挚友"      # 81-100


# 等级分数边界（upper 闭区间）
_LEVEL_BOUNDS = [
    (20.0,  IntimacyLevel.STRANGER),
    (40.0,  IntimacyLevel.FAMILIAR),
    (60.0,  IntimacyLevel.CLOSE),
    (80.0,  IntimacyLevel.INTIMATE),
    (100.0, IntimacyLevel.SOULMATE),
]

# 等级 → 称呼风格提示（供 system prompt 用，T2-04 由 LLM 自然生成具体称呼）
_LEVEL_ADDRESS_HINT = {
    IntimacyLevel.STRANGER: "用礼貌而稍带距离的称呼，如'你好'或对方名字",
    IntimacyLevel.FAMILIAR: "用名字或'你'，自然友好",
    IntimacyLevel.CLOSE:   "可用昵称或小名，语气温和亲近",
    IntimacyLevel.INTIMATE: "用专属亲昵称呼，像好朋友或家人",
    IntimacyLevel.SOULMATE: "用最亲昵的称呼，自然流露关心，无需拘谨",
}


def level_of(score: float) -> IntimacyLevel:
    """分数 → 等级。"""
    s = max(0.0, min(100.0, score))
    for upper, level in _LEVEL_BOUNDS:
        if s <= upper:
            return level
    return IntimacyLevel.SOULMATE  # 兜底


def address_hint(level: IntimacyLevel) -> str:
    """等级 → 称呼风格提示。"""
    return _LEVEL_ADDRESS_HINT.get(level, "")


@dataclass
class IntimacyState:
    """亲密度状态快照。"""
    score: float = 5.0
    updated_at: float = field(default_factory=time.time)
    # 伤害恢复缓冲：被伤害后短期内正向增益被削弱（0~1，1=完全恢复）
    # 每次伤害设为 0.3，随正向互动缓慢回升到 1.0
    recovery: float = 1.0
    # 上次互动时间戳（用于沉默扣分）
    last_interaction_at: float = field(default_factory=time.time)
    # 统计：当前会话连续深度交流轮数（≥5 触发 deep_talk_gain）
    deep_talk_streak: int = 0

    @property
    def level(self) -> IntimacyLevel:
        return level_of(self.score)

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 2),
            "level": self.level.value,
            "recovery": round(self.recovery, 3),
            "updated_at": self.updated_at,
            "last_interaction_at": self.last_interaction_at,
            "deep_talk_streak": self.deep_talk_streak,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IntimacyState":
        return cls(
            score=d.get("score", 5.0),
            recovery=d.get("recovery", 1.0),
            updated_at=d.get("updated_at", time.time()),
            last_interaction_at=d.get("last_interaction_at", time.time()),
            deep_talk_streak=d.get("deep_talk_streak", 0),
        )


class IntimacyManager:
    """亲密度管理器：增减、等级、持久化。

    用法：
        im = IntimacyManager()
        im.on_deep_talk()          # 深度交流
        im.on_user_fact_mentioned() # 记住用户事项
        im.on_hurt(severity=0.8)   # 用户说伤害性话语
        im.on_silence(days=3)     # 连续 3 天未互动
        im.on_apology()           # 用户真诚道歉
        im.on_daily_chat()        # 日常闲聊
        im.save()                 # 持久化
    """

    def __init__(self, persistence: bool = True):
        cfg = config.get("intimacy", {}) or {}
        self.initial_score = cfg.get("initial_score", 5.0)
        self.max_score = cfg.get("max_score", 100.0)
        # 增减配置（与任务清单一致）
        self.deep_talk_gain = cfg.get("deep_talk_gain", 2.0)
        self.mention_fact_gain = cfg.get("mention_user_fact_gain", 3.0)
        self.comfort_gain = cfg.get("comfort_gain", 2.0)
        self.hurt_loss_min = cfg.get("hurt_loss_min", 5.0)
        self.hurt_loss_max = cfg.get("hurt_loss_max", 15.0)
        self.silence_days_loss = cfg.get("silence_days_loss", 2.0)
        self.apology_gain = cfg.get("apology_gain", 3.0)
        self.daily_chat_gain = cfg.get("daily_chat_gain", 0.5)

        self.persistence = persistence
        self.state = IntimacyState(score=self.initial_score)
        self._db_ready = False
        if self.persistence:
            self._load()

        logger.info(
            f"IntimacyManager 初始化 score={self.state.score} "
            f"level={self.state.level.value} recovery={self.state.recovery}"
        )

    # ============================================================
    # 内部工具
    # ============================================================
    def _ensure_db(self) -> bool:
        if not self.persistence:
            return False
        if self._db_ready:
            return True
        try:
            from core.memory import db
            db.init()
            self._db_ready = True
            return True
        except Exception as e:
            logger.debug(f"亲密度 DB 初始化失败: {e}")
            return False

    def _diminishing_factor(self) -> float:
        """边际递减因子：亲密度越高，正向增益越小。

        score 0   → 1.0（全额）
        score 50  → 0.5（半额）
        score 100 → 0.1（几乎不涨，越接近顶点越难）
        """
        # 1 - score/100，再平方让高分区更平
        return max(0.1, 1.0 - (self.state.score / self.max_score) ** 1.5)

    def _apply_gain(self, base: float, reason: str) -> float:
        """应用正向增益（边际递减 + 伤害恢复缓冲）。"""
        dim = self._diminishing_factor()
        # 伤害恢复缓冲：recovery<1 时正向增益被削弱
        effective = base * dim * self.state.recovery
        self.state.score = min(self.max_score, self.state.score + effective)
        # 正向互动缓慢恢复 recovery（每次 +0.1，上限 1.0）
        self.state.recovery = min(1.0, self.state.recovery + 0.1)
        self._touch()
        logger.debug(
            f"亲密度+{reason} base={base} dim={dim:.2f} "
            f"recovery={self.state.recovery:.2f} → +{effective:.2f} "
            f"score={self.state.score:.2f}"
        )
        return effective

    def _apply_loss(self, base: float, reason: str, floor: float = 0.0) -> float:
        """应用负向扣分（有最低保护，不会一次掉很多）。"""
        # 下降缓冲：扣分幅度限制为基础的 80%（不会一次掉太多）
        effective = base * 0.8
        self.state.score = max(0.0, self.state.score - effective)
        self._touch()
        logger.debug(
            f"亲密度-{reason} base={base} → -{effective:.2f} "
            f"score={self.state.score:.2f}"
        )
        return effective

    def _touch(self) -> None:
        """更新时间戳并持久化。"""
        self.state.updated_at = time.time()
        self.state.last_interaction_at = time.time()
        self._save()

    # ============================================================
    # 增减事件（对应任务清单规则）
    # ============================================================
    def on_deep_talk(self) -> float:
        """深度交流（≥5 轮有意义对话）→ +2。"""
        return self._apply_gain(self.deep_talk_gain, "深度交流")

    def on_user_fact_mentioned(self) -> float:
        """记住并主动提及用户的重要事情 → +3。"""
        return self._apply_gain(self.mention_fact_gain, "提及用户事项")

    def on_comfort_effective(self) -> float:
        """安慰用户后用户情绪好转 → +2。"""
        return self._apply_gain(self.comfort_gain, "有效安慰")

    def on_hurt(self, severity: float = 0.5) -> float:
        """用户说伤害性话语 → -5~-15。

        Args:
            severity: 伤害严重度 0~1（0=轻微，1=极严重）
                      映射到 [hurt_loss_min, hurt_loss_max]
        """
        severity = max(0.0, min(1.0, severity))
        loss = self.hurt_loss_min + (self.hurt_loss_max - self.hurt_loss_min) * severity
        applied = self._apply_loss(loss, "伤害话语")
        # 伤害触发恢复缓冲：recovery 降到 0.3，需要后续正向互动回升
        self.state.recovery = max(0.1, 0.3)
        self._save()
        logger.info(
            f"用户伤害性话语 severity={severity:.2f} → -{applied:.2f} "
            f"recovery 降至 {self.state.recovery:.2f}"
        )
        return applied

    def on_silence(self, days: float) -> float:
        """连续 N 天未互动 → -2 * (days/3)。

        每 3 天扣 silence_days_loss，线性累积。
        """
        if days < 1:
            return 0.0
        loss = self.silence_days_loss * (days / 3.0)
        applied = self._apply_loss(loss, f"沉默{days:.1f}天")
        return applied

    def on_apology(self) -> float:
        """用户真诚道歉 → +3（恢复部分）。

        道歉直接提升 recovery 到 0.7（比常规正向互动回升快），
        同时给予 apology_gain 增益。
        """
        # 道歉快速恢复缓冲
        self.state.recovery = max(self.state.recovery, 0.7)
        return self._apply_gain(self.apology_gain, "真诚道歉")

    def on_daily_chat(self) -> float:
        """日常闲聊 → +0.5。"""
        return self._apply_gain(self.daily_chat_gain, "日常闲聊")

    def on_deep_talk_streak(self, turn_count: int) -> float:
        """连续深度对话轮数累积，≥5 触发一次 deep_talk 增益。

        Args:
            turn_count: 本轮新增的有意义对话轮数（通常为 1）
        Returns:
            本次触发的增益（0 表示未触发）
        """
        self.state.deep_talk_streak += turn_count
        if self.state.deep_talk_streak >= 5:
            self.state.deep_talk_streak = 0
            return self.on_deep_talk()
        return 0.0

    # ============================================================
    # 查询
    # ============================================================
    def get_score(self) -> float:
        return self.state.score

    def get_level(self) -> IntimacyLevel:
        return self.state.level

    def get_address_hint(self) -> str:
        """获取当前等级的称呼风格提示（供 system prompt 用）。"""
        return address_hint(self.state.level)

    def context(self) -> dict:
        """返回亲密度上下文，供 ConversationManager 注入 prompt。"""
        return {
            "score": round(self.state.score, 1),
            "level": self.state.level.value,
            "address_hint": self.get_address_hint(),
            "recovery": round(self.state.recovery, 2),
            "prompt_hint": (
                f"（你与用户的亲密度为 {self.state.score:.0f}/100，"
                f"关系阶段：{self.state.level.value}。"
                f"{self.get_address_hint()}）"
            ),
        }

    def reset(self) -> None:
        """重置到初始状态。"""
        self.state = IntimacyState(score=self.initial_score)
        self._save()
        logger.info("亲密度已重置到初始值")

    # ============================================================
    # 持久化
    # ============================================================
    def _load(self) -> None:
        if not self._ensure_db():
            return
        try:
            from core.memory import db
            row = db.conn.execute(
                "SELECT score, stage, metadata_json FROM intimacy_state WHERE id = 1"
            ).fetchone()
            if row:
                self.state.score = (
                    float(row["score"]) if row["score"] is not None
                    else self.initial_score
                )
                import json
                meta = {}
                if row["metadata_json"]:
                    meta = json.loads(row["metadata_json"])
                # 从 metadata 恢复扩展字段
                emo_state = meta.get("ai_emotion", {})
                # 注意：亲密度只读自己的字段，ai_emotion 由 AIEmotionEngine 管理
                inti_meta = meta.get("intimacy", {})
                self.state.recovery = inti_meta.get("recovery", 1.0)
                self.state.last_interaction_at = inti_meta.get(
                    "last_interaction_at", time.time()
                )
                self.state.deep_talk_streak = inti_meta.get("deep_talk_streak", 0)
                logger.info(
                    f"从数据库恢复亲密度: score={self.state.score} "
                    f"level={self.state.level.value}"
                )
        except Exception as e:
            logger.debug(f"加载亲密度失败（首次启动正常）: {e}")

    def _save(self) -> None:
        if not self._ensure_db():
            return
        try:
            from core.memory import db
            import json
            # 读现有 metadata，合并 intimacy 字段（不覆盖 ai_emotion）
            row = db.conn.execute(
                "SELECT metadata_json FROM intimacy_state WHERE id = 1"
            ).fetchone()
            meta = {}
            if row and row["metadata_json"]:
                meta = json.loads(row["metadata_json"])
            # 写入亲密度扩展字段
            meta["intimacy"] = {
                "recovery": self.state.recovery,
                "last_interaction_at": self.state.last_interaction_at,
                "deep_talk_streak": self.state.deep_talk_streak,
            }
            db.save_intimacy_state(
                score=self.state.score,
                stage=self.state.level.value,
                metadata=meta,
            )
        except Exception as e:
            logger.debug(f"保存亲密度失败（不影响对话）: {e}")


# 全局单例（懒加载）
_singleton: IntimacyManager | None = None


def get_intimacy_manager() -> IntimacyManager:
    """获取全局亲密度管理器单例。"""
    global _singleton
    if _singleton is None:
        _singleton = IntimacyManager()
    return _singleton
