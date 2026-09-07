"""T2-03 亲密度系统单元测试。

覆盖：
1. 等级与称呼映射（IntimacyLevel / level_of / address_hint）
2. IntimacyState 状态结构与序列化
3. IntimacyManager 增减规则：
   - on_deep_talk / on_user_fact_mentioned / on_comfort_effective
   - on_hurt（含 severity 映射）
   - on_silence（按天数线性）
   - on_apology / on_daily_chat
   - on_deep_talk_streak（≥5 触发）
4. 特殊规则：
   - 边际递减（高亲密度提升慢）
   - 伤害恢复缓冲（recovery 机制）
   - 下降有缓冲（不会一次掉很多）
5. 持久化（跨"重启"恢复）
6. ConversationManager 集成（伤害触发、闲聊累积、prompt 注入、情绪联动）
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.intimacy import (
    IntimacyLevel,
    IntimacyManager,
    IntimacyState,
    address_hint,
    level_of,
)


# ============================================================
# 1. 等级与称呼映射
# ============================================================
def test_level_mapping():
    """分数 → 等级正确映射。"""
    assert level_of(0) == IntimacyLevel.STRANGER
    assert level_of(20) == IntimacyLevel.STRANGER
    assert level_of(21) == IntimacyLevel.FAMILIAR
    assert level_of(40) == IntimacyLevel.FAMILIAR
    assert level_of(41) == IntimacyLevel.CLOSE
    assert level_of(60) == IntimacyLevel.CLOSE
    assert level_of(61) == IntimacyLevel.INTIMATE
    assert level_of(80) == IntimacyLevel.INTIMATE
    assert level_of(81) == IntimacyLevel.SOULMATE
    assert level_of(100) == IntimacyLevel.SOULMATE
    print("  [✓] 五级映射 0/21/41/61/81 边界正确")


def test_level_clamp():
    """分数超出 0~100 应裁剪。"""
    assert level_of(-10) == IntimacyLevel.STRANGER
    assert level_of(150) == IntimacyLevel.SOULMATE
    print("  [✓] 分数越界裁剪")


def test_address_hint():
    """每级有称呼提示。"""
    for level in IntimacyLevel:
        hint = address_hint(level)
        assert hint, f"{level} 应有称呼提示"
    # 陌生应有"你好"，挚友应有"亲昵"
    assert "你好" in address_hint(IntimacyLevel.STRANGER) or "距离" in address_hint(IntimacyLevel.STRANGER)
    assert "亲昵" in address_hint(IntimacyLevel.SOULMATE)
    print("  [✓] 称呼提示覆盖五级")


# ============================================================
# 2. IntimacyState
# ============================================================
def test_state_serialization():
    """to_dict/from_dict 序列化。"""
    s = IntimacyState(score=42.5, recovery=0.7, deep_talk_streak=3)
    d = s.to_dict()
    assert d["score"] == 42.5
    assert d["level"] == "亲近"
    assert d["recovery"] == 0.7
    assert d["deep_talk_streak"] == 3
    # 反序列化
    s2 = IntimacyState.from_dict(d)
    assert s2.score == 42.5
    assert s2.recovery == 0.7
    assert s2.deep_talk_streak == 3
    print("  [✓] IntimacyState 序列化/反序列化")


def test_state_level_property():
    """level 属性自动从 score 推导。"""
    s = IntimacyState(score=50)
    assert s.level == IntimacyLevel.CLOSE
    s.score = 75
    assert s.level == IntimacyLevel.INTIMATE
    print("  [✓] level 属性随 score 动态变化")


# ============================================================
# 3. IntimacyManager 增减规则
# ============================================================
def make_manager(persistence: bool = False) -> IntimacyManager:
    """构造测试用 IntimacyManager（默认关闭持久化）。"""
    return IntimacyManager(persistence=persistence)


def test_daily_chat_gain():
    """日常闲聊 +0.5（边际递减后实际略少）。"""
    im = make_manager()
    before = im.get_score()
    im.on_daily_chat()
    after = im.get_score()
    assert after > before, "闲聊应提升亲密度"
    # 初始 5.0，边际因子 ≈ 1-(5/100)^1.5 ≈ 0.89，recovery=1.0
    # 实际增益 ≈ 0.5 * 0.89 = 0.445
    gain = after - before
    assert 0.1 < gain < 0.5, f"闲聊增益应在 0.3 左右，实际 {gain}"
    print(f"  [✓] 闲聊 +{gain:.3f}")


def test_deep_talk_gain():
    """深度交流 +2。"""
    im = make_manager()
    before = im.get_score()
    im.on_deep_talk()
    after = im.get_score()
    assert after > before
    print(f"  [✓] 深度交流 +{after-before:.3f}")


def test_user_fact_mentioned_gain():
    """记住用户事项 +3。"""
    im = make_manager()
    before = im.get_score()
    im.on_user_fact_mentioned()
    after = im.get_score()
    assert after > before
    print(f"  [✓] 提及用户事项 +{after-before:.3f}")


def test_comfort_effective_gain():
    """有效安慰 +2。"""
    im = make_manager()
    before = im.get_score()
    im.on_comfort_effective()
    after = im.get_score()
    assert after > before
    print(f"  [✓] 有效安慰 +{after-before:.3f}")


def test_hurt_loss():
    """伤害性话语 -5~-15（含 severity 映射）。"""
    im = make_manager()
    im.state.score = 30.0  # 拉高便于观察下降
    before = im.get_score()
    # severity=0.5 → 介于 5~15 之间
    im.on_hurt(severity=0.5)
    after = im.get_score()
    assert after < before, "伤害应降低亲密度"
    loss = before - after
    # loss = (5 + 5) * 0.8 = 8
    assert 4 <= loss <= 16, f"伤害扣分应在 5~15 范围，实际 {loss}"
    print(f"  [✓] 伤害 severity=0.5 → -{loss:.3f}")


def test_hurt_severity_scaling():
    """severity 越高扣分越多。"""
    im_low = make_manager()
    im_low.state.score = 50.0
    im_low.on_hurt(severity=0.2)
    loss_low = 50.0 - im_low.get_score()

    im_high = make_manager()
    im_high.state.score = 50.0
    im_high.on_hurt(severity=0.9)
    loss_high = 50.0 - im_high.get_score()

    assert loss_high > loss_low, "高 severity 应扣分更多"
    print(f"  [✓] severity 映射 0.2→-{loss_low:.2f} < 0.9→-{loss_high:.2f}")


def test_silence_loss():
    """沉默扣分按天数线性累积。"""
    im = make_manager()
    im.state.score = 30.0
    before = im.get_score()
    im.on_silence(days=3)
    after = im.get_score()
    assert after < before, "沉默应降低亲密度"
    print(f"  [✓] 沉默 3 天 → -{before-after:.3f}")


def test_silence_short_no_effect():
    """沉默 <1 天不扣分。"""
    im = make_manager()
    before = im.get_score()
    im.on_silence(days=0.5)
    assert im.get_score() == before, "0.5 天不应扣分"
    print("  [✓] 短时间沉默不扣分")


def test_apology_gain():
    """真诚道歉 +3 且快速恢复 recovery。"""
    im = make_manager()
    # 先被伤害降低 recovery
    im.on_hurt(severity=0.8)
    assert im.state.recovery < 0.5
    before = im.get_score()
    im.on_apology()
    after = im.get_score()
    assert after > before, "道歉应提升亲密度"
    assert im.state.recovery >= 0.7, "道歉应快速恢复 recovery"
    print(f"  [✓] 道歉 +{after-before:.3f} recovery→{im.state.recovery:.2f}")


def test_deep_talk_streak():
    """连续深度对话 ≥5 轮触发 deep_talk 增益。"""
    im = make_manager()
    before = im.get_score()
    # 累积 4 轮，不应触发
    for _ in range(4):
        im.on_deep_talk_streak(turn_count=1)
    mid = im.get_score()
    assert mid == before, "4 轮不应触发增益"
    # 第 5 轮触发
    im.on_deep_talk_streak(turn_count=1)
    after = im.get_score()
    assert after > before, "第 5 轮应触发 deep_talk 增益"
    print(f"  [✓] 连续 5 轮触发深度交流 +{after-before:.3f}")


# ============================================================
# 4. 特殊规则
# ============================================================
def test_diminishing_returns():
    """边际递减：高亲密度时正向增益更小。"""
    # 低亲密度
    im_low = make_manager()
    im_low.state.score = 5.0
    im_low.on_daily_chat()
    gain_low = im_low.get_score() - 5.0

    # 高亲密度
    im_high = make_manager()
    im_high.state.score = 90.0
    im_high.on_daily_chat()
    gain_high = im_high.get_score() - 90.0

    assert gain_high < gain_low, (
        f"高亲密度增益 {gain_high} 应小于低亲密度 {gain_low}"
    )
    print(f"  [✓] 边际递减 低亲密度 +{gain_low:.3f} > 高亲密度 +{gain_high:.3f}")


def test_hurt_recovery_buffer():
    """伤害后正向增益被削弱，直到 recovery 回升。"""
    im = make_manager()
    im.state.score = 30.0
    # 被伤害
    im.on_hurt(severity=0.8)
    assert im.state.recovery < 0.5, "伤害应降低 recovery"

    # 伤害后闲聊增益应被削弱
    score_after_hurt = im.get_score()
    im.on_daily_chat()
    gain_after_hurt = im.get_score() - score_after_hurt

    # 对照组：无伤害时闲聊增益
    im2 = make_manager()
    im2.state.score = score_after_hurt
    im2.on_daily_chat()
    gain_normal = im2.get_score() - score_after_hurt

    assert gain_after_hurt < gain_normal, (
        f"伤害后增益 {gain_after_hurt} 应小于正常 {gain_normal}"
    )
    print(f"  [✓] 伤害恢复缓冲 伤害后+{gain_after_hurt:.3f} < 正常+{gain_normal:.3f}")


def test_loss_buffer():
    """下降有缓冲：扣分为基础 80%。"""
    im = make_manager()
    im.state.score = 50.0
    # severity=0 → hurt_loss_min=5
    im.on_hurt(severity=0.0)
    # 实际扣分 = 5 * 0.8 = 4
    loss = 50.0 - im.get_score()
    assert abs(loss - 4.0) < 0.01, f"缓冲后应扣 4.0，实际 {loss}"
    print(f"  [✓] 下降缓冲 5*0.8=4.0 → 实际-{loss:.2f}")


def test_score_bounds():
    """分数不超出 0~100。"""
    im = make_manager()
    # 大量伤害不应低于 0
    for _ in range(20):
        im.on_hurt(severity=1.0)
    assert im.get_score() >= 0.0, "分数不应低于 0"
    # 大量增益不应超 100
    im2 = make_manager()
    for _ in range(100):
        im2.state.recovery = 1.0
        im2.on_user_fact_mentioned()
    assert im2.get_score() <= 100.0, "分数不应超 100"
    print(f"  [✓] 分数边界 [0, 100] 实际范围正常")


# ============================================================
# 5. 持久化
# ============================================================
def test_persistence_restore():
    """亲密度状态保存后，新实例能恢复。"""
    with tempfile.TemporaryDirectory() as tmp:
        import core.memory
        import core.memory.db as db_module
        from core.memory.db import Database
        Database.reload(db_path=Path(tmp) / "test_inti.db")
        db = Database.get_instance()
        db.init()
        # 关键：同步更新两处模块级单例（core.memory.db.db 和 core.memory.db）
        db_module.db = db
        core.memory.db = db
        # 重置 IntimacyManager 全局单例，确保用新 db
        import core.intimacy as inti_mod
        inti_mod._singleton = None

        im1 = IntimacyManager(persistence=True)
        im1.on_user_fact_mentioned()  # +3
        im1.on_hurt(severity=0.5)
        saved_score = im1.get_score()
        saved_recovery = im1.state.recovery
        print(f"  保存: score={saved_score} recovery={saved_recovery}")

        # 重置全局单例 + _db_ready，模拟"重启"
        inti_mod._singleton = None
        im2 = IntimacyManager(persistence=True)
        restored_score = im2.get_score()
        restored_recovery = im2.state.recovery
        print(f"  恢复: score={restored_score} recovery={restored_recovery}")
        assert abs(restored_score - saved_score) < 0.1, "score 应跨重启恢复"
        assert abs(restored_recovery - saved_recovery) < 0.05, "recovery 应恢复"
        print(f"  [✓] 持久化恢复 score={restored_score:.2f}")

        db.close()


def test_persistence_does_not_overwrite_ai_emotion():
    """亲密度保存不应覆盖 AI 情绪字段。"""
    with tempfile.TemporaryDirectory() as tmp:
        import core.memory
        import core.memory.db as db_module
        from core.memory.db import Database
        Database.reload(db_path=Path(tmp) / "test_inti2.db")
        db = Database.get_instance()
        db.init()
        db_module.db = db
        core.memory.db = db
        import core.intimacy as inti_mod
        inti_mod._singleton = None
        # 先写入 ai_emotion
        import json
        db.save_intimacy_state(
            score=10.0, stage="陌生",
            metadata={"ai_emotion": {"pad": {"pleasure": 0.5}}},
        )

        im = IntimacyManager(persistence=True)
        im.on_daily_chat()

        # ai_emotion 应保留
        row = db.conn.execute(
            "SELECT metadata_json FROM intimacy_state WHERE id = 1"
        ).fetchone()
        meta = json.loads(row["metadata_json"])
        assert "ai_emotion" in meta, "ai_emotion 应保留"
        assert meta["ai_emotion"]["pad"]["pleasure"] == 0.5
        assert "intimacy" in meta, "亲密度扩展字段应写入"
        print("  [✓] 亲密度保存不覆盖 ai_emotion")

        db.close()


# ============================================================
# 6. ConversationManager 集成
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


def _make_manager_with_intimacy(reply_text="嗯嗯，我在呢"):
    """构造带真实亲密度管理器的 manager（关闭持久化）。"""
    from core.conversation import ConversationManager
    im = IntimacyManager(persistence=False)
    return ConversationManager(
        llm=_make_mock_llm(reply_text),
        asr=_make_mock_asr(),
        tts=_make_mock_tts(),
        emotion_engine=False,
        intimacy_manager=im,
        persist=False,
    )


def test_conversation_daily_chat_increases_intimacy():
    """对话应触发日常闲聊 +0.5。"""
    m = _make_manager_with_intimacy()
    before = m.intimacy.get_score()
    m.text_chat("你好啊")
    after = m.intimacy.get_score()
    assert after > before, "对话应提升亲密度"
    print(f"  [✓] 对话触发闲聊 +{after-before:.3f}")


def test_conversation_hurt_decreases_intimacy():
    """伤害性话语应降低亲密度。"""
    m = _make_manager_with_intimacy()
    m.intimacy.state.score = 30.0
    before = m.intimacy.get_score()
    m.text_chat("你真笨，讨厌你")
    after = m.intimacy.get_score()
    assert after < before, "伤害性话语应降低亲密度"
    print(f"  [✓] 伤害性话语 -{before-after:.3f}")


def test_conversation_intimacy_event_fires():
    """intimacy 事件应触发。"""
    m = _make_manager_with_intimacy()
    events = []
    m.on("intimacy", lambda score, level: events.append((score, level)))
    m.text_chat("你好")
    assert len(events) >= 1, "应触发 intimacy 事件"
    print(f"  [✓] intimacy 事件触发 level={events[0][1]}")


def test_conversation_prompt_has_intimacy_hint():
    """system_prompt 应注入亲密度提示。"""
    m = _make_manager_with_intimacy()
    m.text_chat("你好")
    sys_prompt = m.llm.chat.call_args.kwargs["system_prompt"]
    assert "亲密度" in sys_prompt or "关系阶段" in sys_prompt
    print("  [✓] system_prompt 注入亲密度提示")


def test_conversation_emotion_intimacy_sync():
    """亲密度变化应同步到情绪引擎（empathy_multiplier 变化）。"""
    from core.conversation import ConversationManager
    from core.emotion import AIEmotionEngine
    engine = AIEmotionEngine(persistence=False)
    im = IntimacyManager(persistence=False)
    m = ConversationManager(
        llm=_make_mock_llm(),
        asr=_make_mock_asr(),
        tts=_make_mock_tts(),
        emotion_engine=engine,
        intimacy_manager=im,
        persist=False,
    )
    # 初始亲密度低
    assert engine._intimacy_score == im.get_score()
    # 提升亲密度
    im.state.score = 80.0
    m.text_chat("你好")  # 对话中会同步
    assert abs(engine._intimacy_score - 80.0) < 1.0, "亲密度应同步到情绪引擎"
    print(f"  [✓] 亲密度同步到情绪引擎 score={engine._intimacy_score}")


def test_conversation_without_intimacy_still_works():
    """关闭亲密度时对话应正常进行。"""
    from core.conversation import ConversationManager
    m = ConversationManager(
        llm=_make_mock_llm(),
        asr=_make_mock_asr(),
        tts=_make_mock_tts(),
        emotion_engine=False,
        intimacy_manager=False,
        persist=False,
    )
    reply = m.text_chat("你好")
    assert reply == "嗯，我在听呢"
    assert m.intimacy is None
    print("  [✓] 关闭亲密度对话正常")


def test_conversation_intimacy_metadata_saved():
    """对话记录应携带亲密度分数。"""
    with tempfile.TemporaryDirectory() as tmp:
        from core.conversation import ConversationManager
        import core.memory
        import core.memory.db as db_module
        from core.memory.db import Database
        Database.reload(db_path=Path(tmp) / "test_inti_conv.db")
        db = Database.get_instance()
        db.init()
        db_module.db = db
        core.memory.db = db
        import core.intimacy as inti_mod
        inti_mod._singleton = None

        im = IntimacyManager(persistence=True)
        m = ConversationManager(
            llm=_make_mock_llm(),
            asr=_make_mock_asr(),
            tts=_make_mock_tts(),
            emotion_engine=False,
            intimacy_manager=im,
            db=db, persist=True,
        )
        m.text_chat("你好啊")

        records = db.get_recent_conversations(limit=5)
        user_rec = [r for r in records if r.role == "user"][0]
        assert user_rec.intimacy_score is not None, "对话记录应携带亲密度"
        print(f"  [✓] 对话记录携带亲密度 score={user_rec.intimacy_score}")

        db.close()


# ============================================================
# main
# ============================================================
def main() -> int:
    print("T2-03 亲密度系统单元测试\n")

    print("【1】等级与称呼映射")
    test_level_mapping()
    test_level_clamp()
    test_address_hint()

    print("\n【2】IntimacyState 状态结构")
    test_state_serialization()
    test_state_level_property()

    print("\n【3】增减规则")
    test_daily_chat_gain()
    test_deep_talk_gain()
    test_user_fact_mentioned_gain()
    test_comfort_effective_gain()
    test_hurt_loss()
    test_hurt_severity_scaling()
    test_silence_loss()
    test_silence_short_no_effect()
    test_apology_gain()
    test_deep_talk_streak()

    print("\n【4】特殊规则")
    test_diminishing_returns()
    test_hurt_recovery_buffer()
    test_loss_buffer()
    test_score_bounds()

    print("\n【5】持久化")
    test_persistence_restore()
    test_persistence_does_not_overwrite_ai_emotion()

    print("\n【6】ConversationManager 集成")
    test_conversation_daily_chat_increases_intimacy()
    test_conversation_hurt_decreases_intimacy()
    test_conversation_intimacy_event_fires()
    test_conversation_prompt_has_intimacy_hint()
    test_conversation_emotion_intimacy_sync()
    test_conversation_without_intimacy_still_works()
    test_conversation_intimacy_metadata_saved()

    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
