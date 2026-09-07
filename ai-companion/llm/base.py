"""LLM 适配器抽象层。

设计原则：
- 上层（conversation / personality）只依赖 LLMAdapter，不感知具体厂商
- 新增模型只需实现 chat()，并在 factory.py 注册
- 支持 system_prompt 注入与多轮消息上下文
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    """对话消息单元。

    role:
        - "system"    系统提示/人格
        - "user"      用户
        - "assistant" AI 回复
        - "tool"      工具调用结果（第四阶段启用）
    """
    role: str
    content: str
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class LLMResponse:
    """LLM 调用统一返回。

    text:           主回复文本
    raw:            原始响应（适配器自定），调试用
    usage:          token 用量统计（prompt_tokens / completion_tokens / total_tokens）
    finish_reason:  结束原因（stop / length / tool_calls 等）
    extra:          适配器扩展字段（如情绪标签、调用工具意图等）
    """
    text: str
    raw: Any = None
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class LLMAdapter(ABC):
    """所有 LLM 适配器的基类。

    子类必须实现 chat()，可选实现：
        - stream()        流式输出（T2-06 情感 TTS 与低延迟场景用）
        - count_tokens()  本地 token 估算（短期记忆裁剪用）
    """

    #: 适配器名称，子类覆盖（如 "deepseek" / "local"）
    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """同步对话。

        Args:
            messages:       对话历史，时间顺序
            system_prompt:  系统提示，若提供则插入到 messages 最前
            **kwargs:       覆盖默认参数（temperature / max_tokens 等）
        Returns:
            LLMResponse
        """

    def stream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        **kwargs: Any,
    ):
        """流式对话，返回生成器逐 token 输出。

        默认实现：直接调用 chat() 后一次性返回。子类按需重写。
        """
        resp = self.chat(messages, system_prompt, **kwargs)
        yield resp.text

    def count_tokens(self, text: str) -> int:
        """粗略 token 估算：中文≈字符数，英文≈单词数*1.3。"""
        cn_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other = len(text) - cn_chars
        return cn_chars + int(other / 3.5)

    def build_messages(
        self,
        messages: list[Message],
        system_prompt: str | None,
    ) -> list[dict[str, Any]]:
        """把 Message 列表 + system_prompt 组装为 API 请求体格式。"""
        out: list[dict[str, Any]] = []
        if system_prompt:
            out.append({"role": "system", "content": system_prompt})
        out.extend(m.to_dict() for m in messages)
        return out

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
