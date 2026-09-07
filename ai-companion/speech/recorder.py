"""麦克风录音工具。

提供：
- record(duration_sec): 录指定时长，返回 WAV 字节
- record_until_silence(...): 录到静音为止（VAD 简化版，第二阶段细化）
- save_wav(audio_bytes, path): 落盘调试用

依赖 sounddevice + soundfile；在缺 PortAudio 的环境（如 CI 沙箱）
import 时优雅降级，仅 record() 调用时报错。
"""
from __future__ import annotations

import io
import time
import wave
from typing import Callable

from utils.config import config
from utils.logger import logger

try:
    import sounddevice as sd
    import numpy as np
    _HAS_SD = True
except (ImportError, OSError):
    _HAS_SD = False
    sd = None  # type: ignore
    np = None  # type: ignore


def _require_sd() -> None:
    if not _HAS_SD:
        raise RuntimeError(
            "录音需要 sounddevice + PortAudio。"
            "请安装：pip install sounddevice soundfile "
            "并在系统装 PortAudio（macOS: brew install portaudio；"
            "Linux: apt install libportaudio2）"
        )


def list_input_devices() -> list[dict]:
    """列出可用输入设备，调试用。"""
    _require_sd()
    devs = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] >= 1:
            devs.append({
                "index": i, "name": d["name"],
                "channels": d["max_input_channels"],
                "default_samplerate": d["default_samplerate"],
            })
    return devs


def record(duration_sec: float | None = None) -> bytes:
    """录一段音频，返回 WAV 字节（16kHz / 16bit / 单声道）。

    Args:
        duration_sec: 录音时长。None 则用 config.asr.chunk_duration_ms 折算
                      （用于按键触发场景，按住多久录多久在前端实现）
    Returns:
        WAV 字节数据
    """
    _require_sd()
    cfg = config.get("asr", {}) or {}
    sample_rate = cfg.get("sample_rate", 16000)
    channels = cfg.get("channels", 1)
    if duration_sec is None:
        duration_sec = cfg.get("chunk_duration_ms", 300) / 1000.0

    logger.debug(
        f"开始录音 {duration_sec:.2f}s rate={sample_rate} ch={channels}"
    )
    frames = int(sample_rate * duration_sec)
    audio_int = sd.rec(
        frames=frames,
        samplerate=sample_rate,
        channels=channels,
        dtype="int16",
    )
    sd.wait()  # 阻塞直到录完

    return _pcm_to_wav(audio_int.tobytes(), sample_rate, channels, 2)


def record_until_silence(
    max_duration: float = 10.0,
    silence_threshold_db: float = -35.0,
    silence_duration_sec: float = 1.2,
    chunk_sec: float = 0.1,
) -> bytes:
    """录到检测到静音或超时为止。

    简化版 VAD：基于 RMS 能量判断。第二阶段可换成 Silero VAD。
    适合"用户说完一句话自动结束"的场景。

    Args:
        max_duration:         最长录音时长，防止无限录
        silence_threshold_db: 低于此能量视为静音（-35dB ≈ 安静环境底噪）
        silence_duration_sec: 连续静音达到此时长则停止
        chunk_sec:            每帧分析粒度
    Returns:
        WAV 字节
    """
    _require_sd()
    cfg = config.get("asr", {}) or {}
    sample_rate = cfg.get("sample_rate", 16000)
    channels = cfg.get("channels", 1)

    chunk_frames = int(sample_rate * chunk_sec)
    threshold = 10 ** (silence_threshold_db / 20.0) * 32768

    logger.debug(
        f"VAD 录音启动 max={max_duration}s silence={silence_duration_sec}s"
    )

    collected = []
    silence_start = None
    start_ts = time.time()

    with sd.InputStream(
        samplerate=sample_rate, channels=channels, dtype="int16",
        blocksize=chunk_frames,
    ) as stream:
        while True:
            if time.time() - start_ts > max_duration:
                logger.debug("VAD: 达到最大时长，停止")
                break

            chunk, overflowed = stream.read(chunk_frames)
            if overflowed:
                logger.warning("VAD: 音频缓冲区溢出")

            collected.append(chunk)
            rms = float(np.sqrt(np.mean(np.square(chunk.astype(np.float64)))))
            if rms < threshold:
                if silence_start is None:
                    silence_start = time.time()
                elif time.time() - silence_start >= silence_duration_sec:
                    logger.debug("VAD: 检测到静音，停止")
                    break
            else:
                silence_start = None

    audio_int = np.concatenate(collected, axis=0) if collected else \
        np.zeros((0, channels), dtype="int16")
    return _pcm_to_wav(
        audio_int.tobytes(), sample_rate, channels, 2
    )


def _pcm_to_wav(
    pcm: bytes, sample_rate: int, channels: int, sample_width: int
) -> bytes:
    """把 raw PCM 包装成 WAV 字节流。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def save_wav(audio_bytes: bytes, path: str) -> None:
    """把录音字节落盘，调试用。"""
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(audio_bytes)
    logger.debug(f"音频已保存 {p} ({len(audio_bytes)} bytes)")


def is_available() -> bool:
    """录音环境是否可用，main 自检调用。"""
    return _HAS_SD
