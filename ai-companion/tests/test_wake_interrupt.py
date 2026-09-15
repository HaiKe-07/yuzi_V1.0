"""T2-08 语音唤醒与打断单元测试。

覆盖：
1. 唤醒词检测：
   - 文本关键词检测（命中/未命中/别名/空关键词）
   - 能量 VAD 检测（高能量音频触发/静音不触发）
   - 工厂函数选择正确后端
   - 回调触发（fire_text / _fire）
2. 打断检测：
   - check_chunk：高能量音频触发/静音不触发/累积达到阈值
   - 冷却机制：冷却期内不重复触发
   - start/stop_monitoring 状态
3. ConversationManager 集成：
   - check_wake_word 文本模式触发
   - interrupt() 手动打断
   - speak() 期间打断监听启停
   - status() 包含 wake/interrupt 字段
   - 唤醒词/打断可注入 False 关闭
"""
from __future__ import annotations

import io
import struct
import sys
import time
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from speech.wake_word import (
    BaseWakeWordDetector,
    EnergyVADWakeDetector,
    TextWakeWordDetector,
    get_default_wake_word_detector,
    reset_wake_word_detector,
)
from speech.interruption import (
    InterruptionDetector,
    get_interruption_detector,
    reset_interruption_detector,
)


# ============================================================
# 工具：构造音频
# ============================================================
def _make_wav(samples: list[int] | bytes, sample_rate: int = 16000,
              channels: int = 1, sample_width: int = 2) -> bytes:
    """把 PCM 样本打包成 WAV 字节。"""
    if isinstance(samples, list):
        pcm = struct.pack(f"<{len(samples)}h", *samples)
    else:
        pcm = samples
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _silence_wav(duration_sec: float = 0.5, sample_rate: int = 16000) -> bytes:
    """构造静音 WAV（全 0 样本）。"""
    n = int(sample_rate * duration_sec)
    return _make_wav([0] * n, sample_rate)


def _loud_wav(duration_sec: float = 0.5, sample_rate: int = 16000,
              amplitude: int = 20000) -> bytes:
    """构造高能量 WAV（方波，模拟说话）。"""
    n = int(sample_rate * duration_sec)
    # 方波：正负交替，模拟语音能量
    samples = [amplitude if i % 4 < 2 else -amplitude for i in range(n)]
    return _make_wav(samples, sample_rate)


# ============================================================
# 1. 文本唤醒词检测
# ============================================================
def test_text_wake_keyword_match():
    """关键词在文本中应触发。"""
    det = TextWakeWordDetector(keyword="小爱")
    assert det.detect_text("小爱同学，今天天气怎么样") is True
    print("  [✓] 关键词匹配触发")


def test_text_wake_keyword_no_match():
    """无关键词不应触发。"""
    det = TextWakeWordDetector(keyword="小爱")
    assert det.detect_text("今天天气怎么样") is False
    print("  [✓] 无关键词不触发")


def test_text_wake_alias():
    """别名也应触发。"""
    det = TextWakeWordDetector(keyword="小爱", aliases=["同学", "你好"])
    assert det.detect_text("同学，帮我查天气") is True
    assert det.detect_text("你好呀") is True
    print("  [✓] 别名触发")


def test_text_wake_empty_keyword_triggers_all():
    """空关键词（未配置）时所有文本视为唤醒。"""
    det = TextWakeWordDetector(keyword="")
    assert det.detect_text("随便说点什么") is True
    print("  [✓] 空关键词 → 所有文本触发")


def test_text_wake_case_insensitive():
    """英文关键词不区分大小写。"""
    det = TextWakeWordDetector(keyword="jarvis")
    assert det.detect_text("Hey Jarvis, what's up") is True
    assert det.detect_text("JARVIS are you there") is True
    print("  [✓] 大小写不敏感")


def test_text_wake_fire_text_triggers_callback():
    """fire_text 命中时应触发回调。"""
    det = TextWakeWordDetector(keyword="小爱")
    called = []
    det.on_detect(lambda: called.append(1))
    triggered = det.fire_text("小爱同学")
    assert triggered is True
    assert len(called) == 1
    print("  [✓] fire_text 触发回调")


def test_text_wake_fire_text_no_match():
    """fire_text 未命中时不触发回调。"""
    det = TextWakeWordDetector(keyword="小爱")
    called = []
    det.on_detect(lambda: called.append(1))
    triggered = det.fire_text("今天天气")
    assert triggered is False
    assert len(called) == 0
    print("  [✓] fire_text 未命中不触发回调")


def test_text_wake_audio_detect_returns_false():
    """文本检测器不处理音频。"""
    det = TextWakeWordDetector(keyword="小爱")
    assert det.detect(b"\x00" * 100) is False
    print("  [✓] 文本检测器对音频返回 False")


