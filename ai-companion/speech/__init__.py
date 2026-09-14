"""speech 包：语音 ASR / TTS / 录音 / 播放 / 唤醒 / 打断。

对外暴露统一接口：
    from speech import (
        get_default_asr, get_default_tts,
        record, play,
        get_wake_word_detector, get_interruption_detector,
    )
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
# T2-08 唤醒词与打断
from .wake_word import (
    BaseWakeWordDetector,
    EnergyVADWakeDetector,
    OpenWakeWordDetector,
    TextWakeWordDetector,
    get_default_wake_word_detector,
    get_wake_word_detector,
    reset_wake_word_detector,
)
from .interruption import (
    InterruptionDetector,
    get_interruption_detector,
    reset_interruption_detector,
)

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
    # T2-08 唤醒词
    "BaseWakeWordDetector",
    "EnergyVADWakeDetector",
    "OpenWakeWordDetector",
    "TextWakeWordDetector",
    "get_default_wake_word_detector",
    "get_wake_word_detector",
    "reset_wake_word_detector",
    # T2-08 打断
    "InterruptionDetector",
    "get_interruption_detector",
    "reset_interruption_detector",
]
