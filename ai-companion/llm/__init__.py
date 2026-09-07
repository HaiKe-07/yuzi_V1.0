"""LLM 适配器层。

对外暴露统一接口：
    from llm import get_default_llm, Message
"""
from .base import LLMAdapter, LLMResponse, Message
from .factory import get_llm, get_default_llm, clear_cache

__all__ = [
    "LLMAdapter",
    "LLMResponse",
    "Message",
    "get_llm",
    "get_default_llm",
    "clear_cache",
]