def test_text_wake_is_available_always_true():
    """文本检测器始终可用。"""
    det = TextWakeWordDetector(keyword="小爱")
    assert det.is_available() is True
    print("  [✓] 文本检测器 is_available=True")


# ============================================================
# 2. 能量 VAD 唤醒检测
# ============================================================
def test_energy_vad_loud_triggers():
    """高能量音频应触发。"""
    det = EnergyVADWakeDetector(threshold_db=-40.0, min_speech_sec=0.1)
    wav = _loud_wav(duration_sec=0.5, amplitude=20000)
    assert det.detect(wav) is True
    print("  [✓] 高能量音频触发唤醒")


def test_energy_vad_silence_no_trigger():
    """静音不应触发。"""
    det = EnergyVADWakeDetector(threshold_db=-40.0, min_speech_sec=0.1)
    wav = _silence_wav(duration_sec=0.5)
    assert det.detect(wav) is False
    print("  [✓] 静音不触发唤醒")


def test_energy_vad_short_audio_no_trigger():
    """短于 min_speech_sec 的音频不触发。"""
    det = EnergyVADWakeDetector(threshold_db=-60.0, min_speech_sec=1.0)
    # 只 0.1 秒，远小于 1.0 秒要求
    wav = _loud_wav(duration_sec=0.1, amplitude=20000)
    assert det.detect(wav) is False
    print("  [✓] 短音频不触发")


def test_energy_vad_empty_audio_no_trigger():
    """空音频不触发。"""
    det = EnergyVADWakeDetector(threshold_db=-40.0)
    assert det.detect(b"") is False
    print("  [✓] 空音频不触发")


# ============================================================
# 3. 唤醒词工厂
# ============================================================
def test_factory_text_backend():
    """config engine=text 应返回 TextWakeWordDetector。"""
    with patch("speech.wake_word.config") as mock_cfg:
        mock_cfg.get.side_effect = lambda key, default=None: {
            "wake_word.engine": "text",
            "wake_word.keyword": "小爱",
            "wake_word.aliases": [],
            "app.companion_name": "",
        }.get(key, default)
        det = get_default_wake_word_detector()
        assert isinstance(det, TextWakeWordDetector)
        assert det.keyword == "小爱"
    print("  [✓] 工厂选择 text 后端")


def test_factory_energy_vad_backend():
    """config engine=energy_vad 应返回 EnergyVADWakeDetector。"""
    with patch("speech.wake_word.config") as mock_cfg:
        mock_cfg.get.side_effect = lambda key, default=None: {
            "wake_word.engine": "energy_vad",
            "wake_word.keyword": "",
            "wake_word.threshold_db": -35.0,
            "wake_word.min_speech_sec": 0.3,
            "app.companion_name": "",
        }.get(key, default)
        det = get_default_wake_word_detector()
        assert isinstance(det, EnergyVADWakeDetector)
    print("  [✓] 工厂选择 energy_vad 后端")


def test_factory_keyword_falls_back_to_companion_name():
    """未指定 keyword 时用 companion_name。"""
    with patch("speech.wake_word.config") as mock_cfg:
        mock_cfg.get.side_effect = lambda key, default=None: {
            "wake_word.engine": "text",
            "wake_word.keyword": "",
            "app.companion_name": "小薇",
        }.get(key, default)
        det = get_default_wake_word_detector()
        assert det.keyword == "小薇"
    print("  [✓] keyword 回退到 companion_name")


# ============================================================
# 4. 打断检测
# ============================================================
def test_interrupt_loud_triggers():
    """高能量音频应触发打断。"""
    det = InterruptionDetector(threshold_db=-40.0, min_speech_sec=0.1, cooldown_sec=0)
    called = []
    det.on_interrupt(lambda: called.append(1))
    wav = _loud_wav(duration_sec=0.5, amplitude=20000)
    result = det.check_chunk(wav)
    assert result is True
    assert len(called) == 1
    print("  [✓] 高能量音频触发打断")


def test_interrupt_silence_no_trigger():
    """静音不应触发打断。"""
    det = InterruptionDetector(threshold_db=-40.0, min_speech_sec=0.1, cooldown_sec=0)
    called = []
    det.on_interrupt(lambda: called.append(1))
    wav = _silence_wav(duration_sec=0.5)
    result = det.check_chunk(wav)
    assert result is False
    assert len(called) == 0
    print("  [✓] 静音不触发打断")


