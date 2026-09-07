"""音频播放工具。

提供：
- play(audio, format): 同步播放一段音频字节
- play_file(path): 播放文件
- is_available(): 播放环境是否可用（沙箱无 audio device 时返回 False）

依赖 sounddevice（与 recorder 共用）。无 PortAudio 的环境优雅降级。
T2-08 语音打断时，会扩展为可中断的异步播放。
"""
from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Optional

from utils.logger import logger

try:
    import sounddevice as sd
    import numpy as np
    _HAS_SD = True
except (ImportError, OSError):
    _HAS_SD = False
    sd = None  # type: ignore
    np = None  # type: ignore

# 是否安装了可选的 ffmpeg / pydub（用于非 wav 格式播放）
try:
    from pydub import AudioSegment
    _HAS_PYDUB = True
except ImportError:
    _HAS_PYDUB = False
    AudioSegment = None  # type: ignore


def _require_sd() -> None:
    if not _HAS_SD:
        raise RuntimeError(
            "播放需要 sounddevice + PortAudio。"
            "请安装：pip install sounddevice soundfile "
            "并在系统装 PortAudio（macOS: brew install portaudio；"
            "Linux: apt install libportaudio2 libportaudiocxx0）"
        )


def _decode_to_pcm(audio: bytes, fmt: str) -> tuple[bytes, int, int, int]:
    """把任意格式音频解码为 PCM + 元数据。

    Returns:
        (pcm_bytes, sample_rate, channels, sample_width)
    """
    fmt = fmt.lower()
    if fmt == "wav":
        with wave.open(io.BytesIO(audio), "rb") as wf:
            return (
                wf.readframes(wf.getnframes()),
                wf.getframerate(),
                wf.getnchannels(),
                wf.getsampwidth(),
            )
    if fmt in ("mp3", "ogg", "flac", "aac") and _HAS_PYDUB:
        seg = AudioSegment.from_file(io.BytesIO(audio), format=fmt)
        # 转换到 16bit PCM
        seg = seg.set_sample_width(2)
        return (
            seg.raw_data,
            seg.frame_rate,
            seg.channels,
            2,
        )
    if fmt == "pcm":
        # 假设 24kHz mono 16bit（OpenAI TTS 默认）
        return audio, 24000, 1, 2
    raise ValueError(
        f"无法解码格式 {fmt!r}（需安装 pydub + ffmpeg 处理非 wav 格式）"
    )


def play(
    audio: bytes,
    format: str = "mp3",
    blocking: bool = True,
    on_done: Optional[callable] = None,
) -> None:
    """播放一段音频字节。

    Args:
        audio:     音频字节数据
        format:    音频格式（mp3 / wav / pcm）
        blocking:  是否阻塞直到播放结束
        on_done:   播放完成回调（非阻塞模式有效）
    """
    _require_sd()
    if not audio:
        logger.debug("play() 收到空音频，跳过")
        if on_done:
            on_done()
        return

    try:
        pcm, sample_rate, channels, sample_width = _decode_to_pcm(audio, format)
    except ValueError as e:
        logger.error(f"音频解码失败: {e}")
        if on_done:
            on_done()
        return

    # 转 numpy int16 数组
    arr = np.frombuffer(pcm, dtype=np.int16)
    if channels > 1:
        arr = arr.reshape(-1, channels)

    logger.debug(
        f"播放音频 fmt={format} samples={len(arr)} "
        f"rate={sample_rate} ch={channels}"
    )

    sd.play(arr, sample_rate=sample_rate, channels=channels)
    if blocking:
        sd.wait()
        if on_done:
            on_done()
    else:
        # 非阻塞：用 sd.Stream 自行管理，T2-08 中断时扩展
        import threading
        def _bg():
            sd.wait()
            if on_done:
                on_done()
        threading.Thread(target=_bg, daemon=True).start()


def play_file(path: str | Path, blocking: bool = True) -> None:
    """播放音频文件。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"音频文件不存在: {p}")
    fmt = p.suffix.lstrip(".").lower() or "mp3"
    play(p.read_bytes(), format=fmt, blocking=blocking)


def stop() -> None:
    """停止当前播放（T2-08 语音打断用）。"""
    if _HAS_SD:
        sd.stop()


def is_available() -> bool:
    """播放环境是否可用。"""
    return _HAS_SD
