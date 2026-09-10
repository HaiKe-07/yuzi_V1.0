"""T2-07 主动话题系统单元测试。

覆盖：
1. 触发条件检测：静音/早晨/深夜/随机
2. 冷却机制：同类型触发有最小间隔
3. 话题去重：轮换使用避免重复
4. 亲密度分档：低/中/高 不同话题
5. 记忆增强：随机分享引用长期记忆
6. ConversationManager 集成：text_chat 重置计时器
7. check_proactive 返回话题文本
"""
from __future__ import annotations

import random
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.proactive import (
    ProactiveManager, ProactiveMessage, ProactiveTrigger,
    _intimacy_tier, _COOLDOWN,
)


# ============================================================
# 工具
# ============================================================
def _make_manager(
    intimacy_manager=None,
    memory_manager=None,
    silence_threshold: float = 21600,
    morning_hour: int = 8,
    night_hour: int = 23,
    random_prob: float = 0.05,
) -> ProactiveManager:
    """构造测试用 ProactiveManager。"""
    pm = ProactiveManager(
        intimacy_manager=intimacy_manager,
        memory_manager=memory_manager,
    )
    pm.silence_threshold = silence_threshold
    pm.morning_hour = morning_hour
    pm.night_hour = night_hour
    pm.random_prob = random_prob
    return pm


def _timestamp(hour: int, minute: int = 0) -> float:
    """构造指定当天指定时间的 timestamp。"""
    now = datetime.now()
    dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt.timestamp()


# ============================================================
# 1. 亲密度分档
# ============================================================
def test_intimacy_tier_low():
    assert _intimacy_tier(5) == "low"
    assert _intimacy_tier(30) == "low"
    print("  [✓] 低亲密度 → low")


def test_intimacy_tier_mid():
    assert _intimacy_tier(50) == "mid"
    assert _intimacy_tier(60) == "mid"
    print("  [✓] 中亲密度 → mid")


def test_intimacy_tier_high():
    assert _intimacy_tier(70) == "high"
    assert _intimacy_tier(90) == "high"
    print("  [✓] 高亲密度 → high")


# ============================================================
# 2. 静音触发
# ============================================================
def test_silence_triggers():
    """超过阈值未互动应触发静音话题。"""
    pm = _make_manager(silence_threshold=100)
    # 模拟 200 秒未互动
    pm._last_interaction_at = time.time() - 200
    # 用非早晨/非深夜时段避免触发其他条件
    now = _timestamp(14)
    msg = pm.check(intimacy_score=5, now=now)
    assert msg is not None
    assert msg.trigger == ProactiveTrigger.SILENCE
    assert len(msg.text) > 0
    print(f"  [✓] 静音触发: {msg.text[:30]}")


def test_silence_not_triggered_within_threshold():
    """未超过阈值不应触发。"""
    pm = _make_manager(silence_threshold=1000)
    pm._last_interaction_at = time.time()  # 刚互动
    # 用 14 点（非早晨 7-10、非深夜 23-2）
    now = _timestamp(14)
    # 禁用随机以避免干扰
    pm.random_prob = 0
    msg = pm.check(intimacy_score=5, now=now)
    # 静音不应触发，其他条件也不应（非早晨/深夜）
    assert msg is None, f"不应触发，实际 {msg.trigger.value if msg else None}"
    print("  [✓] 未超阈值不触发")


# ============================================================
# 3. 早晨问候
# ============================================================
def test_morning_triggers():
    """早晨时段应触发早安问候。"""
    pm = _make_manager(morning_hour=8)
    # 早晨 8 点
    now = _timestamp(8)
    # 重置静音（刚互动）
    pm._last_interaction_at = now
    pm.random_prob = 0  # 禁用随机
    msg = pm.check(intimacy_score=5, now=now)
    assert msg is not None
    assert msg.trigger == ProactiveTrigger.MORNING
    assert "早" in msg.text
    print(f"  [✓] 早晨问候: {msg.text[:30]}")


def test_morning_not_triggered_afternoon():
    """下午不应触发早晨问候。"""
    pm = _make_manager(morning_hour=8)
    now = _timestamp(14)  # 下午 2 点
    pm._last_interaction_at = now  # 禁用静音
    pm.random_prob = 0
    msg = pm.check(intimacy_score=5, now=now)
    # 不应触发（非早晨/非深夜/非静音/无随机）
    assert msg is None
    print("  [✓] 下午不触发早晨问候")


