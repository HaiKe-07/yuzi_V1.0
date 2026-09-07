"""LLM 适配器工厂。

根据 config.yaml 的 llm.provider 字段选择并实例化对应适配器。
新增适配器只需：
1. 实现 LLMAdapter 子类
2. 在此处的 _REGISTRY 注册
3. config.yaml 中加该 provider 的配置块
"""
from __future__ import annotations

from functools import lru_cache

from utils.config import config
from utils.logger import logger

from .base import LLMAdapter
from .deepseek_api import DeepSeekAdapter


# provider 名 → 适配器类（按需扩展）
_REGISTRY: dict[str, type[LLMAdapter]] = {
    "deepseek": DeepSeekAdapter,
    # "local": LocalAdapter,   # T5-01 本地 LLM 部署后启用
}


def get_llm(provider: str | None = None) -> LLMAdapter:
    """按 provider 实例化 LLM 适配器。

    Args:
        provider: 配置中的 provider 名，None 则读 config.llm.provider
    Returns:
        LLMAdapter 实例
    Raises:
        ValueError: 未注册的 provider
    """
    provider = provider or config.get("llm.provider", "deepseek")
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise ValueError(
            f"未知的 LLM provider: {provider!r}，"
            f"已注册: {list(_REGISTRY.keys())}"
        )
    logger.debug(f"实例化 LLM 适配器: {cls.__name__} (provider={provider})")
    return cls()


@lru_cache(maxsize=1)
def get_default_llm() -> LLMAdapter:
    """全局默认 LLM 单例。配置切换后调用 clear_cache()。"""
    return get_llm()


def clear_cache() -> None:
    """配置 reload 后清掉单例缓存。"""
    get_default_llm.cache_clear()
