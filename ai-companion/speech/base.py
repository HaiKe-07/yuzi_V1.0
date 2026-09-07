"""ASR（语音识别）抽象层。

设计原则：
- 上层 conversation 只依赖 BaseASR，不感知具体厂商
- 新增 ASR 实现只需实现 transcribe()，并在 factory.py 注册
- 录音逻辑独立成 record() 工具函数，与具体 ASR 解耦
- 支持整段音频（前期）与流式（后期）两种调用模式

第一阶段采用云端整段识别：
    record() → bytes(wav) → transcribe() → text

后续切换本地 FunASR 时，只需新增 LocalFunASRAdapter
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ASRResult:
    """ASR 调用统一返回。

    text:        识别文本
    language:    检测到的语言（zh / en / ...）
    duration:    音频时长（秒），未知填 0
    confidence:  平均置信度 0~1，未知填 0
    raw:          原始响应，调试用
    """
    text: str
    language: str = "zh"
    duration: float = 0.0
    confidence: float = 0.0
    raw: Any = None


class BaseASR(ABC):
    """所有 ASR 适配器的基类。

    子类必须实现 transcribe()，可选实现 stream()（流式）。
    """

    #: 适配器名称，子类覆盖（如 "cloud_openai_whisper" / "local_funasr"）
    name: str = "base"

    #: 该适配器期望的音频格式（采样率/位深/声道）
    sample_rate: int = 16000
    sample_width: int = 2       # 16bit = 2 bytes
    channels: int = 1

    @abstractmethod
    def transcribe(self, audio: bytes, **kwargs: Any) -> ASRResult:
        """整段语音识别。

        Args:
            audio:  WAV 格式字节数据（含 wav 头）
                    16kHz / 16bit / 单声道（参见 config.asr）
            **kwargs: 适配器特定参数
        Returns:
            ASRResult
        """

    def stream(self, audio_stream, **kwargs: Any):
        """流式 ASR，输入音频字节流生成器，输出文本片段。

        默认实现：缓冲到完整段后一次性 transcribe。子类按需重写。
        """
        buf = bytearray()
        for chunk in audio_stream:
            buf.extend(chunk)
        yield self.transcribe(bytes(buf))

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