# ============================================================
# 4. 深夜提醒
# ============================================================
def test_night_triggers():
    """深夜时段应触发晚安提醒。"""
    pm = _make_manager(night_hour=23)
    now = _timestamp(23, 30)  # 晚上 11:30
    pm._last_interaction_at = now  # 禁用静音
    pm.random_prob = 0
    msg = pm.check(intimacy_score=5, now=now)
    assert msg is not None
    assert msg.trigger == ProactiveTrigger.NIGHT
    print(f"  [✓] 深夜提醒: {msg.text[:30]}")


def test_night_not_triggered_morning():
    """早晨不应触发深夜提醒。"""
    pm = _make_manager(night_hour=23)
    now = _timestamp(8)  # 早上 8 点
    pm._last_interaction_at = now
    pm.random_prob = 0
    # 早晨时段会先触发 MORNING
    msg = pm.check(intimacy_score=5, now=now)
    if msg:
        assert msg.trigger != ProactiveTrigger.NIGHT
    print("  [✓] 早晨不触发深夜提醒")


# ============================================================
# 5. 随机分享
# ============================================================
def test_random_triggers():
    """random_prob=1.0 应总是触发随机分享。"""
    pm = _make_manager(random_prob=1.0)
    now = _timestamp(14)  # 非早晨/深夜
    pm._last_interaction_at = now  # 禁用静音
    msg = pm.check(intimacy_score=5, now=now)
    assert msg is not None
    assert msg.trigger == ProactiveTrigger.RANDOM
    print(f"  [✓] 随机分享: {msg.text[:30]}")


def test_random_zero_never_triggers():
    """random_prob=0.0 永不触发随机分享。"""
    pm = _make_manager(random_prob=0.0)
    now = _timestamp(14)
    pm._last_interaction_at = now
    msg = pm.check(intimacy_score=5, now=now)
    assert msg is None
    print("  [✓] random=0 永不触发")


# ============================================================
# 6. 冷却机制
# ============================================================
def test_cooldown_prevents_retrigger():
    """冷却期内同类型不应重复触发。"""
    pm = _make_manager(silence_threshold=100, random_prob=0)
    now = _timestamp(14)
    pm._last_interaction_at = now - 200  # 触发静音
    # 第一次触发
    msg1 = pm.check(intimacy_score=5, now=now)
    assert msg1 is not None
    # 立即再查，应被冷却挡住
    msg2 = pm.check(intimacy_score=5, now=now + 10)
    assert msg2 is None
    print("  [✓] 冷却期内不重复触发")


def test_cooldown_expires():
    """冷却期过后可再次触发。"""
    pm = _make_manager(silence_threshold=100, random_prob=0)
    now = _timestamp(14)
    pm._last_interaction_at = now - 200
    # 第一次触发
    msg1 = pm.check(intimacy_score=5, now=now)
    assert msg1 is not None
    # 过冷却期后
    cooldown = _COOLDOWN[ProactiveTrigger.SILENCE]
    msg2 = pm.check(intimacy_score=5, now=now + cooldown + 100)
    # 应再次触发（静音时间已过冷却）
    assert msg2 is not None
    print(f"  [✓] 冷却期过后可再触发 ({cooldown}s)")


def test_different_types_independent_cooldown():
    """不同触发类型冷却独立。"""
    pm = _make_manager(morning_hour=8, night_hour=23, random_prob=0)
    # 早晨 8 点，刚互动（禁静音）
    now = _timestamp(8)
    pm._last_interaction_at = now
    # 触发早晨
    msg1 = pm.check(intimacy_score=5, now=now)
    assert msg1 is not None and msg1.trigger == ProactiveTrigger.MORNING
    # 同时间再查，早晨被冷却，但其他不触发
    msg2 = pm.check(intimacy_score=5, now=now + 60)
    assert msg2 is None  # 早晨冷却，其他不触发
    print("  [✓] 不同类型冷却独立")


# ============================================================
# 7. 话题去重
# ============================================================
def test_topic_dedup():
    """连续触发不应立即重复同一话题。"""
    pm = _make_manager(silence_threshold=0, random_prob=0)
    now = _timestamp(14)
    pm._last_interaction_at = now  # 禁静音
    # 触发多次，检查去重
    used = []
    for i in range(10):
        pm._last_interaction_at = now - 999999  # 强制静音
        msg = pm.check(intimacy_score=5, now=now + i * 4000)
        if msg:
            used.append(msg.text)
    # 不应全部相同
    unique = set(used)
    assert len(unique) >= 2, f"话题应去重，实际 {len(unique)} 种"
    print(f"  [✓] 话题去重: {len(used)} 次触发 {len(unique)} 种不同话题")


