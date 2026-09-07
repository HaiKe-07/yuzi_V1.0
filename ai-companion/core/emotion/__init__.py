"""core.emotion 包：情绪类型 + 用户情绪感知 + AI 独立情绪（T2-02 扩展）。"""
from .ai_emotion import AIEmotionEngine, AIEmotionState
from .detector import detect_emotion, detect_emotion_with_llm
from .types import (
    EMOTION_PAD,
    EmotionState,
    EmotionType,
    PADVector,
    nearest_emotion,
    pad_of,
)

__all__ = [
    # 类型
    "EmotionType", "EmotionState", "PADVector",
    "EMOTION_PAD", "pad_of", "nearest_emotion",
    # 用户情绪
    "detect_emotion", "detect_emotion_with_llm",
    # AI 情绪（T2-01 PAD + T2-02 关系维度扩展）
    "AIEmotionEngine", "AIEmotionState",
]
