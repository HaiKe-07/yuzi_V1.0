"""AI 独立情绪系统（PAD + 关系维度扩展模型）。

设计原则：
- AI 有自己独立的情绪，不复制用户情绪
- 受用户情绪共鸣（empathy_weight 可调，亲密度调节）
- 受对话内容影响（被夸会开心，被骂会难过）
- 受时间/日程影响（早晨活跃、深夜疲惫）
- 受沉默时长影响（长时间无互动 → loneliness 上升）
- 情绪惯性：当前情绪越强烈，越抗拒被改变（状态机行为）
- 自然衰减：长时间无情绪刺激，回归中性基线
- 持久化：情绪状态可保存到 SQLite，跨会话延续

情绪维度：
    === 基础 PAD（通用情感）===
    pleasure:  愉悦度 -1~1
    arousal:   唤醒度 -1~1（平静→激动）
    dominance: 支配度 -1~1（受控→主导）

    === 关系维度（陪伴场景扩展，T2-02）===
    affection:    对用户的亲近感 0~1（关系导向，单向累积）
    loneliness:   孤独感 0~1（沉默累积，被互动稀释）
    concern:      对用户的担忧 0~1（用户难过时上升）
    playfulness:  调皮/趣味性 0~1（轻松氛围时上升）

每个 AI 情绪状态由 PAD 向量 + 关系维度 + 强度构成，
PAD 映射回离散 EmotionType 便于展示，关系维度影响 prompt 语气。

状态机行为（情绪惯性）：
    当前 intensity 越高，update 时 effective_weight 越被衰减，
    形成"情绪粘性"——强烈情绪不会瞬间被切换，保证情绪连续性。

亲密度调节（T2-03 接入）：
    intimacy_score 越高，empathy_weight 实际生效比例越大，
    形成"越亲密越能被用户情绪带动"的真实关系感。
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

# 关系维度基线：温柔陪伴者对用户有基础亲近感，低孤独/担忧/调皮
_BASELINE_AFFECTION = 0.3
_BASELINE_LONELINESS = 0.1
_BASELINE_CONCERN = 0.1
_BASELINE_PLAYFULNESS = 0.2

# 关系维度取值范围（0~1，与 PAD 的 -1~1 不同）
_REL_MIN = 0.0
_REL_MAX = 1.0


def _clamp_rel(v: float) -> float:
    """关系维度值裁剪到 [0, 1]。"""
    return max(_REL_MIN, min(_REL_MAX, v))


@dataclass
class AIEmotionState:
    """AI 情绪状态。

    === 基础 PAD ===
    pad:       当前 PAD 向量（pleasure/arousal/dominance，-1~1）
    intensity: 当前强度 0~1（0=完全平静，1=情绪极强烈）
    label:     对应的离散情绪类型（由 pad 推导）

    === 关系维度（T2-02 扩展，0~1）===
    affection:    对用户的亲近感
    loneliness:   孤独感
    concern:      对用户的担忧
    playfulness:  调皮/趣味性

    updated_at: 上次更新时间戳
    last_interaction_at: 上次与用户互动的时间戳（用于沉默累积）
    """
    pad: PADVector = field(default_factory=lambda: PADVector(**_BASELINE_PAD.__dict__))
    intensity: float = 0.2
    # 关系维度（T2-02）
    affection: float = _BASELINE_AFFECTION
    loneliness: float = _BASELINE_LONELINESS
    concern: float = _BASELINE_CONCERN
    playfulness: float = _BASELINE_PLAYFULNESS
    updated_at: float = field(default_factory=time.time)
    last_interaction_at: float = field(default_factory=time.time)

    def __post_init__(self):
        self._refresh_label()

    def _refresh_label(self) -> None:
        self.label = nearest_emotion(self.pad)

    def to_dict(self) -> dict:
        return {
            "pad": self.pad.to_dict(),
            "intensity": round(self.intensity, 3),
            "label": self.label.value,
            "affection": round(self.affection, 3),
            "loneliness": round(self.loneliness, 3),
            "concern": round(self.concern, 3),
            "playfulness": round(self.playfulness, 3),
            "updated_at": self.updated_at,
            "last_interaction_at": self.last_interaction_at,
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
            affection=d.get("affection", _BASELINE_AFFECTION),
            loneliness=d.get("loneliness", _BASELINE_LONELINESS),
            concern=d.get("concern", _BASELINE_CONCERN),
            playfulness=d.get("playfulness", _BASELINE_PLAYFULNESS),
            updated_at=d.get("updated_at", time.time()),
            last_interaction_at=d.get("last_interaction_at", time.time()),
        )
        return s


class AIEmotionEngine:
    """AI 情绪引擎：更新、衰减、共鸣、持久化。

    支持 T2-02 扩展：
    - 关系维度（affection/loneliness/concern/playfulness）
    - 时间/日程影响（update_from_time_context）
    - 沉默时长影响（update_from_silence）
    - 亲密度调节（set_intimacy_score）
    """

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
        # 关系维度基线（T2-02）
        self.baseline_affection = cfg.get("baseline_affection", _BASELINE_AFFECTION)
        self.baseline_loneliness = cfg.get("baseline_loneliness", _BASELINE_LONELINESS)
        self.baseline_concern = cfg.get("baseline_concern", _BASELINE_CONCERN)
        self.baseline_playfulness = cfg.get("baseline_playfulness", _BASELINE_PLAYFULNESS)

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

        # 亲密度调节（T2-03 接入，T2-02 预留接口）
        # intimacy_score 0~100，影响 empathy_weight 实际生效比例
        # 越亲密 → empathy 实际越强（最高 1.5 倍）；越陌生 → 越弱（最低 0.5 倍）
        self._intimacy_score: float = cfg.get("intimacy_score", 5.0)
        # 沉默累积速率：每秒 loneliness 上升量（默认 6 小时累积到 0.5）
        self.silence_loneliness_rate = cfg.get("silence_loneliness_rate", 0.5 / 21600.0)

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
            f"intimacy={self._intimacy_score} state={self.state.label.value}"
        )

    # ============================================================
    # 亲密度调节（T2-03 接入点，T2-02 预留）
    # ============================================================
    def set_intimacy_score(self, score: float) -> None:
        """设置亲密度分数（0~100），调节共鸣强度。

        亲密度越高，用户情绪对 AI 影响越大（真实关系感）。
        """
        self._intimacy_score = max(0.0, min(100.0, score))

    def _empathy_multiplier(self) -> float:
        """根据亲密度计算共鸣倍数（0.5~1.5）。

        intimacy 0   → 0.5（陌生，不易被带动）
        intimacy 50  → 1.0（熟悉，正常共鸣）
        intimacy 100 → 1.5（挚友，强烈共鸣）
        """
        # 线性映射 0~100 → 0.5~1.5
        return 0.5 + (self._intimacy_score / 100.0)

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
        """根据用户情绪共鸣更新 AI 情绪（含亲密度调节 + 关系维度）。

        共鸣机制（含情绪惯性 + 亲密度调节）：
        1. 计算用户情绪的 PAD 向量
        2. 亲密度调节：越亲密 empathy 生效倍数越大（0.5~1.5）
        3. 当前 AI 情绪越强烈，越抗拒被改变（惯性）
        4. 与当前 AI 情绪按 (empathy * intimacy_mult * user_strength * (1-inertia)) 融合
        5. 关系维度联动：
           - 用户难过 → concern 上升
           - 用户亲昵 → affection 上升
           - 用户开心 → playfulness 小幅上升
           - 任何互动 → loneliness 下降（被陪伴稀释）
        """
        user_pad = pad_of(user_emotion.primary)
        # 强度归一化到 0~1
        user_strength = user_emotion.intensity / 5.0

        # 亲密度调节倍数（0.5~1.5）
        intimacy_mult = self._empathy_multiplier()
        # 情绪惯性：当前越强烈，越抗拒被用户情绪拉走
        inertia = self._compute_inertia()
        # 共鸣：AI 情绪向用户情绪方向偏移（被惯性与亲密度共同调节）
        effective_weight = (
            self.empathy_weight * intimacy_mult * user_strength * (1.0 - inertia)
        )
        # 防止 weight 过大导致发散
        effective_weight = min(0.95, effective_weight)
        new_pad = self.state.pad.blend(user_pad, effective_weight)

        # 强度提升：用户越激动，AI 也越被带动（但受惯性约束，不会轻易降低）
        target_intensity = max(
            self.state.intensity * (1.0 - inertia * 0.3),  # 惯性保住一部分强度
            min(1.0, user_strength * self.empathy_weight * intimacy_mult * 2),
        )

        self.state.pad = new_pad
        self.state.intensity = target_intensity

        # === 关系维度联动（T2-02）===
        damp = 1.0 - inertia  # 关系维度也受惯性削弱
        # 用户难过 → concern 上升（担忧用户）
        if user_emotion.primary in (
            EmotionType.SAD, EmotionType.ANXIOUS, EmotionType.LONELY,
            EmotionType.DISAPPOINTED, EmotionType.TIRED,
        ):
            self.state.concern = _clamp_rel(
                self.state.concern + user_strength * 0.2 * damp
            )
        # 用户亲昵 → affection 上升
        if user_emotion.primary in (
            EmotionType.AFFECTIONATE, EmotionType.GRATEFUL, EmotionType.MOVED,
        ):
            self.state.affection = _clamp_rel(
                self.state.affection + user_strength * 0.15 * damp
            )
        # 用户开心/兴奋 → playfulness 小幅上升（轻松氛围）
        if user_emotion.primary in (
            EmotionType.HAPPY, EmotionType.EXCITED, EmotionType.INSPIRED,
        ):
            self.state.playfulness = _clamp_rel(
                self.state.playfulness + user_strength * 0.1 * damp
            )
        # 任何用户互动 → loneliness 下降（陪伴稀释孤独）
        self.state.loneliness = _clamp_rel(
            self.state.loneliness - user_strength * 0.3 * damp
        )

        self.state.updated_at = time.time()
        self.state.last_interaction_at = time.time()
        self.state._refresh_label()

        logger.debug(
            f"AI 情绪共鸣 intimacy_mult={intimacy_mult:.2f} inertia={inertia:.2f} "
            f"→ {self.state.label.value} intensity={self.state.intensity:.2f} "
            f"aff={self.state.affection:.2f} lone={self.state.loneliness:.2f} "
            f"con={self.state.concern:.2f} play={self.state.playfulness:.2f}"
        )
        self._save()

    def update_from_content(
        self, content: str, polarity: float = 0.0,
    ) -> None:
        """根据对话内容直接影响 AI 情绪（含关系维度联动）。

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

        # === 关系维度联动（T2-02）===
        # 被夸 → affection 上升；被骂 → affection 下降
        self.state.affection = _clamp_rel(
            self.state.affection + polarity * 0.1 * damp
        )
        # 正面内容 → playfulness 小幅上升；负面内容 → playfulness 下降
        self.state.playfulness = _clamp_rel(
            self.state.playfulness + polarity * 0.05 * damp
        )
        # 任何内容互动 → loneliness 下降（被陪伴稀释）
        self.state.loneliness = _clamp_rel(
            self.state.loneliness - 0.1 * damp
        )

        self.state.updated_at = time.time()
        self.state.last_interaction_at = time.time()
        self.state._refresh_label()

        logger.debug(
            f"AI 情绪内容影响 polarity={polarity:+.2f} inertia={inertia:.2f} → "
            f"{self.state.label.value} aff={self.state.affection:.2f}"
        )
        self._save()

    def decay(self, dt_seconds: float | None = None) -> None:
        """情绪自然衰减，向基线回归（含 PAD + 关系维度）。

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

        # 关系维度也衰减向基线（T2-02）
        # affection/concern/playfulness 缓慢回归基线（关系不像情绪那么快消散）
        rel_decay = decay * 0.5  # 关系维度衰减更慢
        self.state.affection += (self.baseline_affection - self.state.affection) * rel_decay
        self.state.concern += (self.baseline_concern - self.state.concern) * rel_decay
        self.state.playfulness += (self.baseline_playfulness - self.state.playfulness) * rel_decay
        # loneliness 不衰减向基线，而是由沉默累积单独管理（见 update_from_silence）
        # 这里只做温和回归，避免无互动时 loneliness 无限累积
        self.state.loneliness += (self.baseline_loneliness - self.state.loneliness) * rel_decay * 0.3

        self.state.updated_at = time.time()
        self.state._refresh_label()

    # ============================================================
    # 时间/日程影响（T2-02）
    # ============================================================
    # 时段 → (pleasure_delta, arousal_delta) 基线偏移
    # 早晨活跃偏愉悦，下午平稳，夜晚疲惫偏低唤醒
    _TIME_OF_DAY_PROFILE = {
        "morning":   (0.1, 0.2),   # 6-11: 活跃、愉悦
        "noon":      (0.05, 0.0), # 11-14: 平稳
        "afternoon": (0.0, -0.1),  # 14-18: 略疲惫
        "evening":   (0.05, -0.2), # 18-22: 放松
        "night":     (-0.1, -0.4), # 22-6: 疲惫、低唤醒
    }

    @staticmethod
    def _classify_time(hour: int) -> str:
        """小时（0-23）→ 时段分类。"""
        if 6 <= hour < 11:
            return "morning"
        if 11 <= hour < 14:
            return "noon"
        if 14 <= hour < 18:
            return "afternoon"
        if 18 <= hour < 22:
            return "evening"
        return "night"

    def update_from_time_context(self, hour: int | None = None) -> None:
        """根据当前时间调整 AI 基线情绪（时段影响）。

        不同时段 AI 的"状态基线"会偏移：
        - 早晨：活跃、愉悦（arousal + pleasure 上升）
        - 深夜：疲惫、低唤醒（arousal 大幅下降）

        Args:
            hour: 0-23 小时，None 用当前系统时间
        """
        if hour is None:
            hour = time.localtime().tm_hour
        period = self._classify_time(hour)
        p_delta, a_delta = self._TIME_OF_DAY_PROFILE[period]

        # 时段影响作为"温和偏移"，直接加到当前 pad（不衰减、不累积）
        # 用较小幅度避免覆盖用户情绪带来的变化
        self.state.pad.pleasure = max(-1.0, min(1.0,
            self.state.pad.pleasure + p_delta * 0.3
        ))
        self.state.pad.arousal = max(-1.0, min(1.0,
            self.state.pad.arousal + a_delta * 0.3
        ))
        # 深夜降低 playfulness（疲惫时不想调皮）
        if period == "night":
            self.state.playfulness = _clamp_rel(
                self.state.playfulness - 0.05
            )
        # 早晨小幅提升 playfulness
        elif period == "morning":
            self.state.playfulness = _clamp_rel(
                self.state.playfulness + 0.05
            )

        self.state.updated_at = time.time()
        self.state._refresh_label()
        logger.debug(f"AI 情绪时段影响 period={period} hour={hour}")

    # ============================================================
    # 沉默时长影响（T2-02）
    # ============================================================
    def update_from_silence(self, silence_seconds: float | None = None) -> None:
        """根据沉默时长累积 loneliness（无互动越久越孤独）。

        沉默累积规则：
        - 按 silence_loneliness_rate 每秒累积 loneliness
        - 长时间沉默（>1 小时）也会小幅降低 affection（被冷落感）
        - 不会无限累积，loneliness 上限 1.0

        Args:
            silence_seconds: 沉默秒数，None 用 last_interaction_at 自动计算
        """
        if silence_seconds is None:
            silence_seconds = time.time() - self.state.last_interaction_at
        if silence_seconds < 60:  # 1 分钟内不算沉默
            return

        # loneliness 线性累积
        gained = silence_seconds * self.silence_loneliness_rate
        self.state.loneliness = _clamp_rel(self.state.loneliness + gained)

        # 长时间沉默（>1 小时）小幅降低 affection
        if silence_seconds > 3600:
            affection_loss = (silence_seconds / 3600.0) * 0.02  # 每小时降 0.02
            self.state.affection = _clamp_rel(
                self.state.affection - affection_loss
            )

        # 沉默累积的 loneliness 会轻微拉低 pleasure（孤独让人不快乐）
        if self.state.loneliness > 0.5:
            self.state.pad.pleasure = max(-1.0, min(1.0,
                self.state.pad.pleasure - (self.state.loneliness - 0.5) * 0.1
            ))

        self.state.updated_at = time.time()
        self.state._refresh_label()
        logger.debug(
            f"AI 情绪沉默累积 silence={silence_seconds:.0f}s "
            f"lone={self.state.loneliness:.2f}"
        )

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
            "affection": 0.4,
            "loneliness": 0.1,
            "concern": 0.2,
            "playfulness": 0.3,
            "prompt_hint": "（你现在心情开心，强度中等，对用户亲近，请用相应语气回复）"
        }
        """
        state = self.get_state()
        intensity_word = self._intensity_word(state.intensity)
        # 关系维度描述
        rel_parts = []
        if state.affection > 0.5:
            rel_parts.append("对用户亲近")
        elif state.affection < 0.2:
            rel_parts.append("对用户疏远")
        if state.loneliness > 0.5:
            rel_parts.append("感到孤独")
        if state.concern > 0.5:
            rel_parts.append("担心用户")
        if state.playfulness > 0.5:
            rel_parts.append("想调皮一下")
        rel_desc = "，".join(rel_parts)
        hint = (
            f"（你现在的情绪是{state.label.value}{intensity_word}"
            + (f"，{rel_desc}" if rel_desc else "")
            + "，请让回复语气与此一致）"
        )
        return {
            "label": state.label.value,
            "intensity": round(state.intensity, 2),
            "pad": state.pad.to_dict(),
            "affection": round(state.affection, 2),
            "loneliness": round(state.loneliness, 2),
            "concern": round(state.concern, 2),
            "playfulness": round(state.playfulness, 2),
            "prompt_hint": hint,
        }

    def reset(self) -> None:
        """重置到基线情绪（含关系维度）。"""
        self.state = AIEmotionState(
            pad=PADVector(**self.baseline.__dict__),
            affection=self.baseline_affection,
            loneliness=self.baseline_loneliness,
            concern=self.baseline_concern,
            playfulness=self.baseline_playfulness,
        )
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