def test_interrupt_accumulation():
    """短脉冲累积达到阈值才触发。"""
    # 要求 0.5 秒语音才触发
    det = InterruptionDetector(threshold_db=-60.0, min_speech_sec=0.5, cooldown_sec=0)
    called = []
    det.on_interrupt(lambda: called.append(1))
    # 每次给 0.2 秒高能量，需 3 次才到 0.6 秒
    wav_short = _loud_wav(duration_sec=0.2, amplitude=20000)
    # 第 1 次：未达阈值
    assert det.check_chunk(wav_short) is False
    assert len(called) == 0
    # 第 2 次：未达阈值
    assert det.check_chunk(wav_short) is False
    assert len(called) == 0
    # 第 3 次：达到 0.6 秒，触发
    assert det.check_chunk(wav_short) is True
    assert len(called) == 1
    print("  [✓] 累积达到阈值才触发")


def test_interrupt_silence_resets_accumulation():
    """静音重置累积计数。"""
    det = InterruptionDetector(threshold_db=-60.0, min_speech_sec=0.5, cooldown_sec=0)
    called = []
    det.on_interrupt(lambda: called.append(1))
    wav_loud = _loud_wav(duration_sec=0.3, amplitude=20000)
    wav_silent = _silence_wav(duration_sec=0.1)
    # 累积 0.3 秒
    det.check_chunk(wav_loud)
    assert len(called) == 0
    # 静音重置
    det.check_chunk(wav_silent)
    # 再累积 0.3 秒，不应触发（被重置了）
    det.check_chunk(wav_loud)
    assert len(called) == 0
    print("  [✓] 静音重置累积")


def test_interrupt_cooldown():
    """冷却期内不重复触发。"""
    det = InterruptionDetector(
        threshold_db=-60.0, min_speech_sec=0.1, cooldown_sec=10.0,
    )
    called = []
    det.on_interrupt(lambda: called.append(1))
    wav = _loud_wav(duration_sec=0.5, amplitude=20000)
    # 第一次触发
    det.check_chunk(wav)
    assert len(called) == 1
    # 冷却期内不触发
    det.check_chunk(wav)
    assert len(called) == 1  # 仍为 1
    print("  [✓] 冷却期内不重复触发")


def test_interrupt_empty_no_trigger():
    """空音频不触发。"""
    det = InterruptionDetector(threshold_db=-40.0, min_speech_sec=0.1, cooldown_sec=0)
    assert det.check_chunk(b"") is False
    print("  [✓] 空音频不触发")


def test_interrupt_monitoring_state():
    """start/stop_monitoring 改变 is_monitoring 状态。"""
    det = InterruptionDetector(threshold_db=-40.0, min_speech_sec=0.1)
    # 无麦克风环境 is_available=False，start_monitoring 不启动
    assert det.is_monitoring is False
    det.start_monitoring()  # 沙箱无麦克风，不会真正启动
    # 但 is_monitoring 可能被设为 True（即使线程不运行）
    # 这里只验证不报错
    det.stop_monitoring()
    assert det.is_monitoring is False
    print("  [✓] monitoring 状态管理")


# ============================================================
# 5. ConversationManager 集成
# ============================================================
def _make_manager_with_text_wake(keyword="小爱"):
    """构造带文本唤醒词的 ConversationManager（关闭其他依赖）。"""
    from core.conversation import ConversationManager
    from speech.wake_word import TextWakeWordDetector
    wake = TextWakeWordDetector(keyword=keyword)
    interrupt = InterruptionDetector(threshold_db=-40.0, min_speech_sec=0.1)
    return ConversationManager(
        emotion_engine=False,
        intimacy_manager=False,
        memory_manager=False,
        proactive_manager=False,
        wake_word_detector=wake,
        interruption_detector=interrupt,
        persist=False,
    )


def test_manager_check_wake_word_text_match():
    """ConversationManager.check_wake_word 文本模式命中。"""
    m = _make_manager_with_text_wake(keyword="小爱")
    triggered = m.check_wake_word(text="小爱同学")
    assert triggered is True
    print("  [✓] ConversationManager 文本唤醒命中")


def test_manager_check_wake_word_no_match():
    """ConversationManager.check_wake_word 文本模式未命中。"""
    m = _make_manager_with_text_wake(keyword="小爱")
    triggered = m.check_wake_word(text="今天天气")
    assert triggered is False
    print("  [✓] ConversationManager 文本唤醒未命中")


def test_manager_check_wake_word_none_detector():
    """唤醒词检测器关闭时 check_wake_word 返回 False。"""
    from core.conversation import ConversationManager
    m = ConversationManager(
        emotion_engine=False, intimacy_manager=False,
        memory_manager=False, proactive_manager=False,
        wake_word_detector=False, interruption_detector=False,
        persist=False,
    )
    assert m.wake_word is None
    assert m.check_wake_word(text="小爱") is False
    print("  [✓] 检测器关闭时返回 False")


