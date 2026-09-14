"""唤醒词检测（T2-08）。

让用户通过说出 AI 名字（或自定义关键词）唤醒助手，
无需点击按钮即可进入对话。

设计原则：
- 抽象基类 BaseWakeWordDetector，支持可插拔后端
- 默认 EnergyVADWakeDetector：纯能量 VAD，无外部依赖
- 可选 OpenWakeWordDetector：基于 openwakeword 库，检测特定关键词
- 可选 TextWakeWordDetector：文本关键词匹配（文本对话场景/测试用）
- 沙箱无麦克风环境优雅降级（is_available() → False）

集成方式：
1. 持续监听模式：start() 启动后台线程，检测到唤醒词时回调
2. 单次检测模式：detect(audio_chunk) 同步检测一段音频
3. 文本检测模式：detect_text(text) 检测文本是否含唤醒词

ConversationManager 集成：
- wake_word_detector 注入 ConversationManager
- 前端定时调用 /api/wake/check 检测唤醒
- 检测到唤醒后 state: * → IDLE（就绪）
"""
from __future__ import annotations

import io
import threading
import time
import wave
from abc import ABC, abstractmethod
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

# 可选：openwakeword 库
try:
    import openwakeword
    from openwakeword.model import Model
    _HAS_OWW = True
except ImportError:
    _HAS_OWW = False
    openwakeword = None  # type: ignore
    Model = None  # type: ignore


WakeCallback = Callable[[], None]


# ============================================================
# 抽象基类
# ============================================================
class BaseWakeWordDetector(ABC):
    """唤醒词检测器抽象基类。

    子类必须实现 detect()（单次检测）和 is_available()。
    start()/stop() 用于持续监听模式，子类按需实现。
    """

    #: 后端名称，子类覆盖
    name: str = "base"

    def __init__(self, keyword: str = ""):
        self.keyword = keyword
        self._callbacks: list[WakeCallback] = []
        self._listening = False
        self._thread: threading.Thread | None = None

    # ---- 回调管理 ----
    def on_detect(self, callback: WakeCallback) -> None:
        """注册唤醒回调。检测到唤醒词时调用。"""
        self._callbacks.append(callback)

    def _fire(self) -> None:
        """触发所有注册的回调。"""
        for cb in self._callbacks:
            try:
                cb()
            except Exception as e:
                logger.error(f"唤醒回调异常: {e}")

    # ---- 抽象方法 ----
    @abstractmethod
    def detect(self, audio_chunk: bytes) -> bool:
        """检测一段音频是否含唤醒词。

        Args:
            audio_chunk: WAV 字节或 PCM 字节（16kHz/16bit/单声道）
        Returns:
            True=检测到唤醒词
        """

    @abstractmethod
    def is_available(self) -> bool:
        """环境是否可用（库/麦克风是否就绪）。"""

    # ---- 持续监听模式（可选实现）----
    def start(self) -> None:
        """启动持续监听（后台线程）。

        默认实现：循环录音 → detect → 触发回调。
        子类可重写为更高效的流式监听。
        """
        if self._listening:
            return
        if not self.is_available():
            logger.warning(f"唤醒词检测器 {self.name} 不可用，无法启动监听")
            return
        self._listening = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info(f"唤醒词监听已启动 ({self.name})")

    def stop(self) -> None:
        """停止监听。"""
        self._listening = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        logger.info(f"唤醒词监听已停止 ({self.name})")

    def _listen_loop(self) -> None:
        """持续监听循环（默认实现，子类可重写）。

        每次录 1.5 秒音频，detect 后丢弃。
        """
        from speech.recorder import record
        chunk_sec = 1.5
        while self._listening:
            try:
                audio = record(duration_sec=chunk_sec)
                if self.detect(audio):
                    logger.info(f"唤醒词触发 ({self.name})")
                    self._fire()
            except Exception as e:
                logger.debug(f"唤醒监听循环异常: {e}")
                time.sleep(0.5)

    @property
    def is_listening(self) -> bool:
        return self._listening

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} keyword={self.keyword!r}>"


# ============================================================
# 能量 VAD 后端（默认，无外部依赖）
# ============================================================
class EnergyVADWakeDetector(BaseWakeWordDetector):
    """能量 VAD 唤醒检测器。

    检测到用户开口说话（能量超过阈值）即视为唤醒。
    适用于近场麦克风/按住说话场景，无关键词识别能力，
    但无需额外库，作为兜底方案。

    注意：远场场景易被环境噪声触发，建议升级到 OpenWakeWordDetector。
    """

    name = "energy_vad"

    def __init__(
        self,
        keyword: str = "",
        threshold_db: float = -30.0,
        min_speech_sec: float = 0.3,
    ):
        super().__init__(keyword=keyword)
        self.threshold_db = threshold_db
        self.min_speech_samples = int(min_speech_sec * 16000)  # 16kHz

    def detect(self, audio_chunk: bytes) -> bool:
        if not audio_chunk:
            return False
        if not _HAS_NUMPY:
            return False
        try:
            pcm, sr = _wav_to_pcm(audio_chunk)
        except Exception:
            pcm, sr = audio_chunk, 16000

        arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        if len(arr) == 0:
            return False
        # 归一化到 -1~1
        arr = arr / 32768.0
        # RMS 能量
        rms = float(np.sqrt(np.mean(np.square(arr))))
        threshold = 10 ** (self.threshold_db / 20.0)
        return rms > threshold and len(arr) >= self.min_speech_samples

    def is_available(self) -> bool:
        return _HAS_NUMPY and _HAS_SD


