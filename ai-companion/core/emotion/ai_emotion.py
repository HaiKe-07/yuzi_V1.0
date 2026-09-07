"""AI 独立情绪系统（PAD 多维模型）。

设计原则：
- AI 有自己独立的情绪，不复制用户情绪
- 受用户情绪共鸣（empathy_weight 可调）
- 受对话内容影响（被夸会开心，被骂会难过）
- 情绪惯性：当前情绪越强烈，越抗拒被改变（状态机行为）
- 自然衰减：长时间无情绪刺激，回归中性基线
- 持久化：情绪状态可保存到 SQLite，跨会话延续

PAD 模型：
    pleasure: 愉悦度 -1~1
    arousal:  唤醒度 -1~1
    dominance: 支配度 -1~1

每个 AI 情绪状态由 PAD 向量 + 强度构成，映射回离散 EmotionType 便于展示。

状态机行为（情绪惯性）：
    当前 intensity 越高，update 时 effective_weight 越被衰减，
    形成"情绪粘性"——强烈情绪不会瞬间被切换，保证情绪连续性。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from utils.config import config
from utils.logger import logger

from .types import (
    EmotionState,
    EmotionType,
    PADVector,
    nearest_emotion,
    pad_of,
)


# 默认基线情绪：温柔陪伴者，平静偏愉悦
_BASELINE_PAD = PADVector(pleasure=0.3, arousal=-0.2, dominance=0.1)


@dataclass
class AIEmotionState:
    """AI 情绪状态。

    pad:       当前 PAD 向量
    intensity: 当前强度 0~1（0=完全平静，1=情绪极强烈）
    label:     对应的离散情绪类型（由 pad 推导）
    updated_at: 上次更新时间戳
    """
    pad: PADVector = field(default_factory=lambda: PADVector(**_BASELINE_PAD.__dict__))
    intensity: float = 0.2
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        self._refresh_label()

    def _refresh_label(self) -> None:
        self.label = nearest_emotion(self.pad)

    def to_dict(self) -> dict:
        return {
            "pad": self.pad.to_dict(),
            "intensity": round(self.intensity, 3),
            "label": self.label.value,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AIEmotionState":
        pad_d = d.get("pad", {})
        s = cls(
            pad=PADVector(
                pleasure=pad_d.get("pleasure", 0.3),
                arousal=pad_d.get("arousal", -0.2),
                dominance=pad_d.get("dominance", 0.1),
            ),
            intensity=d.get("intensity", 0.2),
            updated_at=d.get("updated_at", time.time()),
        )
        return s


class AIEmotionEngine:
    """AI 情绪引擎：更新、衰减、共鸣、持久化。"""

    def __init__(
        self,
        baseline: PADVector | None = None,
        empathy_weight: float | None = None,
        decay_rate: float | None = None,
        inertia_factor: float | None = None,
        persistence: bool = True,
    ):
        cfg = config.get("emotion", {}) or {}
        self.baseline = baseline or PADVector(
            pleasure=cfg.get("baseline_pleasure", 0.3),
            arousal=cfg.get("baseline_arousal", -0.2),
            dominance=cfg.get("baseline_dominance", 0.1),
        )
        # 共鸣权重：AI 情绪受用户情绪影响的程度（0~1）
        self.empathy_weight = empathy_weight if empathy_weight is not None \
            else cfg.get("empathy_weight", 0.4)
        # 衰减率：每秒向基线回归的比例
        self.decay_rate = decay_rate if decay_rate is not None \
            else cfg.get("decay_rate", 0.05)
        # 情绪惯性因子（0~1）：当前 intensity 越高，越抗拒被新输入改变
        # 0 = 无惯性（随时可被改写），1 = 完全粘住（无法改变，不实际）
        # 默认 0.5：强度满载时 effective_weight 衰减一半
        self.inertia_factor = inertia_factor if inertia_factor is not None \
            else cfg.get("inertia_factor", 0.5)

        self.persistence = persistence
        self.state = AIEmotionState(pad=PADVector(**self.baseline.__dict__))
        # DB 初始化标记（避免每次 save 都 init）
        self._db_ready = False

        # 从数据库恢复
        if self.persistence:
            self._load()

        logger.info(
            f"AIEmotionEngine 初始化 empathy={self.empathy_weight} "
            f"decay={self.decay_rate} inertia={self.inertia_factor} "
            f"state={self.state.label.value}"
        )

    # ============================================================
    # 情绪更新
    # ============================================================
    def _compute_inertia(self) -> float:
        """计算当前情绪惯性阻力（0~1）。

        当前 intensity 越高，越抗拒被新输入改变。
        inertia_factor 控制惯性强度上限：
            0   → 无惯性，随时可改写
            0.5 → 满载时 effective_weight 衰减 50%
            1.0 → 满载时 effective_weight 衰减 100%（完全粘住，不实际）
        """
        return min(0.95, self.state.intensity * self.inertia_factor)

    def update_from_user_emotion(self, user_emotion: EmotionState) -> None:
        """根据用户情绪共鸣更新 AI 情绪。

        共鸣机制（含情绪惯性）：
        1. 计算用户情绪的 PAD 向量
        2. 当前 AI 情绪越强烈，越抗拒被改变（惯性）
        3. 与当前 AI 情绪按 (empathy_weight * user_strength * (1-inertia)) 融合
        4. 强度按用户情绪强度比例调整
        """
        user_pad = pad_of(user_emotion.primary)
        # 强度归一化到 0~1
        user_strength = user_emotion.intensity / 5.0

        # 情绪惯性：当前越强烈，越抗拒被用户情绪拉走
        inertia = self._compute_inertia()
        # 共鸣：AI 情绪向用户情绪方向偏移（被惯性削弱）
        effective_weight = self.empathy_weight * user_strength * (1.0 - inertia)
        new_pad = self.state.pad.blend(user_pad, effective_weight)

        # 强度提升：用户越激动，AI 也越被带动（但受惯性约束，不会轻易降低）
        target_intensity = max(
            self.state.intensity * (1.0 - inertia * 0.3),  # 惯性保住一部分强度
            min(1.0, user_strength * self.empathy_weight * 2),
        )

        self.state.pad = new_pad
        self.state.intensity = target_intensity
        self.state.updated_at = time.time()
        self.state._refresh_label()

        logger.debug(
            f"AI 情绪共鸣 inertia={inertia:.2f} → {self.state.label.value} "
            f"intensity={self.state.intensity:.2f} "
            f"pad={self.state.pad.to_dict()}"
        )
        self._save()

    def update_from_content(
        self, content: str, polarity: float = 0.0,
    ) -> None:
        """根据对话内容直接影响 AI 情绪。

        Args:
            content:  对话文本（用户对 AI 说的话）
            polarity: 内容极性 -1~1（负=对 AI 不友好，正=对 AI 友好）
                      未传则用关键词规则推断
        """
        if polarity == 0.0:
            polarity = self._infer_polarity(content)
        if abs(polarity) < 0.1:
            return

        # 情绪惯性：当前越强烈，内容影响越被削弱
        inertia = self._compute_inertia()
        damp = 1.0 - inertia  # 阻尼系数

        # 极性直接影响 pleasure（被惯性削弱）
        self.state.pad.pleasure = max(-1.0, min(1.0,
            self.state.pad.pleasure + polarity * 0.3 * damp
        ))
        # 正面内容提升 arousal（开心），负面也提升（生气/难过）
        self.state.pad.arousal = max(-1.0, min(1.0,
            self.state.pad.arousal + abs(polarity) * 0.2 * damp
        ))
        # 被夸提升 dominance，被骂降低
        self.state.pad.dominance = max(-1.0, min(1.0,
            self.state.pad.dominance + polarity * 0.15 * damp
        ))
        self.state.intensity = min(1.0, self.state.intensity + abs(polarity) * 0.2 * damp)
        self.state.updated_at = time.time()
        self.state._refresh_label()

        logger.debug(
            f"AI 情绪内容影响 polarity={polarity:+.2f} inertia={inertia:.2f} → "
            f"{self.state.label.value}"
        )
        self._save()

    def decay(self, dt_seconds: float | None = None) -> None:
        """情绪自然衰减，向基线回归。

        Args:
            dt_seconds: 距上次更新的时间，None 自动计算
        """
        if dt_seconds is None:
            dt_seconds = time.time() - self.state.updated_at
        if dt_seconds < 1:
            return

        # 指数衰减：每秒向基线回归 decay_rate 比例
        decay = 1.0 - math.exp(-self.decay_rate * dt_seconds)
        self.state.pad.pleasure += (self.baseline.pleasure - self.state.pad.pleasure) * decay
        self.state.pad.arousal += (self.baseline.arousal - self.state.pad.arousal) * decay
        self.state.pad.dominance += (self.baseline.dominance - self.state.pad.dominance) * decay
        self.state.intensity = max(0.0, self.state.intensity * (1 - decay))
        self.state.updated_at = time.time()
        self.state._refresh_label()

    def get_state(self) -> AIEmotionState:
        """获取当前情绪状态（自动衰减后）。"""
        self.decay()
        return self.state

    def get_label(self) -> str:
        """获取当前情绪标签（中文）。"""
        return self.get_state().label.value

    def emotion_context(self) -> dict:
        """返回情绪上下文，供 ConversationManager 注入 LLM prompt。

        返回示例：
            {
                "label": "开心",
                "intensity": 0.62,
                "pad": {"pleasure": 0.5, "arousal": 0.3, "dominance": 0.1},
                "prompt_hint": "（你现在心情开心，强度中等，请用相应语气回复）"
            }
        """
        state = self.get_state()
        intensity_word = self._intensity_word(state.intensity)
        hint = f"（你现在的情绪是{state.label.value}{intensity_word}，请让回复语气与此一致）"
        return {
            "label": state.label.value,
            "intensity": round(state.intensity, 2),
            "pad": state.pad.to_dict(),
            "prompt_hint": hint,
        }

    def reset(self) -> None:
        """重置到基线情绪。"""
        self.state = AIEmotionState(pad=PADVector(**self.baseline.__dict__))
        self._save()
        logger.info("AI 情绪已重置到基线")

    # ============================================================
    # 内容极性推断（简化规则）
    # ============================================================
    _POSITIVE_WORDS = ["谢谢", "喜欢", "爱你", "想你", "厉害", "棒", "牛", "赞", "心疼你", "辛苦了"]
    _NEGATIVE_WORDS = ["讨厌", "烦", "滚", "闭嘴", "蠢", "笨", "没用", "嫌弃", "骂", "凶"]

    def _infer_polarity(self, text: str) -> float:
        """从文本推断对 AI 的友好度。"""
        if not text:
            return 0.0
        pos = sum(1 for w in self._POSITIVE_WORDS if w in text)
        neg = sum(1 for w in self._NEGATIVE_WORDS if w in text)
        if pos == neg:
            return 0.0
        return (pos - neg) / max(pos, neg)  # -1~1

    @staticmethod
    def _intensity_word(intensity: float) -> str:
        """0~1 强度转口语词，供 prompt_hint 使用。"""
        if intensity >= 0.8:
            return "极了"
        if intensity >= 0.6:
            return "得很"
        if intensity >= 0.4:
            return "挺"
        if intensity >= 0.2:
            return "有点"
        return ""

    # ============================================================
    # 持久化
    # ============================================================
    def _ensure_db(self) -> bool:
        """确保数据库已初始化（幂等）。

        Returns:
            True 表示 db 可用，False 表示不可用（应跳过持久化）
        """
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
            logger.debug(f"AI 情绪 DB 初始化失败（持久化关闭）: {e}")
            return False

    def _load(self) -> None:
        if not self._ensure_db():
            return
        try:
            from core.memory import db
            # 用 intimacy_state 表的 metadata_json 字段存 AI 情绪
            # （T2-03 亲密度系统会接管此表，此处复用）
            row = db.conn.execute(
                "SELECT metadata_json FROM intimacy_state WHERE id = 1"
            ).fetchone()
            if row and row["metadata_json"]:
                import json
                meta = json.loads(row["metadata_json"])
                emo_d = meta.get("ai_emotion")
                if emo_d:
                    self.state = AIEmotionState.from_dict(emo_d)
                    logger.info(f"从数据库恢复 AI 情绪: {self.state.label.value}")
        except Exception as e:
            logger.debug(f"加载 AI 情绪失败（首次启动正常）: {e}")

    def _save(self) -> None:
        if not self._ensure_db():
            return
        try:
            from core.memory import db
            import json
            # 读现有 metadata，合并 ai_emotion（不覆盖亲密度等其他字段）
            row = db.conn.execute(
                "SELECT metadata_json FROM intimacy_state WHERE id = 1"
            ).fetchone()
            meta = {}
            if row and row["metadata_json"]:
                meta = json.loads(row["metadata_json"])
            meta["ai_emotion"] = self.state.to_dict()
            # upsert：保留其他字段（score/stage 由 T2-03 亲密度系统管理）
            db.save_intimacy_state(
                score=meta.get("score", 5.0),
                stage=meta.get("stage", "陌生"),
                metadata=meta,
            )
        except Exception as e:
            logger.debug(f"保存 AI 情绪失败（不影响对话）: {e}")
