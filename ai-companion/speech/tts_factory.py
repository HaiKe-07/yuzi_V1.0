"""TTS 适配器工厂。

根据 config.yaml 的 tts.provider 字段选择并实例化对应适配器。
新增适配器只需：
1. 实现 BaseTTS 子类
2. 在此处的 _REGISTRY 注册
3. config.yaml 中加该 provider 的配置块
"""
from __future__ import annotations

from functools import lru_cache

from utils.config import config
from utils.logger import logger

from .tts_base import BaseTTS, TTSResult
from .openai_tts import OpenAITTSAdapter
from .volc_tts import VolcTTSAdapter


# provider 名 → 适配器类
_REGISTRY: dict[str, type[BaseTTS]] = {
    "cloud_openai_tts": OpenAITTSAdapter,
    "cloud_volc": VolcTTSAdapter,  # 骨架，T2-06 补全
    # "local_cosyvoice": CosyVoiceAdapter,  # T5-03 启用
}


def get_tts(provider: str | None = None) -> BaseTTS:
    """按 provider 实例化 TTS 适配器。

    Args:
        provider: 配置中的 provider 名，None 则读 config.tts.provider
    Returns:
        BaseTTS 实例
    Raises:
        ValueError: 未注册的 provider
    """
    provider = provider or config.get("tts.provider", "cloud_openai_tts")
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise ValueError(
            f"未知的 TTS provider: {provider!r}，"
            f"已注册: {list(_REGISTRY.keys())}"
        )
    logger.debug(f"实例化 TTS 适配器: {cls.__name__} (provider={provider})")
    return cls()


@lru_cache(maxsize=1)
def get_default_tts() -> BaseTTS:
    """全局默认 TTS 单例。"""
    return get_tts()


def clear_cache() -> None:
    get_default_tts.cache_clear()
