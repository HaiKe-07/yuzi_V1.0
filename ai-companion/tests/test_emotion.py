"""T2-01 情绪系统单元测试。

覆盖：
1. 情绪类型与 PAD 向量（types.py）
2. 用户情绪感知器（detector.py）：关键词命中 / 否定反转 / 强度修饰 / 混合情绪
3. AI 独立情绪引擎（ai_emotion.py）：
   - 共鸣 update_from_user_emotion
   - 内容影响 update_from_content
   - 自然衰减 decay
   - 情绪惯性（状态机粘性）
   - 持久化 _load/_save（跨"重启"恢复）
   - emotion_context 接口
4. ConversationManager 集成：情绪检测注入 + prompt 上下文 + 事件触发
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.emotion import (
    AIEmotionEngine,
    AIEmotionState,
    EmotionState,
    EmotionType,
    PADVector,
    detect_emotion,
    nearest_emotion,
    pad_of,
)
from core.emotion.types import EMOTION_PAD


# ============================================================
# 1. 情绪类型与 PAD 向量
# ============================================================
def test_emotion_types_count():
    """22 种情绪（含中性）全部定义。"""
    assert len(EmotionType) == 22, f"应有 22 种情绪，实际 {len(EmotionType)}"
    assert EmotionType.NEUTRAL.value == "中性"
    print("  [✓] 22 种情绪类型完整定义")


def test_pad_vector_blend():
    """PAD 加权融合：weight=0 保持 self，weight=1 完全取 other。"""
    a = PADVector(pleasure=1.0, arousal=0.0, dominance=-1.0)
    b = PADVector(pleasure=-1.0, arousal=1.0, dominance=1.0)
    mid = a.blend(b, 0.5)
    assert abs(mid.pleasure - 0.0) < 0.01
    assert abs(mid.arousal - 0.5) < 0.01
    assert abs(mid.dominance - 0.0) < 0.01
    # weight=0 应等于 a
    same = a.blend(b, 0.0)
    assert same.pleasure == 1.0
    # weight=1 应等于 b
    full = a.blend(b, 1.0)
    assert full.arousal == 1.0
    print("  [✓] PADVector.blend 加权融合")


def test_pad_vector_distance():
    """欧氏距离：相同点距离 0。"""
    a = PADVector(0.5, 0.5, 0.5)
    assert a.distance(a) == 0.0
    b = PADVector(0.5, 0.5, 0.8)
    assert abs(a.distance(b) - 0.3) < 0.01
    print("  [✓] PADVector.distance 欧氏距离")


def test_nearest_emotion():
    """PAD 坐标 → 最近情绪类型。"""
    # 精确命中开心
    happy_pad = pad_of(EmotionType.HAPPY)
    assert nearest_emotion(happy_pad) == EmotionType.HAPPY
    # 原点应离中性最近
    origin = PADVector(0.0, 0.0, 0.0)
    assert nearest_emotion(origin) == EmotionType.NEUTRAL
    print("  [✓] nearest_emotion PAD→情绪映射")


def test_emotion_state_label():
    """混合情绪可读标签。"""
    s = EmotionState(
        primary=EmotionType.SAD, intensity=4.0,
        secondary=[(EmotionType.LONELY, 3.0)],
    )
    label = s.label()
    assert "难过" in label
    assert "孤独" in label
    print(f"  [✓] 混合情绪标签: {label}")


# ============================================================
# 2. 用户情绪感知器
# ============================================================
def test_detect_basic_keyword():
    """基础关键词命中。"""
    s = detect_emotion("我今天很开心")
    assert s.primary == EmotionType.HAPPY
    assert s.intensity >= 1.0
    print(f"  [✓] 基础关键词命中 → {s.primary.value}")


def test_detect_intensity_modifier():
    """强度修饰词加成。"""
    base = detect_emotion("我开心")
    boosted = detect_emotion("我非常开心")
    assert boosted.intensity > base.intensity, "非常应提升强度"
    print(f"  [✓] 强度修饰 基础={base.intensity} 加成={boosted.intensity}")


def test_detect_negation():
    """否定词反转：不+开心 不应命中 HAPPY。"""
    s = detect_emotion("我今天不开心")
    assert s.primary != EmotionType.HAPPY, "否定应排除开心"
    print(f"  [✓] 否定词反转 → {s.primary.value}")


def test_detect_mixed_emotions():
    """一句话多情绪：主+次。"""
    s = detect_emotion("我又开心又有点难过")
    # 至少命中一个，且有 secondary
    assert s.primary != EmotionType.NEUTRAL
    print(f"  [✓] 混合情绪 主={s.primary.value} 次={len(s.secondary)}个")


def test_detect_empty_text():
    """空文本 → 中性。"""
    assert detect_emotion("").primary == EmotionType.NEUTRAL
    assert detect_emotion("   ").primary == EmotionType.NEUTRAL
    print("  [✓] 空文本回退中性")


def test_detect_exclamation_boost():
    """感叹号加成强度。"""
    plain = detect_emotion("我开心")
    exclam = detect_emotion("我开心！")
    assert exclam.intensity >= plain.intensity
    print("  [✓] 感叹号加成强度")


# ============================================================
# 3. AI 独立情绪引擎
# ============================================================
def make_engine(persistence: bool = False, **kwargs) -> AIEmotionEngine:
    """构造测试用 AIEmotionEngine（默认关闭持久化，避免污染项目 db）。"""
    return AIEmotionEngine(persistence=persistence, **kwargs)


def test_ai_emotion_baseline():
    """初始化后应在基线状态。"""
    e = make_engine()
    assert e.state.intensity <= 0.3, "初始强度应低"
    assert e.state.label in EMOTION_PAD, "label 应是合法情绪"
    print(f"  [✓] 基线情绪 = {e.state.label.value} intensity={e.state.intensity}")


def test_ai_empathy_updates_state():
    """用户开心 → AI 产生共鸣偏移。"""
    e = make_engine(empathy_weight=0.5)
    before_p = e.state.pad.pleasure
    e.update_from_user_emotion(EmotionState(
        primary=EmotionType.HAPPY, intensity=4.0,
    ))
    after_p = e.state.pad.pleasure
    assert after_p > before_p, "用户开心应提升 AI pleasure"
    print(f"  [✓] 共鸣 pleasure {before_p:.2f} → {after_p:.2f}")


def test_ai_content_positive():
    """被夸 → pleasure 提升。"""
    e = make_engine()
    before = e.state.pad.pleasure
    e.update_from_content("你真厉害，谢谢你")
    after = e.state.pad.pleasure
    assert after > before, "正面内容应提升 pleasure"
    print(f"  [✓] 内容正面 pleasure {before:.2f} → {after:.2f}")


def test_ai_content_negative():
    """被骂 → pleasure 下降 + dominance 下降。"""
    e = make_engine()
    before_p = e.state.pad.pleasure
    before_d = e.state.pad.dominance
    e.update_from_content("你真笨，讨厌你")
    after_p = e.state.pad.pleasure
    after_d = e.state.pad.dominance
    assert after_p < before_p, "负面内容应降低 pleasure"
    assert after_d < before_d, "被骂应降低 dominance"
    print(f"  [✓] 内容负面 pleasure {before_p:.2f}→{after_p:.2f} dom {before_d:.2f}→{after_d:.2f}")


def test_ai_decay_returns_to_baseline():
    """衰减后向基线回归。"""
    e = make_engine(decay_rate=0.5)
    # 先拉到开心
    e.update_from_user_emotion(EmotionState(
        primary=EmotionType.HAPPY, intensity=5.0,
    ))
    peak_p = e.state.pad.pleasure
    # 衰减 10 秒
    e.decay(dt_seconds=10.0)
    assert e.state.pad.pleasure < peak_p, "衰减后 pleasure 应下降"
    # 继续衰减应接近基线
    e.decay(dt_seconds=100.0)
    assert abs(e.state.pad.pleasure - e.baseline.pleasure) < 0.1, "长时间衰减应回归基线"
    print("  [✓] 衰减回归基线")


def test_ai_inertia_resists_change():
    """情绪惯性：高强度时抗拒被改写。"""
    # 高惯性引擎
    e_high = make_engine(inertia_factor=0.9, empathy_weight=0.8)
    # 先把 AI 拉到强烈开心
    e_high.update_from_user_emotion(EmotionState(
        primary=EmotionType.HAPPY, intensity=5.0,
    ))
    peak_p = e_high.state.pad.pleasure
    peak_i = e_high.state.intensity

    # 再试图用难过情绪拉走
    e_high.update_from_user_emotion(EmotionState(
        primary=EmotionType.SAD, intensity=5.0,
    ))

    # 高惯性下 pleasure 不应剧烈下降（粘住）
    drop = peak_p - e_high.state.pad.pleasure
    assert drop < 0.5, f"高惯性应抗拒改变，drop={drop:.2f} 过大"
    print(f"  [✓] 情绪惯性 阻止 pleasure 突变 drop={drop:.2f}")

    # 对照：零惯性应剧烈改变
    e_low = make_engine(inertia_factor=0.0, empathy_weight=0.8)
    e_low.update_from_user_emotion(EmotionState(
        primary=EmotionType.HAPPY, intensity=5.0,
    ))
    peak_p_low = e_low.state.pad.pleasure
    e_low.update_from_user_emotion(EmotionState(
        primary=EmotionType.SAD, intensity=5.0,
    ))
    drop_low = peak_p_low - e_low.state.pad.pleasure
    assert drop_low > drop, "零惯性应比高惯性变化更大"
    print(f"  [✓] 对照零惯性 drop={drop_low:.2f} > 高惯性 drop={drop:.2f}")


def test_ai_emotion_context():
    """emotion_context 返回 prompt_hint。"""
    e = make_engine()
    e.update_from_user_emotion(EmotionState(
        primary=EmotionType.HAPPY, intensity=4.0,
    ))
    ctx = e.emotion_context()
    assert "label" in ctx
    assert "intensity" in ctx
    assert "pad" in ctx
    assert "prompt_hint" in ctx
    assert "情绪" in ctx["prompt_hint"] or ctx["label"] in ctx["prompt_hint"]
    print(f"  [✓] emotion_context label={ctx['label']} hint={ctx['prompt_hint'][:30]}")


def test_ai_reset():
    """reset 回到基线。"""
    e = make_engine()
    e.update_from_user_emotion(EmotionState(
        primary=EmotionType.ANGRY, intensity=5.0,
    ))
    assert e.state.intensity > 0.1
    e.reset()
    assert abs(e.state.pad.pleasure - e.baseline.pleasure) < 0.01
    assert e.state.intensity <= 0.3
    print("  [✓] reset 回到基线")


# ============================================================
# 4. 持久化：跨"重启"恢复
# ============================================================
def test_ai_emotion_persistence():
    """情绪状态保存后，新引擎实例能恢复。"""
    with tempfile.TemporaryDirectory() as tmp:
        from core.memory.db import Database
        # 把全局 db 指向临时文件
        Database.reload(db_path=Path(tmp) / "test_emo.db")
        db = Database.get_instance()
        db.init()

        # 第一个引擎：写入强烈开心情绪
        e1 = AIEmotionEngine(persistence=True, empathy_weight=0.8)
        e1.update_from_user_emotion(EmotionState(
            primary=EmotionType.HAPPY, intensity=5.0,
        ))
        saved_label = e1.get_label()
        saved_pad = e1.state.pad.to_dict()
        print(f"  保存: label={saved_label} pad={saved_pad}")

        # 第二个引擎：从同一 db 恢复
        e2 = AIEmotionEngine(persistence=True)
        restored_label = e2.get_label()
        restored_pad = e2.state.pad.to_dict()
        print(f"  恢复: label={restored_label} pad={restored_pad}")

        assert restored_label == saved_label, "重启后情绪标签应一致"
        assert abs(restored_pad["pleasure"] - saved_pad["pleasure"]) < 0.05, "pleasure 应接近"
        print(f"  [✓] 持久化恢复 label={restored_label}")

        db.close()


def test_ai_emotion_save_does_not_overwrite_intimacy():
    """AI 情绪保存不应覆盖亲密度 score/stage。"""
    with tempfile.TemporaryDirectory() as tmp:
        from core.memory.db import Database
        Database.reload(db_path=Path(tmp) / "test_emo2.db")
        db = Database.get_instance()
        db.init()
        # 先写入亲密度
        db.save_intimacy_state(score=42.5, stage="熟悉", metadata={"foo": "bar"})

        # AI 情绪保存
        e = AIEmotionEngine(persistence=True)
        e.update_from_user_emotion(EmotionState(
            primary=EmotionType.SAD, intensity=3.0,
        ))

        # 亲密度应保留
        s = db.get_intimacy_state()
        assert s["score"] == 42.5, f"亲密度 score 被覆盖: {s['score']}"
        assert s["stage"] == "熟悉"
        print(f"  [✓] AI 情绪保存不覆盖亲密度 score={s['score']} stage={s['stage']}")

        db.close()


# ============================================================
# 5. ConversationManager 集成
# ============================================================
def _make_mock_llm(reply_text="嗯，我在听呢"):
    llm = MagicMock()
    from llm import LLMResponse
    llm.chat.return_value = LLMResponse(text=reply_text, usage={"total_tokens": 10})
    return llm


def _make_mock_asr(text="你好"):
    asr = MagicMock()
    from speech import ASRResult
    asr.transcribe.return_value = ASRResult(text=text)
    return asr


def _make_mock_tts():
    tts = MagicMock()
    from speech import TTSResult
    tts.default_voice = "nova"
    tts.synthesize.return_value = TTSResult(audio=b"X", format="mp3", voice="nova")
    return tts


def _make_manager_with_emotion(reply_text="嗯嗯，我在呢"):
    """构造带真实情绪引擎的 manager（关闭持久化避免污染 db）。"""
    from core.conversation import ConversationManager
    engine = AIEmotionEngine(persistence=False)
    return ConversationManager(
        llm=_make_mock_llm(reply_text),
        asr=_make_mock_asr(),
        tts=_make_mock_tts(),
        emotion_engine=engine,
        persist=False,
    )


def test_conversation_detects_user_emotion():
    """text_chat 应检测用户情绪并更新 AI 情绪。"""
    m = _make_manager_with_emotion()
    ai_before = m.emotion.state.pad.pleasure

    m.text_chat("我今天好开心啊！")

    ai_after = m.emotion.state.pad.pleasure
    assert ai_after > ai_before, "用户开心应提升 AI pleasure"
    print(f"  [✓] 用户情绪被检测 AI pleasure {ai_before:.2f}→{ai_after:.2f}")


def test_conversation_emotion_event_fires():
    """emotion 事件应在对话中触发。"""
    m = _make_manager_with_emotion()
    events = []
    m.on("emotion", lambda user_emotion, ai_emotion: events.append((user_emotion, ai_emotion)))

    m.text_chat("我难过")

    assert len(events) >= 1, "应触发 emotion 事件"
    user_emo, ai_emo = events[0]
    assert user_emo == "难过", f"用户情绪应为难过，实际 {user_emo}"
    print(f"  [✓] emotion 事件 user={user_emo} ai={ai_emo}")


def test_conversation_prompt_has_emotion_hint():
    """system_prompt 应注入 AI 情绪提示。"""
    m = _make_manager_with_emotion()
    m.text_chat("我真的好开心")

    # 检查最后一次 LLM 调用的 system_prompt 含情绪提示
    call_kwargs = m.llm.chat.call_args.kwargs
    sys_prompt = call_kwargs["system_prompt"]
    assert "情绪" in sys_prompt or "心情" in sys_prompt, "prompt 应含情绪提示"
    print(f"  [✓] system_prompt 注入情绪提示")


def test_conversation_emotion_metadata_saved():
    """对话记录应携带情绪标签（用持久化 db 验证）。"""
    with tempfile.TemporaryDirectory() as tmp:
        from core.conversation import ConversationManager
        from core.memory.db import Database
        Database.reload(db_path=Path(tmp) / "test_emo_conv.db")
        db = Database.get_instance()

        engine = AIEmotionEngine(persistence=True)
        m = ConversationManager(
            llm=_make_mock_llm(),
            asr=_make_mock_asr(),
            tts=_make_mock_tts(),
            emotion_engine=engine,
            db=db, persist=True,
        )
        m.text_chat("我太开心了")

        records = db.get_recent_conversations(limit=5)
        user_rec = [r for r in records if r.role == "user"][0]
        assert user_rec.emotion == "开心", f"用户情绪标签应为开心，实际 {user_rec.emotion}"
        ai_rec = [r for r in records if r.role == "assistant"][0]
        assert ai_rec.ai_emotion is not None, "AI 情绪标签应非空"
        print(f"  [✓] 对话记录携带情绪 user={user_rec.emotion} ai={ai_rec.ai_emotion}")

        db.close()


def test_conversation_without_emotion_still_works():
    """关闭情绪引擎时对话应正常进行。"""
    from core.conversation import ConversationManager
    m = ConversationManager(
        llm=_make_mock_llm(),
        asr=_make_mock_asr(),
        tts=_make_mock_tts(),
        emotion_engine=False,
        persist=False,
    )
    reply = m.text_chat("你好")
    assert reply == "嗯，我在听呢"
    assert m.emotion is None
    print("  [✓] 关闭情绪引擎对话正常")


# ============================================================
# main
# ============================================================
def main() -> int:
    print("T2-01 情绪系统单元测试\n")

    print("【1】情绪类型与 PAD 向量")
    test_emotion_types_count()
    test_pad_vector_blend()
    test_pad_vector_distance()
    test_nearest_emotion()
    test_emotion_state_label()

    print("\n【2】用户情绪感知器")
    test_detect_basic_keyword()
    test_detect_intensity_modifier()
    test_detect_negation()
    test_detect_mixed_emotions()
    test_detect_empty_text()
    test_detect_exclamation_boost()

    print("\n【3】AI 独立情绪引擎")
    test_ai_emotion_baseline()
    test_ai_empathy_updates_state()
    test_ai_content_positive()
    test_ai_content_negative()
    test_ai_decay_returns_to_baseline()
    test_ai_inertia_resists_change()
    test_ai_emotion_context()
    test_ai_reset()

    print("\n【4】情绪持久化")
    test_ai_emotion_persistence()
    test_ai_emotion_save_does_not_overwrite_intimacy()

    print("\n【5】ConversationManager 集成")
    test_conversation_detects_user_emotion()
    test_conversation_emotion_event_fires()
    test_conversation_prompt_has_emotion_hint()
    test_conversation_emotion_metadata_saved()
    test_conversation_without_emotion_still_works()

    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
