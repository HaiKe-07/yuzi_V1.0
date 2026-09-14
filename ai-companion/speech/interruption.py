"""语音打断检测（T2-08）。

AI 播放语音时，持续监听麦克风。检测到用户开口说话（能量超过阈值）
立即停止播放，让用户能"插嘴"，更接近真人对话。

设计原则：
- 能量 VAD 为主：RMS 超过阈值且持续 min_speech_sec 即视为打断
- 阈值高于唤醒词检测（更严格，避免环境噪声误触发）
- 冷却机制：检测到一次打断后冷却 cooldown_sec，避免抖动
- 沙箱无麦克风环境优雅降级（is_available() → False）

集成方式：
1. 同步模式：check_chunk(audio) 检测单段音频
2. 监听模式：start_monitoring(callback) 后台线程持续监听
   AI 开始播放时 start_monitoring，播放结束 stop_monitoring

ConversationManager 集成：
- speak() 调用前 start_monitoring，播放完毕 stop_monitoring
- 打断回调 → player.stop() → state: SPEAKING → IDLE
"""
from __future__ import annotations

import io
import threading
import time
import wave
from typing import Callable

from utils.config import config
from utils.logger import logger

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False
    np = None  # type: ignore

try:
    import sounddevice as sd
    _HAS_SD = True
except (ImportError, OSError):
    _HAS_SD = False
    sd = None  # type: ignore


InterruptCallback = Callable[[], None]


