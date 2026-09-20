"""AI 情绪 → Live2D 表情映射（T3-02）。

把 AIEmotionEngine 输出的 20 种中文情绪标签映射到 Live2D 表情文件名。
同时考虑情绪强度：低强度时用更柔和的表情变体。

Live2D 模型表情文件（expressions/*.exp3.json）命名约定：
    happy / happy_strong
    sad / sad_strong
    angry / angry_strong
    neutral
    excited / excited_strong
    calm
    embarrassed
    surprised
    tired
    lonely

对于模型未提供的表情，回退到最近的近似表情（如"内疚"→"sad"）。

映射策略：
1. 正面情绪（开心/兴奋/期待/亲昵）→ happy 系
2. 负面情绪（难过/焦虑/生气/孤独）→ sad/angry 系
3. 中性/平静 → neutral/calm
4. 复杂情绪（感动/怀念）→ calm（柔和）
"""
from __future__ import annotations

from .types import EmotionType


# 情绪标签 → Live2D 表情基础名
_EMOTION_TO_EXPRESSION: dict[EmotionType, str] = {
    # 中性
    EmotionType.NEUTRAL: "neutral",

    # 正面
    EmotionType.HAPPY: "happy",
    EmotionType.EXCITED: "excited",
    EmotionType.CALM: "calm",
    EmotionType.GRATEFUL: "happy",         # 感恩 → happy（柔和）
    EmotionType.PROUD: "happy",            # 自豪 → happy
    EmotionType.HOPEFUL: "excited",        # 期待 → excited
    EmotionType.AFFECTIONATE: "happy",     # 亲昵 → happy
    EmotionType.INSPIRED: "excited",       # 受鼓舞 → excited

    # 负面
    EmotionType.SAD: "sad",
    EmotionType.ANXIOUS: "sad",            # 焦虑 → sad（担忧表情）
    EmotionType.ANGRY: "angry",
    EmotionType.FRUSTRATED: "angry",       # 挫败 → angry
    EmotionType.LONELY: "lonely",
    EmotionType.GUILTY: "sad",             # 内疚 → sad
    EmotionType.EMBARRASSED: "embarrassed",
    EmotionType.DISAPPOINTED: "sad",       # 失望 → sad
    EmotionType.JEALOUS: "angry",          # 嫉妒 → angry
    EmotionType.BORED: "neutral",
    EmotionType.TIRED: "tired",

    # 复杂
    EmotionType.MOVED: "calm",             # 感动 → calm（柔和）
    EmotionType.NOSTALGIC: "calm",         # 怀念 → calm
}

# 表情强度分档阈值
_STRONG_THRESHOLD = 0.7   # intensity > 0.7 用 _strong 变体
_MILD_THRESHOLD = 0.3     # intensity < 0.3 保持基础表情

# 支持 _strong 变体的表情（模型制作了强烈版本）
_STRONG_VARIANTS: set[str] = {
    "happy", "sad", "angry", "excited",
}


def emotion_to_expression(
    emotion: EmotionType | str,
    intensity: float = 0.5,
) -> str:
    """把 AI 情绪映射到 Live2D 表情文件名。

    Args:
        emotion: EmotionType 枚举或中文标签字符串
        intensity: 情绪强度 0~1，高于 0.7 用 _strong 变体

    Returns:
        Live2D 表情文件名（不含 .exp3.json 后缀）

    Examples:
        >>> emotion_to_expression(EmotionType.HAPPY, 0.8)
        'happy_strong'
        >>> emotion_to_expression("难过", 0.5)
        'sad'
        >>> emotion_to_expression("中性", 0.0)
        'neutral'
    """
    # 统一转为 EmotionType
    if isinstance(emotion, str):
        try:
            emotion = EmotionType(emotion)
        except ValueError:
            return "neutral"
    elif not isinstance(emotion, EmotionType):
        return "neutral"

    base = _EMOTION_TO_EXPRESSION.get(emotion, "neutral")

    # 高强度 + 支持强烈变体 → 用 _strong 版本
    if intensity > _STRONG_THRESHOLD and base in _STRONG_VARIANTS:
        return f"{base}_strong"

    return base


def get_available_expressions() -> list[str]:
    """返回所有可能的 Live2D 表情名（去重）。

    用于前端检测模型支持哪些表情。
    """
    return sorted(set(_EMOTION_TO_EXPRESSION.values()) | _STRONG_VARIANTS)


def is_strong_variant(name: str) -> bool:
    """判断表情名是否是强烈变体。"""
    return name.endswith("_strong")


def base_expression(name: str) -> str:
    """获取表情名的基础版本（去掉 _strong 后缀）。"""
    if name.endswith("_strong"):
        return name[:-7]
    return name
