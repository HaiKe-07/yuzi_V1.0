"""工具：情绪推荐音乐（T4-03）。

结合当前用户情绪与场景，从酷狗音乐推荐合适歌曲并播放（对接 T3-07 音乐控制）。
验收：用户难过时推荐舒缓治愈类歌曲，开心时推荐轻快类。

设计：
1. 情绪 → 推荐风格/关键词/I 例曲 映射表（覆盖 core/emotion/types.py 全部 20+ 情绪）
2. 输入可为 EmotionType 枚举或中文情绪名；未识别走中性推荐
3. 输出结构化推荐（风格、关键词、示例曲、舒缓/活力标记）
4. play_for_emotion：把推荐关键词交给 MusicController.play 播放（非 Windows 降级提示）
"""
from __future__ import annotations

from typing import Dict, Optional, Union

try:
    from core.emotion.types import EmotionType
    _HAS_EMOTION = True
except Exception:  # 无情绪引擎时兜底
    _HAS_EMOTION = False
    EmotionType = None


# 情绪 → 推荐配置：style 风格、keyword 搜索关键词、example 示例曲、calm 是否舒缓
EMOTION_MUSIC: Dict[str, dict] = {
    "中性":  {"style": "畅销流行", "keyword": "热门 流行", "example": "流行热门歌单", "calm": False},
    # 正面
    "开心":  {"style": "轻快流行、电子", "keyword": "轻快 流行", "example": "晴天", "calm": False},
    "兴奋":  {"style": "动感电子、舞曲", "keyword": "动感 电子 舞曲", "example": "火力全开", "calm": False},
    "平静":  {"style": "轻音乐、钢琴", "keyword": "轻音乐 钢琴", "example": "菊次郎的夏天", "calm": True},
    "感恩":  {"style": "温暖治愈、吉他", "keyword": "温暖 治愈 民谣", "example": "岁月神偷", "calm": True},
    "自豪":  {"style": "激昂、管弦/摇滚", "keyword": "激昂 摇滚", "example": "We Will Rock You", "calm": False},
    "期待":  {"style": "励志、节奏感强", "keyword": "励志 正能量", "example": "Catch My Breath", "calm": False},
    "亲昵":  {"style": "甜蜜温柔、情歌", "keyword": "甜蜜 温柔 情歌", "example": "告白气球", "calm": True},
    "受鼓舞": {"style": "激昂、励志", "keyword": "励志 振奋", "example": "Dream It Possible", "calm": False},
    # 负面
    "难过":  {"style": "治愈系、抒情慢歌", "keyword": "治愈 抒情 慢歌", "example": "小幸运", "calm": True},
    "焦虑":  {"style": "白噪音、轻音乐", "keyword": "白噪音 轻音乐", "example": "雨声白噪音", "calm": True},
    "生气":  {"style": "平静纯音乐、钢琴", "keyword": "平静 纯音乐 钢琴", "example": "夜的钢琴曲", "calm": True},
    "挫败":  {"style": "舒缓、疗愈", "keyword": "舒缓 疗愈", "example": "平凡之路", "calm": True},
    "孤独":  {"style": "温暖民谣", "keyword": "温暖 民谣", "example": "安和桥", "calm": True},
    "内疚":  {"style": "温柔、轻音乐", "keyword": "温柔 轻音乐", "example": "Better Man", "calm": True},
    "尴尬":  {"style": "轻快电音、缓解气氛", "keyword": "轻快 电音 洗脑", "example": "Dance Monkey", "calm": False},
    "失望":  {"style": "治愈、抒情", "keyword": "治愈 抒情", "example": "像我这样的人", "calm": True},
    "嫉妒":  {"style": "燃、节奏感强，释放", "keyword": "燃 节奏 电子", "example": "The Phoenix", "calm": False},
    "无聊":  {"style": "欢快流行、提神", "keyword": "欢快 流行 洗脑", "example": "不如跳舞", "calm": False},
    "疲惫":  {"style": "民谣、舒缓爵士", "keyword": "民谣 舒缓 爵士", "example": "Five Hundred Miles", "calm": True},
    # 复杂
    "感动":  {"style": "抒情、弦乐", "keyword": "抒情 弦乐", "example": "Someone Like You", "calm": True},
    "怀念":  {"style": "经典老歌、民谣", "keyword": "经典 老歌 民谣", "example": "后来", "calm": True},
}

# 未在表内的情绪回退
DEFAULT_CFG = EMOTION_MUSIC["中性"]


def emotion_name(emotion: Union[str, object]) -> str:
    """把 EmotionType 枚举或中文串规整为中文情绪名。"""
    if emotion is None:
        return "中性"
    if isinstance(emotion, str):
        return emotion.strip().replace("情绪", "")
    # EmotionType 枚举
    return emotion.value


def recommend(emotion: Union[str, object]) -> dict:
    """根据情绪返回推荐配置。未识别 → 中性推荐。"""
    name = emotion_name(emotion)
    cfg = EMOTION_MUSIC.get(name, DEFAULT_CFG)
    reported = name if name in EMOTION_MUSIC else "中性"
    return {
        "emotion": reported,
        "style": cfg["style"],
        "keyword": cfg["keyword"],
        "example": cfg["example"],
        "calm": cfg["calm"],
        "suggestion": (
            f"根据你现在的情绪（{reported}），为你推荐{cfg['style']}类型的歌曲，"
            f"可以试试：{cfg['keyword']}"
        ),
    }


def recommend_and_play(emotion: Union[str, object], controller=None) -> str:
    """按情绪推荐并播放（controller 为 tools.music.MusicController）。

    Returns:
        播放结果文案；未提供 controller 时仅返回推荐文案。
    """
    r = recommend(emotion)
    if controller is None:
        return r["suggestion"]
    # 用 “风格关键词 + 示例曲” 作为搜索关键词交给音乐控制
    query = f"{r['keyword']} {r['example']}".strip()
    result = controller.play(query)
    return f"{r['suggestion']}\n{result}"


def all_recommendations() -> Dict[str, dict]:
    """返回全部情绪推荐映射（供 UI / 文档）。"""
    return {k: {kk: vv for kk, vv in v.items() if kk != "calm" or kk} for k, v in EMOTION_MUSIC.items()}


# LLM 工具声明
def describe() -> dict:
    return {
        "name": "recommend_music",
        "description": "根据用户情绪推荐音乐并播放。",
        "parameters": {
            "type": "object",
            "properties": {
                "emotion": {"type": "string", "description": "情绪，如 开心/难过/生气/焦虑/疲惫/期待"},
                "play": {"type": "boolean", "description": "是否直接播放", "default": True},
            },
            "required": ["emotion"],
        },
    }


if _HAS_EMOTION:
    # 便于按 EmotionType 直接取值
    EMOTION_MUSIC_ENUM = {
        emo: EMOTION_MUSIC[emo.value]
        for emo in EmotionType
        if emo.value in EMOTION_MUSIC
    }