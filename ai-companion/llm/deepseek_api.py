"""DeepSeek LLM 适配器。

DeepSeek API 兼容 OpenAI Chat Completions 接口，
故直接复用 openai SDK，把 base_url 指向 DeepSeek。

切换本地 Qwen / 其他兼容 OpenAI 的模型时，可继承本类覆盖 base_url。
"""
from __future__ import annotations

from typing import Any

from utils.config import config
from utils.logger import logger

from .base import LLMAdapter, LLMResponse, Message

try:
    from openai import OpenAI
    from openai import APIError, APIConnectionError, APITimeoutError, RateLimitError
    _HAS_OPENAI = True
except ImportError:  # openai 未安装时给出友好提示，避免 import 阶段崩溃
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore


class DeepSeekAdapter(LLMAdapter):
    """DeepSeek API 适配器。"""

    name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ):
        if not _HAS_OPENAI:
            raise ImportError(
                "未安装 openai 包，请运行: pip install openai>=1.12.0"
            )

        cfg = config.get("llm.deepseek", {}) or {}
        self.api_key = api_key or cfg.get("api_key") or ""
        self.model = model or cfg.get("model", "deepseek-chat")
        self.base_url = base_url or cfg.get("base_url", "https://api.deepseek.com")
        self.temperature = temperature if temperature is not None else cfg.get("temperature", 0.8)
        self.max_tokens = max_tokens or cfg.get("max_tokens", 1024)
        self.timeout = timeout or cfg.get("timeout", 30)

        if not self.api_key:
            logger.warning(
                "DeepSeek api_key 为空，调用 chat() 时会报错。"
                "请在 .env 设置 DEEPSEEK_API_KEY 或修改 config.yaml。"
            )

        # 延迟创建 OpenAI 客户端：无 key 时不影响框架启动
        self._client = None
        logger.info(
            f"DeepSeekAdapter 初始化: model={self.model} "
            f"base_url={self.base_url} temperature={self.temperature}"
        )

    @property
    def client(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "DeepSeek api_key 未配置，无法调用。"
                    "请设置 DEEPSEEK_API_KEY 环境变量后重试。"
                )
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    def chat(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        payload = self.build_messages(messages, system_prompt)
        params = {
            "model": kwargs.pop("model", self.model),
            "messages": payload,
            "temperature": kwargs.pop("temperature", self.temperature),
            "max_tokens": kwargs.pop("max_tokens", self.max_tokens),
            **kwargs,  # 透传其他可选参数（如 top_p / tools）
        }

        try:
            resp = self.client.chat.completions.create(**params)
        except APITimeoutError as e:
            logger.error(f"DeepSeek 超时: {e}")
            raise
        except APIConnectionError as e:
            logger.error(f"DeepSeek 连接失败: {e}")
            raise
        except RateLimitError as e:
            logger.error(f"DeepSeek 限流: {e}")
            raise
        except APIError as e:
            logger.error(f"DeepSeek API 错误: {e}")
            raise

        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        usage = {}
        if getattr(resp, "usage", None):
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
        finish_reason = choice.finish_reason or ""

        logger.debug(
            f"DeepSeek 返回 finish={finish_reason} usage={usage} "
            f"text_len={len(text)}"
        )
        return LLMResponse(
            text=text,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else resp,
            usage=usage,
            finish_reason=finish_reason,
        )

    def stream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        **kwargs: Any,
    ):
        """真流式：逐 chunk 输出文本 delta。"""
        payload = self.build_messages(messages, system_prompt)
        params = {
            "model": kwargs.pop("model", self.model),
            "messages": payload,
            "temperature": kwargs.pop("temperature", self.temperature),
            "max_tokens": kwargs.pop("max_tokens", self.max_tokens),
            "stream": True,
            **kwargs,
        }
        try:
            stream = self.client.chat.completions.create(**params)
        except APIError as e:
            logger.error(f"DeepSeek 流式调用失败: {e}")
            raise

        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content
            except (AttributeError, IndexError):
                continue
            if delta:
                yield delta
