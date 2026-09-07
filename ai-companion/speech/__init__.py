"""speech 包：语音 ASR / TTS / 录音 / 播放。

对外暴露统一接口：
    from speech import get_default_asr, get_default_tts, record, play
"""
from .base import ASRResult, BaseASR
from .factory import clear_cache as clear_asr_cache
from .factory import get_asr, get_default_asr
from .recorder import (
    is_available as mic_available,
    list_input_devices,
    record,
    record_until_silence,
    save_wav,
)
from .player import (
    is_available as player_available,
    play,
    play_file,
    stop as stop_playback,
)
from .tts_base import BaseTTS, TTSResult
from .tts_factory import (
    clear_cache as clear_tts_cache,
    get_default_tts,
    get_tts,
)
from .tts_cache import TTSCache

__all__ = [
    # ASR
    "BaseASR",
    "ASRResult",
    "get_asr",
    "get_default_asr",
    "clear_asr_cache",
    # TTS
    "BaseTTS",
    "TTSResult",
    "get_tts",
    "get_default_tts",
    "clear_tts_cache",
    "TTSCache",
    # 录音
    "record",
    "record_until_silence",
    "save_wav",
    "list_input_devices",
    "mic_available",
    # 播放
    "play",
    "play_file",
    "stop_playback",
    "player_available",
]