# ============================================================
# openWakeWord 后端（可选，关键词检测）
# ============================================================
class OpenWakeWordDetector(BaseWakeWordDetector):
    """openwakeword 库实现。

    检测特定关键词（如 "hey jarvis" / AI 名字），准确率更高。
    需要安装：pip install openwakeword

    支持自定义关键词模型（.tflite / .onnx）。
    """

    name = "openwakeword"

    def __init__(
        self,
        keyword: str = "",
        model_path: str = "",
        threshold: float = 0.5,
    ):
        super().__init__(keyword=keyword)
        self.model_path = model_path
        self.threshold = threshold
        self._model = None
        if _HAS_OWW:
            try:
                if model_path:
                    self._model = Model(
                        wakeword_models=[model_path],
                        inference_framework="onnx",
                    )
                else:
                    # 用内置预训练模型
                    openwakeword.download_models()
                    self._model = Model(inference_framework="onnx")
                logger.info(f"openwakeword 已加载 (model_path={model_path or 'builtin'})")
            except Exception as e:
                logger.warning(f"openwakeword 模型加载失败: {e}")
                self._model = None

    def detect(self, audio_chunk: bytes) -> bool:
        if not _HAS_OWW or self._model is None:
            return False
        if not audio_chunk:
            return False
        try:
            pcm, sr = _wav_to_pcm(audio_chunk)
        except Exception:
            pcm, sr = audio_chunk, 16000
        # openwakeword 期望 int16 numpy 数组，16kHz
        arr = np.frombuffer(pcm, dtype=np.int16)
        if len(arr) < 1280:  # 至少 80ms（1280 samples @ 16kHz）
            return False
        # 单次预测
        scores = self._model.predict(arr)
        # 取最高分关键词
        if scores:
            for name, score in scores.items():
                if score >= self.threshold:
                    logger.debug(f"openwakeword 命中: {name}={score:.2f}")
                    return True
        return False

    def is_available(self) -> bool:
        return _HAS_OWW and self._model is not None and _HAS_SD


# ============================================================
# 文本关键词后端（文本对话/测试场景）
# ============================================================
class TextWakeWordDetector(BaseWakeWordDetector):
    """文本关键词检测器。

    在文本对话场景下检测唤醒词，无需麦克风。
    例如：用户输入"小爱同学，今天天气如何" → 触发唤醒

    也可作为单元测试用：注入文本，验证回调是否触发。
    """

    name = "text_keyword"

    def __init__(self, keyword: str = "", aliases: list[str] | None = None):
        super().__init__(keyword=keyword)
        self.aliases = aliases or []

    def detect(self, audio_chunk: bytes) -> bool:
        # 文本检测器不处理音频
        return False

    def detect_text(self, text: str) -> bool:
        """检测文本是否含唤醒词。

        Args:
            text: 用户输入文本
        Returns:
            True=含唤醒词
        """
        if not text:
            return False
        keywords = [self.keyword] + self.aliases
        keywords = [k for k in keywords if k]
        if not keywords:
            # 未配置关键词，所有文本都视为唤醒
            return True
        text_lower = text.lower()
        return any(k.lower() in text_lower for k in keywords)

    def fire_text(self, text: str) -> bool:
        """检测文本并触发回调（如命中）。

        Returns:
            True=命中并触发了回调
        """
        if self.detect_text(text):
            logger.info(f"文本唤醒触发 (text={text[:20]!r})")
            self._fire()
            return True
        return False

    def is_available(self) -> bool:
        # 文本检测器始终可用
        return True

    def start(self) -> None:
        # 文本模式无需持续监听
        self._listening = True
        logger.info(f"文本唤醒监听已启动 ({self.name})")

    def stop(self) -> None:
        self._listening = False


# ============================================================
# 工具
# ============================================================
def _wav_to_pcm(wav_bytes: bytes) -> tuple[bytes, int]:
    """从 WAV 字节解出 PCM + 采样率。

    非 WAV 输入原样返回（假设是 PCM）。
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            return wf.readframes(wf.getnframes()), wf.getframerate()
    except Exception:
        return wav_bytes, 16000


# ============================================================
# 工厂函数
# ============================================================
def get_default_wake_word_detector(
    keyword: str | None = None,
) -> BaseWakeWordDetector:
    """根据 config.wake_word 构造默认唤醒词检测器。

    Args:
        keyword: 覆盖配置中的关键词；None 用 config.wake_word.keyword
    """
    cfg = config.get("wake_word", {}) or {}
    engine = cfg.get("engine", "energy_vad")
    kw = keyword or cfg.get("keyword", "") or config.get("app.companion_name", "")

    if engine == "openwakeword" and _HAS_OWW:
        return OpenWakeWordDetector(
            keyword=kw,
            model_path=cfg.get("model_path", ""),
            threshold=cfg.get("threshold", 0.5),
        )
    if engine == "text":
        aliases = cfg.get("aliases", []) or []
        return TextWakeWordDetector(keyword=kw, aliases=aliases)
    # 默认 energy_vad
    return EnergyVADWakeDetector(
        keyword=kw,
        threshold_db=cfg.get("threshold_db", -30.0),
        min_speech_sec=cfg.get("min_speech_sec", 0.3),
    )


# ============================================================
# 全局单例
# ============================================================
_wake_word_detector: BaseWakeWordDetector | None = None


def get_wake_word_detector() -> BaseWakeWordDetector:
    """获取全局唤醒词检测器单例。"""
    global _wake_word_detector
    if _wake_word_detector is None:
        _wake_word_detector = get_default_wake_word_detector()
    return _wake_word_detector


def reset_wake_word_detector() -> None:
    """重置单例（测试用）。"""
    global _wake_word_detector
    if _wake_word_detector is not None:
        try:
            _wake_word_detector.stop()
        except Exception:
            pass
    _wake_word_detector = None