# ============================================================
# 8. 亲密度分档话题
# ============================================================
def test_low_intimacy_topics():
    """低亲密度应使用礼貌话题。"""
    pm = _make_manager(silence_threshold=100, random_prob=0)
    now = _timestamp(14)
    pm._last_interaction_at = now - 200
    msg = pm.check(intimacy_score=10, now=now)
    assert msg is not None
    # 低亲密度话题应偏礼貌（不含"想你""亲爱的"等亲昵词）
    assert "亲爱的" not in msg.text
    print(f"  [✓] 低亲密度话题: {msg.text[:30]}")


def test_high_intimacy_topics():
    """高亲密度应使用亲昵话题。"""
    pm = _make_manager(silence_threshold=100, random_prob=0)
    now = _timestamp(14)
    pm._last_interaction_at = now - 200
    # 触发多次看是否有亲昵词
    found_intimate = False
    for i in range(10):
        pm._last_interaction_at = now - 999999
        msg = pm.check(intimacy_score=90, now=now + i * 4000)
        if msg and any(w in msg.text for w in ["想你", "亲爱的", "心疼"]):
            found_intimate = True
            break
    assert found_intimate, "高亲密度应出现亲昵话题"
    print(f"  [✓] 高亲密度话题含亲昵词: {msg.text[:30]}")


# ============================================================
# 9. 记忆增强
# ============================================================
def test_memory_enhanced_random():
    """随机分享可引用长期记忆。"""
    mock_mem = MagicMock()
    from core.memory.manager import Memory, MemoryType
    mock_mem.all_memories.return_value = [
        Memory(id=1, type=MemoryType.INTEREST, content="科幻电影",
               importance=4),
    ]
    pm = _make_manager(
        memory_manager=mock_mem, random_prob=1.0,
    )
    now = _timestamp(14)
    pm._last_interaction_at = now  # 禁静音
    # 多次触发随机分享，至少有一次引用记忆
    found_enhanced = False
    for i in range(20):
        pm._last_trigger_at[ProactiveTrigger.RANDOM] = 0  # 重置冷却
        msg = pm.check(intimacy_score=5, now=now + i * 8000)
        if msg and "科幻" in msg.text:
            found_enhanced = True
            break
    assert found_enhanced, "应至少有一次引用记忆的随机分享"
    print(f"  [✓] 记忆增强: {msg.text[:40]}")


def test_memory_enhanced_no_memories():
    """无记忆时随机分享用普通话题。"""
    mock_mem = MagicMock()
    mock_mem.all_memories.return_value = []
    pm = _make_manager(memory_manager=mock_mem, random_prob=1.0)
    now = _timestamp(14)
    pm._last_interaction_at = now
    msg = pm.check(intimacy_score=5, now=now)
    assert msg is not None
    assert len(msg.text) > 0
    print(f"  [✓] 无记忆时用普通话题: {msg.text[:30]}")


# ============================================================
# 10. 互动时间跟踪
# ============================================================
def test_on_user_interaction_resets_timer():
    """用户互动应重置静音计时器。"""
    pm = _make_manager(silence_threshold=100)
    pm._last_interaction_at = time.time() - 200  # 超阈值
    assert pm.silence_duration > 100
    pm.on_user_interaction()
    assert pm.silence_duration < 1
    print("  [✓] 用户互动重置计时器")


# ============================================================
# 11. ConversationManager 集成
# ============================================================
def _make_mock_llm(reply_text="嗯，我在呢"):
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
    tts.default_voice = "zh_female_wanwan"
    tts.synthesize.return_value = TTSResult(
        audio=b"AUDIO", format="mp3", voice="zh_female_wanwan",
    )
    return tts


def _make_manager_with_proactive(tmpdir):
    """构造带主动话题的 ConversationManager。"""
    import core.proactive as proactive_module

    pm = ProactiveManager()
    pm.silence_threshold = 100  # 测试用短阈值
    pm.random_prob = 0  # 禁用随机

    from core.conversation import ConversationManager
    return ConversationManager(
        llm=_make_mock_llm(),
        asr=_make_mock_asr(),
        tts=_make_mock_tts(),
        emotion_engine=False,
        intimacy_manager=False,
        memory_manager=False,
        proactive_manager=pm,
        persist=False,
    )


def test_conversation_resets_proactive_timer():
    """text_chat 后应重置主动话题计时器。"""
    with __import__("tempfile").TemporaryDirectory() as tmp:
        m = _make_manager_with_proactive(tmp)
        # 模拟长时间未互动
        m.proactive._last_interaction_at = time.time() - 999
        # 用户对话
        m.text_chat("你好呀")
        # 计时器应已重置
        assert m.proactive.silence_duration < 1
        print("  [✓] text_chat 重置主动话题计时器")


