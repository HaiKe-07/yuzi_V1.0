"""情绪类型定义。

参考 Ekman 基本情绪 + Plutchik 情绪轮扩展，覆盖陪伴场景常见 20+ 种。

设计原则：
- 每种情绪有 PAD 坐标，方便 AI 情绪融合计算
- 强度分级 1~5，对应"有点/比较/挺/很/极度"
- 混合情绪：主情绪 + 次要情绪列表（如"开心又有点想哭"）
- 中性 (neutral) 单独占位，表示无明显情绪
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EmotionType(str, Enum):
    """情绪类型（20 种 + 中性）。

    命名遵循中文，便于人格 prompt 与日志直接使用。
    """
    NEUTRAL = "中性"

    # 正面
    HAPPY = "开心"
    EXCITED = "兴奋"
    CALM = "平静"
    GRATEFUL = "感恩"
    PROUD = "自豪"
    HOPEFUL = "期待"
    AFFECTIONATE = "亲昵"
    INSPIRED = "受鼓舞"

    # 负面
    SAD = "难过"
    ANXIOUS = "焦虑"
    ANGRY = "生气"
    FRUSTRATED = "挫败"
    LONELY = "孤独"
    GUILTY = "内疚"
    EMBARRASSED = "尴尬"
    DISAPPOINTED = "失望"
    JEALOUS = "嫉妒"
    BORED = "无聊"
    TIRED = "疲惫"

    # 复杂
    MOVED = "感动"
    NOSTALGIC = "怀念"


@dataclass
class PADVector:
    """PAD 情感三维坐标。

    pleasure: 愉悦度 -1~1（负→正）
    arousal:  唤醒度 -1~1（平静→激动）
    dominance: 支配度 -1~1（受控→主导）

    参考 Mehrabian 模型。每种情绪有近似坐标，便于：
    1. 用户情绪 → AI 情绪融合（共鸣）
    2. AI 情绪 → TTS 音色/语速映射（T2-06）
    """
    pleasure: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0

    def blend(self, other: "PADVector", weight: float = 0.5) -> "PADVector":
        """加权融合另一个 PAD 向量。"""
        w = max(0.0, min(1.0, weight))
        return PADVector(
            pleasure=self.pleasure * (1 - w) + other.pleasure * w,
            arousal=self.arousal * (1 - w) + other.arousal * w,
            dominance=self.dominance * (1 - w) + other.dominance * w,
        )

    def distance(self, other: "PADVector") -> float:
        """欧氏距离，用于找最接近的情绪类型。"""
        return (
            (self.pleasure - other.pleasure) ** 2
            + (self.arousal - other.arousal) ** 2
            + (self.dominance - other.dominance) ** 2
        ) ** 0.5

    def to_dict(self) -> dict:
        return {
            "pleasure": round(self.pleasure, 3),
            "arousal": round(self.arousal, 3),
            "dominance": round(self.dominance, 3),
        }


# 每种情绪对应的 PAD 坐标（近似值，来自心理学常用映射）
EMOTION_PAD: dict[EmotionType, PADVector] = {
    EmotionType.NEUTRAL:      PADVector(0.0, 0.0, 0.0),
    # 正面
    EmotionType.HAPPY:        PADVector(0.8, 0.4, 0.2),
    EmotionType.EXCITED:      PADVector(0.7, 0.8, 0.1),
    EmotionType.CALM:         PADVector(0.5, -0.6, 0.3),
    EmotionType.GRATEFUL:     PADVector(0.6, 0.1, -0.2),
    EmotionType.PROUD:        PADVector(0.6, 0.5, 0.7),
    EmotionType.HOPEFUL:      PADVector(0.4, 0.3, -0.1),
    EmotionType.AFFECTIONATE: PADVector(0.7, 0.2, -0.3),
    EmotionType.INSPIRED:     PADVector(0.6, 0.5, 0.3),
    # 负面
    EmotionType.SAD:          PADVector(-0.7, -0.4, -0.5),
    EmotionType.ANXIOUS:      PADVector(-0.5, 0.6, -0.4),
    EmotionType.ANGRY:        PADVector(-0.6, 0.8, 0.5),
    EmotionType.FRUSTRATED:   PADVector(-0.5, 0.4, -0.2),
    EmotionType.LONELY:       PADVector(-0.5, -0.4, -0.6),
    EmotionType.GUILTY:       PADVector(-0.4, 0.2, -0.7),
    EmotionType.EMBARRASSED:  PADVector(-0.3, 0.3, -0.6),
    EmotionType.DISAPPOINTED: PADVector(-0.4, -0.2, -0.4),
    EmotionType.JEALOUS:      PADVector(-0.3, 0.4, -0.3),
    EmotionType.BORED:        PADVector(-0.2, -0.7, -0.2),
    EmotionType.TIRED:        PADVector(-0.3, -0.7, -0.3),
    # 复杂
    EmotionType.MOVED:        PADVector(0.4, 0.3, -0.3),
    EmotionType.NOSTALGIC:    PADVector(0.1, -0.3, -0.2),
}


def pad_of(emotion: EmotionType) -> PADVector:
    """获取某情绪类型的 PAD 坐标。"""
    return EMOTION_PAD.get(emotion, PADVector())


def nearest_emotion(pad: PADVector) -> EmotionType:
    """从 PAD 坐标找最接近的情绪类型。"""
    best = EmotionType.NEUTRAL
    best_d = float("inf")
    for emo, vec in EMOTION_PAD.items():
        d = pad.distance(vec)
        if d < best_d:
            best_d = d
            best = emo
    return best


@dataclass
class EmotionState:
    """情绪状态：主情绪 + 次要情绪 + 强度。

    支持混合情绪，例如：
        primary = SAD, intensity = 4
        secondary = [(LONELY, 3), (DISAPPOINTED, 2)]
    """
    primary: EmotionType = EmotionType.NEUTRAL
    intensity: float = 1.0  # 1~5
    secondary: list[tuple[EmotionType, float]] = field(default_factory=list)

    def label(self) -> str:
        """可读标签，用于人格 prompt / 日志。"""
        parts = [f"{self.primary.value}{_intensity_word(self.intensity)}"]
        for emo, inten in self.secondary[:2]:  # 最多展示 2 个次要
            parts.append(f"有点{emo.value}")
        return "、".join(parts)

    def to_dict(self) -> dict:
        return {
            "primary": self.primary.value,
            "intensity": round(self.intensity, 2),
            "secondary": [
                {"emotion": e.value, "intensity": round(i, 2)}
                for e, i in self.secondary
            ],
            "pad": pad_of(self.primary).to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EmotionState":
        primary = EmotionType(d.get("primary", "中性"))
        secondary = [
            (EmotionType(s["emotion"]), float(s["intensity"]))
            for s in d.get("secondary", [])
        ]
        return cls(
            primary=primary,
            intensity=float(d.get("intensity", 1.0)),
            secondary=secondary,
        )


def _intensity_word(intensity: float) -> str:
    """强度转口语词。"""
    if intensity >= 4.5:
        return "极了"
    if intensity >= 3.5:
        return "得很"
    if intensity >= 2.5:
        return "挺"
    if intensity >= 1.5:
        return "有点"
    return ""
