"""TTS（语音合成）抽象层。

设计原则：
- 上层 conversation 只依赖 BaseTTS，不感知具体厂商
- 新增 TTS 实现只需实现 synthesize()，并在 factory.py 注册
- 支持整段合成（前期）与流式合成（T2-06 情感 TTS 用）
- 缓存层独立，与具体适配器解耦

第一阶段采用云端整段合成 + 文件缓存：
    text → synthesize() → bytes(mp3) → cache + play()

后续切换 GPT-SoVITS / CosyVoice 2 时，只需新增本地适配器。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class TTSResult:
    """TTS 调用统一返回。

    audio:        音频字节数据（mp3 / wav / pcm，按 format 字段标识）
    format:       音频格式（"mp3" / "wav" / "pcm"）
    sample_rate:  采样率（PCM 时有意义）
    duration:     音频时长（秒），未知填 0
    voice:        实际使用的音色 ID
    cached:       是否命中缓存
    raw:          原始响应，调试用
    """
    audio: bytes
    format: str = "mp3"
    sample_rate: int = 24000
    duration: float = 0.0
    voice: str = ""
    cached: bool = False
    raw: Any = None


class BaseTTS(ABC):
    """所有 TTS 适配器的基类。

    子类必须实现 synthesize()，可选实现 stream()（流式）。
    """

    #: 适配器名称
    name: str = "base"

    #: 默认音色（子类覆盖）
    default_voice: str = ""

    #: 是否支持流式合成
    supports_stream: bool = False

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        **kwargs: Any,
    ) -> TTSResult:
        """整段语音合成。

        Args:
            text:    待合成文本
            voice:   音色 ID，None 用 default_voice
            **kwargs: 适配器特定参数（speed / pitch / emotion 等）
        Returns:
            TTSResult
        """

    def stream(
        self,
        text: str,
        voice: str | None = None,
        **kwargs: Any,
    ):
        """流式合成，返回音频字节块生成器。

        默认实现：一次性 synthesize 后逐 chunk 切。子类按需重写。
        """
        result = self.synthesize(text, voice, **kwargs)
        chunk_size = 4096
        for i in range(0, len(result.audio), chunk_size):
            yield result.audio[i:i + chunk_size]

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
