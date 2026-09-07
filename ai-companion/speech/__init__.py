"""speech 包：语音 ASR / TTS / 录音。

对外暴露统一接口：
    from speech import get_default_asr, record
"""
from .base import ASRResult, BaseASR
from .factory import clear_cache, get_asr, get_default_asr
from .recorder import (
    is_available,
    list_input_devices,
    record,
    record_until_silence,
    save_wav,
)

__all__ = [
    "BaseASR",
    "ASRResult",
    "get_asr",
    "get_default_asr",
    "clear_cache",
    "record",
    "record_until_silence",
    "save_wav",
    "list_input_devices",
    "is_available",
]