def test_check_proactive_returns_text():
    """check_proactive 应返回话题文本。"""
    with __import__("tempfile").TemporaryDirectory() as tmp:
        m = _make_manager_with_proactive(tmp)
        # 模拟长时间未互动（触发静音）
        m.proactive._last_interaction_at = time.time() - 999
        now = _timestamp(14)  # 非早晨/深夜
        text = m.check_proactive()
        assert text is not None
        assert len(text) > 0
        print(f"  [✓] check_proactive 返回: {text[:30]}")


def test_check_proactive_none_when_disabled():
    """主动话题关闭时 check_proactive 返回 None。"""
    from core.conversation import ConversationManager
    m = ConversationManager(
        llm=_make_mock_llm(),
        asr=_make_mock_asr(),
        tts=_make_mock_tts(),
        emotion_engine=False,
        intimacy_manager=False,
        memory_manager=False,
        proactive_manager=False,
        persist=False,
    )
    assert m.check_proactive() is None
    print("  [✓] 关闭时返回 None")


def test_status_includes_proactive():
    """status 应含主动话题状态。"""
    with __import__("tempfile").TemporaryDirectory() as tmp:
        m = _make_manager_with_proactive(tmp)
        s = m.status()
        assert "proactive" in s
        assert s["proactive"] == "on"
        assert "proactive_silence" in s
        print(f"  [✓] status 含 proactive: {s['proactive']} silence={s['proactive_silence']}")


# ============================================================
# 12. enabled 开关
# ============================================================
def test_disabled_returns_none():
    """enabled=False 时 check 返回 None。"""
    pm = _make_manager()
    pm.enabled = False
    pm._last_interaction_at = time.time() - 999999  # 超长静音
    now = _timestamp(14)
    assert pm.check(intimacy_score=5, now=now) is None
    print("  [✓] disabled 时返回 None")


# ============================================================
# 13. 触发优先级
# ============================================================
def test_morning_priority_over_silence():
    """早晨问候优先级高于静音（同时满足时早晨先触发）。"""
    pm = _make_manager(morning_hour=8, silence_threshold=100, random_prob=0)
    now = _timestamp(8)  # 早晨 8 点
    pm._last_interaction_at = now - 200  # 同时满足静音
    msg = pm.check(intimacy_score=5, now=now)
    assert msg is not None
    # 早晨优先于静音
    assert msg.trigger == ProactiveTrigger.MORNING
    print(f"  [✓] 早晨优先于静音: {msg.trigger.value}")


def test_night_priority_over_silence():
    """深夜提醒优先级高于静音。"""
    pm = _make_manager(night_hour=23, silence_threshold=100, random_prob=0)
    now = _timestamp(23, 30)  # 深夜
    pm._last_interaction_at = now - 200
    msg = pm.check(intimacy_score=5, now=now)
    assert msg is not None
    # 深夜优先于静音
    assert msg.trigger == ProactiveTrigger.NIGHT
    print(f"  [✓] 深夜优先于静音: {msg.trigger.value}")


# ============================================================
# main
# ============================================================
def main() -> int:
    print("T2-07 主动话题系统单元测试\n")

    print("【1】亲密度分档")
    test_intimacy_tier_low()
    test_intimacy_tier_mid()
    test_intimacy_tier_high()

    print("\n【2】静音触发")
    test_silence_triggers()
    test_silence_not_triggered_within_threshold()

    print("\n【3】早晨问候")
    test_morning_triggers()
    test_morning_not_triggered_afternoon()

    print("\n【4】深夜提醒")
    test_night_triggers()
    test_night_not_triggered_morning()

    print("\n【5】随机分享")
    test_random_triggers()
    test_random_zero_never_triggers()

    print("\n【6】冷却机制")
    test_cooldown_prevents_retrigger()
    test_cooldown_expires()
    test_different_types_independent_cooldown()

    print("\n【7】话题去重")
    test_topic_dedup()

    print("\n【8】亲密度分档话题")
    test_low_intimacy_topics()
    test_high_intimacy_topics()

    print("\n【9】记忆增强")
    test_memory_enhanced_random()
    test_memory_enhanced_no_memories()

    print("\n【10】互动时间跟踪")
    test_on_user_interaction_resets_timer()

    print("\n【11】ConversationManager 集成")
    test_conversation_resets_proactive_timer()
    test_check_proactive_returns_text()
    test_check_proactive_none_when_disabled()
    test_status_includes_proactive()

    print("\n【12】enabled 开关")
    test_disabled_returns_none()

    print("\n【13】触发优先级")
    test_morning_priority_over_silence()
    test_night_priority_over_silence()

    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
