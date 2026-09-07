"""T1-06 对话管理器单元测试。

用 mock LLM/ASR/TTS 验证：
1. 状态机正确流转 IDLE→THINKING→IDLE
2. 短期记忆按 N 轮滚动
3. text_chat 把对话历史传给 LLM
4. 事件钩子触发时机正确
5. voice_chat 串联 ASR→LLM→TTS
6. 错误时不卡死在中间状态
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.conversation import ConversationManager, ConversationState
from llm import LLMResponse, Message
from speech import ASRResult, TTSResult


# ---------- 工具：构造 mock 三件套 ----------
def make_mock_llm(reply_text="嗯，我在听呢"):
    llm = MagicMock()
    llm.chat.return_value = LLMResponse(text=reply_text, usage={"total_tokens": 10})
    return llm


def make_mock_asr(text="你好呀"):
    asr = MagicMock()
    asr.transcribe.return_value = ASRResult(text=text)
    return asr


def make_mock_tts(audio=b"FAKE_AUDIO"):
    tts = MagicMock()
    tts.default_voice = "nova"
    tts.synthesize.return_value = TTSResult(
        audio=audio, format="mp3", voice="nova"
    )
    return tts


def make_manager(max_turns=3, **kwargs):
    """构造一个不依赖真实 LLM/ASR/TTS 的 manager。"""
    return ConversationManager(
        llm=kwargs.get("llm", make_mock_llm()),
        asr=kwargs.get("asr", make_mock_asr()),
        tts=kwargs.get("tts", make_mock_tts()),
        max_turns=max_turns,
    )


# ---------- 测试用例 ----------
def test_state_machine_text_chat():
    m = make_manager()
    assert m.state == ConversationState.IDLE

    # 触发 text_chat，期间状态应转到 THINKING 再回 IDLE
    states_seen = []
    m.on("state_change", lambda old, new: states_seen.append(new.value))
    reply = m.text_chat("你好")

    assert reply == "嗯，我在听呢"
    assert m.state == ConversationState.IDLE
    assert "thinking" in states_seen
    print("  [✓] 状态机 IDLE→THINKING→IDLE 正确流转")


def test_short_term_history_rolls():
    m = make_manager(max_turns=2)
    # 4 轮对话 = 8 条消息，超过 max_turns*2=4 应滚动
    for i in range(4):
        m.text_chat(f"第{i}句")

    history = m.history
    assert len(history) == 4, f"应保留 4 条，实际 {len(history)}"
    # 最早的两轮应已被淘汰
    assert history[0].content == "第2句"
    assert history[1].content == "嗯，我在听呢"
    assert history[2].content == "第3句"
    print("  [✓] 短期记忆按 max_turns 滚动")


def test_history_passed_to_llm():
    m = make_manager()
    m.text_chat("你好")
    m.text_chat("今天天气怎么样？")

    llm = m.llm
    # 第二次 chat 调用时，历史中已有 3 条（user1, assistant1, user2）
    # assistant2 还没加入（chat 调用之后才加）
    call_args = llm.chat.call_args_list[-1]
    messages = call_args.args[0]
    assert len(messages) == 3, f"期望 3 条历史，实际 {len(messages)}"
    assert messages[0].role == "user"
    assert messages[0].content == "你好"
    assert messages[1].role == "assistant"
    assert messages[1].content == "嗯，我在听呢"
    assert messages[2].role == "user"
    assert messages[2].content == "今天天气怎么样？"
    print("  [✓] 对话历史正确传给 LLM")


def test_personality_prompt_injected():
    m = make_manager()
    m.text_chat("嗨")

    call_kwargs = m.llm.chat.call_args.kwargs
    sys_prompt = call_kwargs["system_prompt"]
    assert "陪伴" in sys_prompt
    assert "温柔" in sys_prompt
    print("  [✓] 人格 system_prompt 注入 LLM")


def test_event_hooks_fire_in_order():
    m = make_manager()
    events = []
    m.on("user_input", lambda text: events.append(("user", text)))
    m.on("ai_reply", lambda text: events.append(("ai", text)))
    m.on("state_change", lambda old, new: events.append(("state", new.value)))

    m.text_chat("你好")

    # 期望顺序：state→thinking, user, ai, state→idle
    assert events[0] == ("state", "thinking")
    assert events[1] == ("user", "你好")
    assert events[2] == ("ai", "嗯，我在听呢")
    assert events[-1] == ("state", "idle")
    print("  [✓] 事件钩子按预期顺序触发")


def test_empty_input_returns_empty():
    m = make_manager()
    reply = m.text_chat("")
    assert reply == ""
    m.llm.chat.assert_not_called()
    print("  [✓] 空输入不调用 LLM")


def test_llm_error_recovers_to_idle():
    m = make_manager()
    m.llm.chat.side_effect = RuntimeError("LLM 挂了")

    errors = []
    m.on("error", lambda exception: errors.append(exception))

    try:
        m.text_chat("你好")
    except RuntimeError:
        pass

    assert m.state == ConversationState.IDLE
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    print("  [✓] LLM 异常后状态恢复 IDLE + 触发 error 事件")


def test_voice_chat_full_chain():
    """语音对话串联 ASR→LLM→TTS。"""
    m = make_manager()
    # 不传 audio 参数，但 asr 是 mock，避免真实录音
    fake_wav = b"FAKE_WAV_BYTES"
    reply = m.voice_chat(audio=fake_wav)

    # ASR 被调用一次，参数是 fake_wav
    m.asr.transcribe.assert_called_once()
    assert m.asr.transcribe.call_args.args[0] == fake_wav
    # LLM 被调用一次
    m.llm.chat.assert_called_once()
    # TTS 被调用一次
    m.tts.synthesize.assert_called_once()
    # 返回值是 LLM 的回复
    assert reply == "嗯，我在听呢"
    print("  [✓] voice_chat 完整链路 ASR→LLM→TTS 串联")


def test_voice_chat_empty_asr_skips_llm():
    """ASR 返回空文本时跳过 LLM/TTS。"""
    m = make_manager()
    m.asr.transcribe.return_value = ASRResult(text="")

    reply = m.voice_chat(audio=b"WAV")

    assert reply == ""
    m.llm.chat.assert_not_called()
    m.tts.synthesize.assert_not_called()
    print("  [✓] ASR 空结果跳过 LLM/TTS")


def test_clear_history():
    m = make_manager()
    m.text_chat("你好")
    assert len(m.history) == 2
    m.clear_history()
    assert len(m.history) == 0
    print("  [✓] clear_history 重置短期记忆")


def test_status():
    m = make_manager()
    s = m.status()
    assert s["state"] == "idle"
    assert s["max_turns"] == 3
    assert "history_len" in s
    print("  [✓] status() 返回完整状态摘要")


def main() -> int:
    print("T1-06 对话管理器单元测试")
    test_state_machine_text_chat()
    test_short_term_history_rolls()
    test_history_passed_to_llm()
    test_personality_prompt_injected()
    test_event_hooks_fire_in_order()
    test_empty_input_returns_empty()
    test_llm_error_recovers_to_idle()
    test_voice_chat_full_chain()
    test_voice_chat_empty_asr_skips_llm()
    test_clear_history()
    test_status()
    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
