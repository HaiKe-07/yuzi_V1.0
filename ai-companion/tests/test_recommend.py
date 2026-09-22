"""T4-03 情绪推荐音乐测试。

覆盖：
1. 情绪→风格映射正确性：难过→治愈慢歌(舒缓)、开心→轻快、生气→平静钢琴、焦虑→白噪音、疲惫→民谣爵士、期待→励志
2. recommend：未识别→中性、EmotionType 枚举输入、推荐结构字段
3. recommend_and_play：接 MusicController 播放（验证 query 传递与降级）
4. LLM schema / 全量映射
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.recommend import (  # noqa: E402
    EMOTION_MUSIC,
    describe,
    emotion_name,
    recommend,
    recommend_and_play,
)


# ---------- 验收映射 ----------
def test_sad_recommends_calming():
    r = recommend("难过")
    assert r["style"] == "治愈系、抒情慢歌"
    assert r["calm"] is True
    assert "治愈" in r["suggestion"]
    print("  [✓] 难过→治愈舒缓（验收）")


def test_happy_recommends_upbeat():
    r = recommend("开心")
    assert "轻快" in r["style"]
    assert r["calm"] is False
    print("  [✓] 开心→轻快（验收）")


def test_angry_recommends_calm_piano():
    r = recommend("生气")
    assert "钢琴" in r["style"]
    assert r["calm"] is True
    print("  [✓] 生气→平静钢琴")


def test_anxious_recommends_white_noise():
    r = recommend("焦虑")
    assert "白噪音" in r["style"]
    assert r["calm"] is True
    print("  [✓] 焦虑→白噪音")


def test_tired_recommends_folk_jazz():
    r = recommend("疲惫")
    assert "民谣" in r["style"]
    assert r["calm"] is True
    print("  [✓] 疲惫→民谣爵士")


def test_hopeful_recommends_energetic():
    r = recommend("期待")
    assert "励志" in r["style"]
    assert r["calm"] is False
    print("  [✓] 期待→励志")


# ---------- 通用 ----------
def test_emotion_name_normalization():
    assert emotion_name("开心") == "开心"
    assert emotion_name("心情有点难过") == "心情有点难过"  # 未识别字样，交给库兜底
    assert emotion_name(None) == "中性"
    print("  [✓] emotion_name 规整")


def test_recommend_unknown_falls_back_neutral():
    r = recommend("不存在情绪XYZ")
    assert r["emotion"] == "中性"
    print("  [✓] 未识别情绪 → 中性兜底")


def test_recommend_structure():
    r = recommend("失落")  # 未在表，中性兜底
    for k in ("emotion", "style", "keyword", "example", "calm", "suggestion"):
        assert k in r
    print("  [✓] 推荐字段结构完整")


def test_recommend_with_emotion_enum():
    try:
        from core.emotion.types import EmotionType
        r = recommend(EmotionType.SAD)
        assert r["emotion"] == "难过"
    except ImportError:
        pass  # 情绪引擎不可用时跳过
    print("  [✓] EmotionType 枚举输入")


# ---------- 播放集成 ----------
def test_recommend_and_play_with_controller():
    ctrl = MagicMock()
    ctrl.play.return_value = "已开始播放：治愈 抒情 慢歌 小幸运"
    r = recommend_and_play("难过", controller=ctrl)
    ctrl.play.assert_called_once()
    call_kw = ctrl.play.call_args.args[0]
    assert "治愈" in call_kw and "小幸运" in call_kw
    assert "已开始播放" in r
    print("  [✓] 推荐→播放（关键词传给音乐控制）")


def test_recommend_and_play_no_controller():
    r = recommend_and_play("开心")  # 未给 controller → 仅返回推荐文案
    assert "轻快" in r
    assert "播放" not in r
    print("  [✓] 无 controller 仅返回推荐")


# ---------- schema / 全量 ----------
def test_describe_schema():
    d = describe()
    assert d["name"] == "recommend_music"
    assert d["parameters"]["required"] == ["emotion"]
    print("  [✓] LLM 工具 schema")


def test_all_emotion_mapping_populated():
    # 覆盖率校验：core 情绪类型应基本全映射
    assert len(EMOTION_MUSIC) >= 20
    import re
    # 每项字段齐全
    for k, v in EMOTION_MUSIC.items():
        assert v["style"] and v["keyword"] and v["example"]
    print(f"  [✓] 情绪映射 {len(EMOTION_MUSIC)} 条，字段齐全")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        test_sad_recommends_calming,
        test_happy_recommends_upbeat,
        test_angry_recommends_calm_piano,
        test_anxious_recommends_white_noise,
        test_tired_recommends_folk_jazz,
        test_hopeful_recommends_energetic,
        test_emotion_name_normalization,
        test_recommend_unknown_falls_back_neutral,
        test_recommend_structure,
        test_recommend_with_emotion_enum,
        test_recommend_and_play_with_controller,
        test_recommend_and_play_no_controller,
        test_describe_schema,
        test_all_emotion_mapping_populated,
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