# ============================================================
# 打断检测器
# ============================================================
class InterruptionDetector:
    """语音打断检测器。

    用法（监听模式）：
        det = InterruptionDetector()
        det.start_monitoring(on_interrupt=lambda: player.stop())
        # ... AI 播放语音 ...
        det.stop_monitoring()

    用法（同步检测）：
        det = InterruptionDetector()
        if det.check_chunk(audio):
            player.stop()
    """

    def __init__(
        self,
        threshold_db: float = -25.0,
        min_speech_sec: float = 0.2,
        cooldown_sec: float = 1.5,
        chunk_sec: float = 0.1,
        sample_rate: int = 16000,
        channels: int = 1,
    ):
        # 阈值高于唤醒词（-25 vs -30 dB），减少环境噪声误触发
        self.threshold_db = threshold_db
        self.min_speech_samples = int(min_speech_sec * sample_rate)
        self.cooldown_sec = cooldown_sec
        self.chunk_sec = chunk_sec
        self.sample_rate = sample_rate
        self.channels = channels

        self._monitoring = False
        self._thread: threading.Thread | None = None
        self._callbacks: list[InterruptCallback] = []
        self._last_interrupt_at: float = 0.0
        # 持续语音累积计数（连续检测到语音的样本数）
        self._speech_count: int = 0

        logger.info(
            f"InterruptionDetector 初始化: threshold={threshold_db}dB "
            f"min_speech={min_speech_sec}s cooldown={cooldown_sec}s"
        )

    # ============================================================
    # 回调管理
    # ============================================================
    def on_interrupt(self, callback: InterruptCallback) -> None:
        """注册打断回调。检测到用户打断时调用。"""
        self._callbacks.append(callback)

    def _fire(self) -> None:
        """触发所有打断回调。"""
        now = time.time()
        # 冷却判断：避免短时间内多次打断
        if now - self._last_interrupt_at < self.cooldown_sec:
            return
        self._last_interrupt_at = now
        logger.info(f"检测到用户打断 (count={self._speech_count})")
        for cb in self._callbacks:
            try:
                cb()
            except Exception as e:
                logger.error(f"打断回调异常: {e}")

    # ============================================================
    # 同步检测
    # ============================================================
    def check_chunk(self, audio_chunk: bytes) -> bool:
        """检测单段音频是否含用户语音（打断）。

        持续累积语音样本，达到 min_speech_samples 才触发。

        Args:
            audio_chunk: WAV 或 PCM 字节
        Returns:
            True=检测到打断
        """
        if not audio_chunk or not _HAS_NUMPY:
            return False
        try:
            pcm, sr = _wav_to_pcm(audio_chunk)
        except Exception:
            pcm, sr = audio_chunk, 16000

        arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        if len(arr) == 0:
            return False
        arr = arr / 32768.0
        rms = float(np.sqrt(np.mean(np.square(arr))))
        threshold = 10 ** (self.threshold_db / 20.0)

        if rms > threshold:
            self._speech_count += len(arr)
            if self._speech_count >= self.min_speech_samples:
                self._fire()
                self._speech_count = 0
                return True
        else:
            # 静音时重置累积（防止间歇噪声凑数）
            self._speech_count = 0
        return False

    # ============================================================
    # 监听模式
    # ============================================================
    def start_monitoring(self) -> None:
        """启动后台监听线程。

        AI 开始播放语音时调用。检测到打断会触发 on_interrupt 回调。
        """
        if self._monitoring:
            return
        if not self.is_available():
            logger.warning("打断检测器不可用（无麦克风），跳过监听")
            return
        self._monitoring = True
        self._speech_count = 0
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("打断监听已启动")

    def stop_monitoring(self) -> None:
        """停止监听。AI 播放结束时调用。"""
        was = self._monitoring
        self._monitoring = False
        self._speech_count = 0
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        if was:
            logger.info("打断监听已停止")

    def _monitor_loop(self) -> None:
        """持续监听循环：每 chunk_sec 录一段，检测是否含打断。"""
        if not _HAS_SD:
            return
        cfg = config.get("asr", {}) or {}
        sr = cfg.get("sample_rate", self.sample_rate)
        channels = cfg.get("channels", self.channels)
        chunk_frames = int(sr * self.chunk_sec)

        try:
            with sd.InputStream(
                samplerate=sr, channels=channels, dtype="int16",
                blocksize=chunk_frames,
            ) as stream:
                while self._monitoring:
                    chunk, overflowed = stream.read(chunk_frames)
                    if overflowed:
                        logger.debug("打断监听: 缓冲区溢出")
                    # 包装成 WAV 给 check_chunk
                    wav_bytes = _pcm_to_wav(
                        chunk.tobytes(), sr, channels, 2
                    )
                    self.check_chunk(wav_bytes)
        except Exception as e:
            logger.error(f"打断监听循环异常: {e}")

    # ============================================================
    # 状态
    # ============================================================
    @property
    def is_monitoring(self) -> bool:
        return self._monitoring

    def is_available(self) -> bool:
        """环境是否可用（麦克风+numpy 就绪）。"""
        return _HAS_NUMPY and _HAS_SD

    def status(self) -> dict:
        """当前状态摘要，调试用。"""
        return {
            "monitoring": self._monitoring,
            "available": self.is_available(),
            "threshold_db": self.threshold_db,
            "speech_count": self._speech_count,
            "cooldown_sec": self.cooldown_sec,
        }


# ============================================================
# 工具
# ============================================================
def _wav_to_pcm(wav_bytes: bytes) -> tuple[bytes, int]:
    """从 WAV 字节解出 PCM + 采样率。"""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            return wf.readframes(wf.getnframes()), wf.getframerate()
    except Exception:
        return wav_bytes, 16000


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


# ============================================================
# 全局单例
# ============================================================
_interruption_detector: InterruptionDetector | None = None


def get_interruption_detector() -> InterruptionDetector:
    """获取全局打断检测器单例。"""
    global _interruption_detector
    if _interruption_detector is None:
        cfg = config.get("interruption", {}) or {}
        _interruption_detector = InterruptionDetector(
            threshold_db=cfg.get("threshold_db", -25.0),
            min_speech_sec=cfg.get("min_speech_sec", 0.2),
            cooldown_sec=cfg.get("cooldown_sec", 1.5),
            chunk_sec=cfg.get("chunk_sec", 0.1),
        )
    return _interruption_detector


def reset_interruption_detector() -> None:
    """重置单例（测试用）。"""
    global _interruption_detector
    if _interruption_detector is not None:
        try:
            _interruption_detector.stop_monitoring()
        except Exception:
            pass
    _interruption_detector = None
