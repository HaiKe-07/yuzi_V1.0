"""T1-02 LLM 适配器单元测试。

无需真实 API key：用 mock 验证：
1. Message 序列化正确
2. system_prompt + messages 组装正确
3. LLMResponse 字段映射正确
4. 工厂能按 provider 选到 DeepSeekAdapter
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent  # 项目根 ai-companion/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm import Message, get_llm
from llm.base import LLMResponse


def test_message_to_dict():
    m = Message(role="user", content="你好")
    assert m.to_dict() == {"role": "user", "content": "你好"}

    m = Message(role="assistant", content="在的", name="ai")
    assert m.to_dict() == {"role": "assistant", "content": "在的", "name": "ai"}


def test_build_messages_with_system_prompt():
    msgs = [
        Message(role="user", content="嗨"),
        Message(role="assistant", content="嗯？"),
    ]
    adapter = get_llm("deepseek")
    payload = adapter.build_messages(msgs, system_prompt="你是温柔的助手")
    assert payload[0] == {"role": "system", "content": "你是温柔的助手"}
    assert payload[1] == {"role": "user", "content": "嗨"}
    assert payload[2] == {"role": "assistant", "content": "嗯？"}
    print("  [✓] build_messages 系统提示注入位置正确")


def test_chat_returns_llmresponse():
    """mock OpenAI client.chat.completions.create 验证响应映射。"""
    adapter = get_llm("deepseek")

    fake_choice = MagicMock()
    fake_choice.message.content = "  你好啊~  "
    fake_choice.finish_reason = "stop"
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    fake_resp.usage = MagicMock(
        prompt_tokens=10, completion_tokens=5, total_tokens=15
    )
    fake_resp.model_dump.return_value = {"id": "fake"}

    # 让 adapter.client 直接返回我们 mock 的 client
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp
    adapter._client = fake_client

    msgs = [Message(role="user", content="在吗")]
    resp = adapter.chat(msgs, system_prompt="你是助手")
    assert isinstance(resp, LLMResponse)
    assert resp.text == "你好啊~"  # 去掉首尾空白
    assert resp.finish_reason == "stop"
    assert resp.usage["total_tokens"] == 15
    print("  [✓] chat() 返回 LLMResponse，文本/usage/finish_reason 正确")

    # 验证 create 被以正确参数调用
    call_kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "deepseek-chat"
    assert call_kwargs["temperature"] == 0.8
    assert call_kwargs["max_tokens"] == 1024
    assert call_kwargs["messages"][0] == {
        "role": "system", "content": "你是助手"
    }
    print("  [✓] chat() 传入参数 model/temperature/max_tokens/messages 正确")


def test_factory_unknown_provider():
    try:
        get_llm("nonexistent_provider")
    except ValueError as e:
        assert "nonexistent_provider" in str(e)
        print("  [✓] 工厂拒绝未知 provider 并给出可读报错")
        return
    raise AssertionError("未知 provider 应抛 ValueError")


def main() -> int:
    print("T1-02 LLM 适配器单元测试")
    test_message_to_dict()
    print("  [✓] Message 序列化")
    test_build_messages_with_system_prompt()
    test_chat_returns_llmresponse()
    test_factory_unknown_provider()
    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