def test_manager_interrupt_method():
    """手动 interrupt() 应触发 interrupted 事件。"""
    m = _make_manager_with_text_wake()
    called = []
    m.on("interrupted", lambda text="": called.append(text))
    m.interrupt()
    assert len(called) == 1
    print("  [✓] interrupt() 触发 interrupted 事件")


def test_manager_interrupt_none_detector():
    """打断检测器关闭时 interrupt() 不报错。"""
    from core.conversation import ConversationManager
    m = ConversationManager(
        emotion_engine=False, intimacy_manager=False,
        memory_manager=False, proactive_manager=False,
        wake_word_detector=False, interruption_detector=False,
        persist=False,
    )
    assert m.interruption is None
    m.interrupt()  # 不应抛异常
    print("  [✓] 检测器关闭时 interrupt() 不报错")


def test_manager_status_has_wake_fields():
    """status() 应包含 wake_word / interruption 字段。"""
    m = _make_manager_with_text_wake()
    s = m.status()
    assert "wake_word" in s
    assert "wake_listening" in s
    assert "interruption" in s
    assert "interruption_monitoring" in s
    assert "on" in s["wake_word"]
    assert s["interruption"] == "on"
    print("  [✓] status() 包含 wake/interrupt 字段")


def test_manager_status_wake_off():
    """检测器关闭时 status() 显示 off。"""
    from core.conversation import ConversationManager
    m = ConversationManager(
        emotion_engine=False, intimacy_manager=False,
        memory_manager=False, proactive_manager=False,
        wake_word_detector=False, interruption_detector=False,
        persist=False,
    )
    s = m.status()
    assert s["wake_word"] == "off"
    assert s["interruption"] == "off"
    print("  [✓] status() 显示 off")


def test_manager_wake_event_emitted():
    """唤醒触发时应发出 wake 事件。"""
    m = _make_manager_with_text_wake(keyword="小爱")
    called = []
    m.on("wake", lambda text="": called.append(text))
    m.check_wake_word(text="小爱同学")
    assert len(called) == 1
    print("  [✓] wake 事件触发")


# ============================================================
# 6. 单例管理
# ============================================================
def test_wake_word_singleton():
    """get_wake_word_detector 返回单例。"""
    from speech.wake_word import get_wake_word_detector
    reset_wake_word_detector()
    det1 = get_wake_word_detector()
    det2 = get_wake_word_detector()
    assert det1 is det2
    reset_wake_word_detector()
    print("  [✓] 唤醒词检测器单例")


def test_interruption_singleton():
    """get_interruption_detector 返回单例。"""
    reset_interruption_detector()
    det1 = get_interruption_detector()
    det2 = get_interruption_detector()
    assert det1 is det2
    reset_interruption_detector()
    print("  [✓] 打断检测器单例")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        # 文本唤醒词
        test_text_wake_keyword_match,
        test_text_wake_keyword_no_match,
        test_text_wake_alias,
        test_text_wake_empty_keyword_triggers_all,
        test_text_wake_case_insensitive,
        test_text_wake_fire_text_triggers_callback,
        test_text_wake_fire_text_no_match,
        test_text_wake_audio_detect_returns_false,
        test_text_wake_is_available_always_true,
        # 能量 VAD
        test_energy_vad_loud_triggers,
        test_energy_vad_silence_no_trigger,
        test_energy_vad_short_audio_no_trigger,
        test_energy_vad_empty_audio_no_trigger,
        # 工厂
        test_factory_text_backend,
        test_factory_energy_vad_backend,
        test_factory_keyword_falls_back_to_companion_name,
        # 打断
        test_interrupt_loud_triggers,
        test_interrupt_silence_no_trigger,
        test_interrupt_accumulation,
        test_interrupt_silence_resets_accumulation,
        test_interrupt_cooldown,
        test_interrupt_empty_no_trigger,
        test_interrupt_monitoring_state,
        # ConversationManager 集成
        test_manager_check_wake_word_text_match,
        test_manager_check_wake_word_no_match,
        test_manager_check_wake_word_none_detector,
        test_manager_interrupt_method,
        test_manager_interrupt_none_detector,
        test_manager_status_has_wake_fields,
        test_manager_status_wake_off,
        test_manager_wake_event_emitted,
        # 单例
        test_wake_word_singleton,
        test_interruption_singleton,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  [✗] {t.__name__} 失败: {e}")
            traceback.print_exc()

    print(f"\n{'='*50}")
    print(f"结果: {passed} 通过, {failed} 失败 / 共 {len(tests)}")
    if failed == 0:
        print("🎉 全部通过！")
