"""用户情绪感知器。

把用户输入文本映射为 EmotionState（主+次+强度）。

第一阶段采用两阶段策略：
1. 关键词/词袋规则：覆盖常见情绪词，无 LLM 调用，零延迟
2. LLM 兜底：规则未命中时调用 LLM 做情绪分类（需 API key）

规则库覆盖 20+ 种情绪，每种情绪配套：
- 触发词列表（含口语/网络用语）
- 强度修饰词（"很/挺/极/有点..."）
- 否定词（"不/没/别..."，会反转情绪）

T2-02 阶段：当 LLM 情绪识别稳定后，规则降为兜底。
"""
from __future__ import annotations

import re
from typing import Any

from utils.config import config
from utils.logger import logger

from .types import EmotionState, EmotionType, PADVector, pad_of


# ============================================================
# 情绪词典（关键词 → 情绪类型 + 默认强度）
# ============================================================
# 每个情绪类型对应若干触发词，匹配后默认强度 3
_EMOTION_KEYWORDS: dict[EmotionType, list[str]] = {
    EmotionType.HAPPY:        ["开心", "高兴", "快乐", "乐呵", "美滋滋", "心情好", "舒服"],
    EmotionType.EXCITED:      ["激动", "兴奋", "嗨", "太棒了", "牛", "绝了", "炸裂"],
    EmotionType.CALM:         ["平静", "安宁", "放松", "佛系", "无所谓", "随便"],
    EmotionType.GRATEFUL:     ["谢谢", "感谢", "多谢", "辛苦了", "麻烦你了", "劳驾"],
    EmotionType.PROUD:        ["骄傲", "自豪", "牛气", "成功", "做到了", "完成"],
    EmotionType.HOPEFUL:      ["期待", "希望", "盼望", "愿望", "梦想", "想试"],
    EmotionType.AFFECTIONATE: ["想你", "喜欢你", "爱你", "抱抱", "亲亲", "宝贝", "甜"],
    EmotionType.INSPIRED:     ["受鼓舞", "燃", "打鸡血", "有动力", "精神了"],
    EmotionType.SAD:          ["难过", "伤心", "哭", "悲痛", "心碎", "蓝瘦", "emo", "郁闷"],
    EmotionType.ANXIOUS:      ["焦虑", "紧张", "害怕", "怕", "担心", "愁", "慌", "恐惧"],
    EmotionType.ANGRY:        ["生气", "怒", "气死", "火大", "烦死了", "讨厌", "可恶", "气"],
    EmotionType.FRUSTRATED:   ["挫败", "不行", "做不到", "太难", "搞不定", "放弃", "白费"],
    EmotionType.LONELY:       ["孤独", "寂寞", "一个人", "没人", "没人陪", "冷清"],
    EmotionType.GUILTY:       ["内疚", "对不起", "抱歉", "是我的错", "怪我", "不好意思"],
    EmotionType.EMBARRASSED:  ["尴尬", "丢人", "社死", "脸红", "窘迫", "出洋相"],
    EmotionType.DISAPPOINTED:  ["失望", "白等", "就这", "不过如此", "可惜", "遗憾"],
    EmotionType.JEALOUS:      ["嫉妒", "酸", "吃醋", "羡慕", "凭什么"],
    EmotionType.BORED:        ["无聊", "没劲", "没意思", "乏味", "枯燥"],
    EmotionType.TIRED:        ["累", "疲惫", "困", "犯困", "撑不住", "没力气", "虚"],
    EmotionType.MOVED:        ["感动", "泪目", "破防", "戳中", "暖心", "泪崩"],
    EmotionType.NOSTALGIC:    ["怀念", "回忆", "从前", "那时候", "小时候", "往事"],
}


# 强度修饰词 → 加成
_INTENSITY_BOOST: list[tuple[str, float]] = [
    (r"极了|超|爆|极度|非常|特别|真的|超", 1.5),   # 3 → 4.5
    (r"很|挺|蛮|好|相当|实在|确实", 1.0),           # 3 → 4
    (r"有点|稍微|一点|略", -1.0),                   # 3 → 2
    (r"特别特别|超级|无敌", 2.0),                    # 3 → 5
]

