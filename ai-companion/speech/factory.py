"""ASR 适配器工厂。

根据 config.yaml 的 asr.provider 字段选择并实例化对应适配器。
新增适配器只需：
1. 实现 BaseASR 子类
2. 在此处的 _REGISTRY 注册
3. config.yaml 中加该 provider 的配置块
"""
from __future__ import annotations

from functools import lru_cache

from utils.config import config
from utils.logger import logger

from .base import ASRResult, BaseASR
from .openai_whisper import OpenAIWhisperAdapter
from .xfyun_asr import XfyunASRAdapter


# provider 名 → 适配器类
_REGISTRY: dict[str, type[BaseASR]] = {
    "cloud_openai_whisper": OpenAIWhisperAdapter,
    "cloud_xfyun": XfyunASRAdapter,  # 骨架，待补全
    # "local_funasr": LocalFunASRAdapter,  # T5-02 本地 ASR 部署后启用
}


def get_asr(provider: str | None = None) -> BaseASR:
    """按 provider 实例化 ASR 适配器。

    Args:
        provider: 配置中的 provider 名，None 则读 config.asr.provider
    Returns:
        BaseASR 实例
    Raises:
        ValueError: 未注册的 provider
    """
    provider = provider or config.get("asr.provider", "cloud_openai_whisper")
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise ValueError(
            f"未知的 ASR provider: {provider!r}，"
            f"已注册: {list(_REGISTRY.keys())}"
        )
    logger.debug(f"实例化 ASR 适配器: {cls.__name__} (provider={provider})")
    return cls()


@lru_cache(maxsize=1)
def get_default_asr() -> BaseASR:
    """全局默认 ASR 单例。"""
    return get_asr()


def clear_cache() -> None:
    get_default_asr.cache_clear()
