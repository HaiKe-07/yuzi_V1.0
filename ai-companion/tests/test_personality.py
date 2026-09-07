"""T2-04 动态称呼系统单元测试。

覆盖：
1. PersonalityContext.from_intimacy_and_emotion：从亲密度+情绪构造上下文
2. PersonalityEngine.build_system_prompt：动态占位符填充
3. 不同亲密度等级下 prompt 称呼建议的差异
4. AI 情绪简述注入
5. 称呼多变指引存在
6. ConversationManager 集成：动态 ctx 注入 prompt
7. 用户自定义称呼优先级
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.personality import PersonalityContext, PersonalityEngine, personality
from core.intimacy import IntimacyManager, IntimacyLevel
from core.emotion import AIEmotionEngine, EmotionState, EmotionType


# ============================================================
# 1. PersonalityContext 构造
# ============================================================
def test_from_intimacy_and_emotion_basic():
    """从亲密度+情绪构造上下文，字段应填充。"""
    im = IntimacyManager(persistence=False)
    im.state.score = 50.0  # 亲近
    # 高 empathy + 同步亲密度，确保用户开心情绪能共鸣到 AI
    em = AIEmotionEngine(persistence=False, empathy_weight=0.8)
    em.set_intimacy_score(50.0)
    em.update_from_user_emotion(EmotionState(
        primary=EmotionType.HAPPY, intensity=4.0,
    ))

    ctx = PersonalityContext.from_intimacy_and_emotion(
        intimacy_manager=im, emotion_engine=em,
    )
    assert ctx.intimacy_stage == "亲近"
    assert ctx.intimacy_score == 50.0
    assert ctx.address_hint is not None
    assert ctx.ai_emotion_brief is not None
    assert "开心" in ctx.ai_emotion_brief
    print(f"  [✓] 构造上下文 stage={ctx.intimacy_stage} emotion={ctx.ai_emotion_brief[:20]}")


def test_from_intimacy_only():
    """只传亲密度，情绪字段应为 None。"""
    im = IntimacyManager(persistence=False)
    ctx = PersonalityContext.from_intimacy_and_emotion(intimacy_manager=im)
    assert ctx.intimacy_stage == "陌生"
    assert ctx.ai_emotion_brief is None
    print("  [✓] 只传亲密度，情绪字段为 None")


def test_from_emotion_only():
    """只传情绪引擎，亲密度字段应为 None。"""
    em = AIEmotionEngine(persistence=False)
    ctx = PersonalityContext.from_intimacy_and_emotion(emotion_engine=em)
    assert ctx.intimacy_stage is None
    assert ctx.ai_emotion_brief is not None
    print("  [✓] 只传情绪，亲密度字段为 None")


def test_from_none():
    """都不传，所有字段为 None（用默认值兜底）。"""
    ctx = PersonalityContext.from_intimacy_and_emotion()
    assert ctx.intimacy_stage is None
    assert ctx.ai_emotion_brief is None
    assert ctx.user_alias is None
    print("  [✓] 都不传，字段全 None")


def test_user_alias_override():
    """用户自定义称呼优先级最高。"""
    im = IntimacyManager(persistence=False)
    ctx = PersonalityContext.from_intimacy_and_emotion(
        intimacy_manager=im, user_alias="小明",
    )
    assert ctx.user_alias == "小明"
    print("  [✓] 用户自定义称呼优先")


# ============================================================
# 2. 动态 prompt 填充
# ============================================================
def test_prompt_fills_intimacy_stage():
    """不同亲密度等级 prompt 中 stage 应不同。"""
    engine = PersonalityEngine()

    # 陌生
    im1 = IntimacyManager(persistence=False)
    im1.state.score = 10.0
    ctx1 = PersonalityContext.from_intimacy_and_emotion(intimacy_manager=im1)
    p1 = engine.build_system_prompt(ctx1)
    assert "陌生" in p1

    # 挚友
    im2 = IntimacyManager(persistence=False)
    im2.state.score = 90.0
    ctx2 = PersonalityContext.from_intimacy_and_emotion(intimacy_manager=im2)
    p2 = engine.build_system_prompt(ctx2)
    assert "挚友" in p2

    assert p1 != p2, "不同亲密度 prompt 应不同"
    print("  [✓] 不同亲密度 prompt 中 stage 不同")


def test_prompt_fills_address_hint():
    """prompt 应含当前等级的称呼建议。"""
    engine = PersonalityEngine()
    im = IntimacyManager(persistence=False)
    im.state.score = 50.0  # 亲近
    ctx = PersonalityContext.from_intimacy_and_emotion(intimacy_manager=im)
    p = engine.build_system_prompt(ctx)
    # 亲近等级应有"昵称"或"小名"提示
    assert "昵称" in p or "小名" in p
    print("  [✓] prompt 含当前等级称呼建议")


def test_prompt_fills_score():
    """prompt 应含具体分数。"""
    engine = PersonalityEngine()
    im = IntimacyManager(persistence=False)
    im.state.score = 42.5
    ctx = PersonalityContext.from_intimacy_and_emotion(intimacy_manager=im)
    p = engine.build_system_prompt(ctx)
    assert "42" in p or "亲密度 42" in p
    print("  [✓] prompt 含亲密度分数")


def test_prompt_fills_emotion_brief():
    """prompt 应含 AI 情绪简述。"""
    engine = PersonalityEngine()
    em = AIEmotionEngine(persistence=False, empathy_weight=0.8)
    em.set_intimacy_score(50.0)
    em.update_from_user_emotion(EmotionState(
        primary=EmotionType.SAD, intensity=4.0,
    ))
    ctx = PersonalityContext.from_intimacy_and_emotion(emotion_engine=em)
    p = engine.build_system_prompt(ctx)
    assert "难过" in p
    print("  [✓] prompt 含 AI 情绪简述")


def test_prompt_has_address_variety_hint():
    """prompt 应含「称呼要自然多变」指引。"""
    engine = PersonalityEngine()
    p = engine.build_system_prompt()
    assert "自然多变" in p or "多变" in p
    print("  [✓] prompt 含称呼多变指引")


def test_prompt_no_unfilled_placeholder():
    """prompt 不应残留 {xxx} 占位符。"""
    engine = PersonalityEngine()
    im = IntimacyManager(persistence=False)
    em = AIEmotionEngine(persistence=False)
    ctx = PersonalityContext.from_intimacy_and_emotion(
        intimacy_manager=im, emotion_engine=em,
    )
    p = engine.build_system_prompt(ctx)
    # 不应残留 {word} 形式的占位符
    import re
    leftovers = re.findall(r"\{[a-z_]+\}", p)
    assert not leftovers, f"prompt 残留占位符: {leftovers}"
    print("  [✓] prompt 无残留占位符")


# ============================================================
# 3. 不同等级称呼建议差异
# ============================================================
def test_address_hint_differs_across_levels():
    """五级亲密度应有不同的称呼建议。"""
    hints = {}
    for score, level_name in [(10, "陌生"), (30, "熟悉"), (50, "亲近"),
                               (70, "亲密"), (90, "挚友")]:
        im = IntimacyManager(persistence=False)
        im.state.score = float(score)
        ctx = PersonalityContext.from_intimacy_and_emotion(intimacy_manager=im)
        hints[level_name] = ctx.address_hint
    # 至少有几个等级提示不同
    unique_hints = set(hints.values())
    assert len(unique_hints) >= 3, (
        f"五级称呼建议应至少 3 种不同，实际 {len(unique_hints)}"
    )
    print(f"  [✓] 五级称呼建议差异 共 {len(unique_hints)} 种")


# ============================================================
# 4. AI 情绪简述细节
# ============================================================
def test_emotion_brief_high_intensity():
    """高强度情绪简述应含'较强烈'。"""
    em = AIEmotionEngine(persistence=False, empathy_weight=0.8)
    em.update_from_user_emotion(EmotionState(
        primary=EmotionType.ANGRY, intensity=5.0,
    ))
    ctx = PersonalityContext.from_intimacy_and_emotion(emotion_engine=em)
    brief = ctx.ai_emotion_brief
    assert "较强烈" in brief or "情绪较强烈" in brief
    print(f"  [✓] 高强度情绪简述: {brief[:30]}")


def test_emotion_brief_relation_dims():
    """关系维度高时应反映在情绪简述。"""
    em = AIEmotionEngine(persistence=False, empathy_weight=0.8)
    em.set_intimacy_score(70.0)
    # 多轮亲昵互动累积 affection，使其达到"对用户亲近"阈值
    for _ in range(5):
        em.update_from_user_emotion(EmotionState(
            primary=EmotionType.AFFECTIONATE, intensity=5.0,
        ))
    ctx = PersonalityContext.from_intimacy_and_emotion(emotion_engine=em)
    brief = ctx.ai_emotion_brief
    # affection 高应含"亲近"
    assert "亲近" in brief
    print(f"  [✓] 关系维度注入情绪简述: {brief[:30]}")


# ============================================================
# 5. ConversationManager 集成
# ============================================================
def _make_mock_llm(reply_text="嗯，我在听呢"):
    llm = MagicMock()
    from llm import LLMResponse
    llm.chat.return_value = LLMResponse(text=reply_text, usage={"total_tokens": 10})
    return llm


def _make_mock_asr():
    asr = MagicMock()
    from speech import ASRResult
    asr.transcribe.return_value = ASRResult(text="你好")
    return asr


def _make_mock_tts():
    tts = MagicMock()
    from speech import TTSResult
    tts.default_voice = "nova"
    tts.synthesize.return_value = TTSResult(audio=b"X", format="mp3", voice="nova")
    return tts


def _make_manager_full(reply_text="嗯嗯，我在呢"):
    """构造带亲密度+情绪引擎的 manager。"""
    from core.conversation import ConversationManager
    im = IntimacyManager(persistence=False)
    em = AIEmotionEngine(persistence=False)
    return ConversationManager(
        llm=_make_mock_llm(reply_text),
        asr=_make_mock_asr(),
        tts=_make_mock_tts(),
        emotion_engine=em,
        intimacy_manager=im,
        persist=False,
    )


def test_conversation_prompt_has_dynamic_intimacy():
    """对话 prompt 应含动态亲密度（非默认值）。"""
    m = _make_manager_full()
    m.intimacy.state.score = 65.0  # 亲密
    m.text_chat("你好")
    sys_prompt = m.llm.chat.call_args.kwargs["system_prompt"]
    assert "亲密" in sys_prompt, "prompt 应含亲密度等级"
    print("  [✓] 对话 prompt 含动态亲密度等级")


def test_conversation_prompt_has_dynamic_emotion():
    """对话 prompt 应含动态 AI 情绪。"""
    m = _make_manager_full()
    m.text_chat("我今天好难过")
    sys_prompt = m.llm.chat.call_args.kwargs["system_prompt"]
    # AI 被用户难过情绪带动，prompt 应反映情绪
    assert "情绪" in sys_prompt
    print("  [✓] 对话 prompt 含动态 AI 情绪")


def test_conversation_prompt_differs_by_intimacy():
    """不同亲密度下 prompt 应不同。"""
    m_low = _make_manager_full()
    m_low.intimacy.state.score = 10.0  # 陌生
    m_low.text_chat("你好")
    p_low = m_low.llm.chat.call_args.kwargs["system_prompt"]

    m_high = _make_manager_full()
    m_high.intimacy.state.score = 90.0  # 挚友
    m_high.text_chat("你好")
    p_high = m_high.llm.chat.call_args.kwargs["system_prompt"]

    assert p_low != p_high, "不同亲密度 prompt 应不同"
    assert "陌生" in p_low
    assert "挚友" in p_high
    print("  [✓] 不同亲密度 prompt 不同")


def test_conversation_prompt_has_address_variety():
    """对话 prompt 应含称呼多变指引。"""
    m = _make_manager_full()
    m.text_chat("你好")
    sys_prompt = m.llm.chat.call_args.kwargs["system_prompt"]
    assert "自然多变" in sys_prompt or "多变" in sys_prompt
    print("  [✓] 对话 prompt 含称呼多变指引")


def test_conversation_no_unfilled_placeholder():
    """对话 prompt 不应残留占位符。"""
    m = _make_manager_full()
    m.text_chat("你好")
    sys_prompt = m.llm.chat.call_args.kwargs["system_prompt"]
    import re
    leftovers = re.findall(r"\{[a-z_]+\}", sys_prompt)
    assert not leftovers, f"对话 prompt 残留占位符: {leftovers}"
    print("  [✓] 对话 prompt 无残留占位符")


def test_conversation_without_engines_uses_defaults():
    """关闭亲密度+情绪时 prompt 应用默认值兜底。"""
    from core.conversation import ConversationManager
    m = ConversationManager(
        llm=_make_mock_llm(),
        asr=_make_mock_asr(),
        tts=_make_mock_tts(),
        emotion_engine=False,
        intimacy_manager=False,
        persist=False,
    )
    m.text_chat("你好")
    sys_prompt = m.llm.chat.call_args.kwargs["system_prompt"]
    # 默认值应填充
    assert "陌生" in sys_prompt or "你" in sys_prompt
    import re
    leftovers = re.findall(r"\{[a-z_]+\}", sys_prompt)
    assert not leftovers, f"默认 prompt 残留占位符: {leftovers}"
    print("  [✓] 关闭引擎时 prompt 用默认值兜底")


# ============================================================
# main
# ============================================================
def main() -> int:
    print("T2-04 动态称呼系统单元测试\n")

    print("【1】PersonalityContext 构造")
    test_from_intimacy_and_emotion_basic()
    test_from_intimacy_only()
    test_from_emotion_only()
    test_from_none()
    test_user_alias_override()

    print("\n【2】动态 prompt 填充")
    test_prompt_fills_intimacy_stage()
    test_prompt_fills_address_hint()
    test_prompt_fills_score()
    test_prompt_fills_emotion_brief()
    test_prompt_has_address_variety_hint()
    test_prompt_no_unfilled_placeholder()

    print("\n【3】不同等级称呼建议差异")
    test_address_hint_differs_across_levels()

    print("\n【4】AI 情绪简述细节")
    test_emotion_brief_high_intensity()
    test_emotion_brief_relation_dims()

    print("\n【5】ConversationManager 集成")
    test_conversation_prompt_has_dynamic_intimacy()
    test_conversation_prompt_has_dynamic_emotion()
    test_conversation_prompt_differs_by_intimacy()
    test_conversation_prompt_has_address_variety()
    test_conversation_no_unfilled_placeholder()
    test_conversation_without_engines_uses_defaults()

    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