# 否定词（在情绪词前 3 字内出现视为反转）
_NEGATION_WORDS = ["不", "没", "别", "无", "毫不", "并不", "没在"]


def _find_keyword(text: str, keywords: list[str]) -> tuple[bool, int]:
    """在 text 中查找关键词。

    Returns:
        (matched, position)
    """
    for kw in keywords:
        idx = text.find(kw)
        if idx >= 0:
            return True, idx
    return False, -1


def _check_negation(text: str, pos: int) -> bool:
    """检查情绪词前 3 字内是否有否定词。"""
    start = max(0, pos - 3)
    window = text[start:pos]
    return any(neg in window for neg in _NEGATION_WORDS)


def _compute_intensity(
    base_intensity: float, text: str, pos: int
) -> float:
    """根据强度修饰词调整。"""
    window = text[max(0, pos - 4): pos + 6]
    intensity = base_intensity
    for pattern, boost in _INTENSITY_BOOST:
        if re.search(pattern, window):
            intensity += boost
            break  # 只取最强一个
    # 感叹号 +0.5
    if "！" in window or "!" in window:
        intensity += 0.5
    # 重复字符（如"哈哈哈""呜呜呜"）+0.5
    if re.search(r"(.)\1{2,}", text):
        intensity += 0.5
    return max(1.0, min(5.0, intensity))


def detect_emotion(text: str) -> EmotionState:
    """从文本检测情绪状态。

    Args:
        text: 用户输入
    Returns:
        EmotionState（主情绪 + 次要情绪 + 强度）
    """
    if not text or not text.strip():
        return EmotionState()

    hits: list[tuple[EmotionType, float, int]] = []
    for emotion, keywords in _EMOTION_KEYWORDS.items():
        matched, pos = _find_keyword(text, keywords)
        if not matched:
            continue
        if _check_negation(text, pos):
            # 否定情绪：跳过此情绪，不加入命中
            continue
        intensity = _compute_intensity(3.0, text, pos)
        hits.append((emotion, intensity, pos))

    if not hits:
        return EmotionState()

    # 按强度排序
    hits.sort(key=lambda x: x[1], reverse=True)
    primary_emo, primary_int, _ = hits[0]
    secondary = [(e, i) for e, i, _ in hits[1:3] if i >= 1.5]

    return EmotionState(
        primary=primary_emo,
        intensity=primary_int,
        secondary=secondary,
    )


# ============================================================
# LLM 兜底：规则未命中时调用
# ============================================================
_LLM_EMOTION_PROMPT = """请分析用户这句话的情绪，只返回 JSON：
{"primary": "情绪词", "intensity": 1到5的数字, "secondary": [{"emotion": "情绪词", "intensity": 数字}]}

可选情绪词（21种）：中性、开心、兴奋、平静、感恩、自豪、期待、亲昵、受鼓舞、
难过、焦虑、生气、挫败、孤独、内疚、尴尬、失望、嫉妒、无聊、疲惫、感动、怀念

用户说：{text}
"""


def detect_emotion_with_llm(
    text: str, llm=None
) -> EmotionState:
    """LLM 兜底情绪识别。规则未命中时使用。

    Args:
        text: 用户输入
        llm:  LLM 适配器，None 则用全局默认
    """
    if not text or not text.strip():
        return EmotionState()

    # 先走规则，命中直接返回
    rule_result = detect_emotion(text)
    if rule_result.primary != EmotionType.NEUTRAL:
        return rule_result

    # 规则未命中 → LLM
    if llm is None:
        try:
            from llm import get_default_llm
            llm = get_default_llm()
        except Exception as e:
            logger.debug(f"LLM 不可用，回退中性情绪: {e}")
            return EmotionState()

    try:
        from llm import Message
        prompt = _LLM_EMOTION_PROMPT.replace("{text}", text)
        resp = llm.chat([Message(role="user", content=prompt)])
        import json
        # 容错：LLM 可能返回带 markdown 代码块
        raw = resp.text.strip()
        raw = re.sub(r"^```json\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
        return EmotionState.from_dict(data)
    except Exception as e:
        logger.debug(f"LLM 情绪识别失败，回退规则: {e}")
        return rule_result
