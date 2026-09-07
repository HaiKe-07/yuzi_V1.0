"""OpenAI TTS 云端适配器。

通过 OpenAI SDK 调用 tts-1 模型，把文字转为语音。
- 与 LLM/ASR 复用同一份 openai 包
- 默认音色 nova（OpenAI 提供的柔和女声之一）
- 自动缓存：相同 text+voice 命中本地缓存
- 支持流式（OpenAI TTS 不支持真流式，用整段 + chunk 切模拟）

切换 GPT-SoVITS / CosyVoice 2 时另写适配器，不修改本类。

注：OpenAI TTS 是英文优化的，对中文效果一般；
T2-06 接入情感 TTS 时可切换到 cloud_volc / local_cosyvoice。
"""
from __future__ import annotations

from typing import Any

from utils.config import config
from utils.logger import logger

from .tts_base import BaseTTS, TTSResult
from .tts_cache import TTSCache

try:
    from openai import OpenAI
    from openai import APIError, APIConnectionError, APITimeoutError
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore


class OpenAITTSAdapter(BaseTTS):
    """OpenAI TTS API 适配器。"""

    name = "cloud_openai_tts"
    default_voice = "nova"   # OpenAI 提供的柔和女声
    supports_stream = False  # OpenAI TTS 不支持真流式

    # OpenAI 支持的音色列表
    _VALID_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer", "coral", "sage"}

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "tts-1",
        voice: str | None = None,
        format: str = "mp3",
        speed: float = 1.0,
        timeout: int = 30,
        enable_cache: bool = True,
    ):
        if not _HAS_OPENAI:
            raise ImportError(
                "未安装 openai 包，请运行: pip install openai>=1.12.0"
            )
        cfg = config.get("tts.openai", {}) or {}
        self.api_key = api_key or cfg.get("api_key") or \
            config.get("llm.deepseek.api_key", "") or ""
        self.base_url = base_url or cfg.get("base_url") or None
        self.model = model or cfg.get("model", "tts-1")
        self.default_voice = voice or cfg.get("voice", "nova")
        self.format = format or cfg.get("format", "mp3")
        self.speed = speed if speed != 1.0 else cfg.get("speed", 1.0)
        self.timeout = timeout or cfg.get("timeout", 30)

        if not self.api_key:
            logger.warning(
                "OpenAI TTS api_key 为空，调用 synthesize() 时会报错。"
                "请在 .env 设置 OPENAI_API_KEY 或 config.tts.openai.api_key。"
            )

        self._client = None
        self.cache = TTSCache() if enable_cache else None
        logger.info(
            f"OpenAITTSAdapter 初始化: model={self.model} "
            f"voice={self.default_voice} format={self.format} "
            f"cache={'on' if self.cache else 'off'}"
        )

    @property
    def client(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "OpenAI TTS api_key 未配置，无法调用。"
                    "请设置 OPENAI_API_KEY 环境变量后重试。"
                )
            kwargs = {"api_key": self.api_key, "timeout": self.timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        **kwargs: Any,
    ) -> TTSResult:
        """整段语音合成。

        Args:
            text:   待合成文本
            voice:  音色，None 用 default_voice
            **kwargs: speed / response_format 等
        """
        if not text or not text.strip():
            return TTSResult(audio=b"", format=self.format, voice=voice or self.default_voice)

        voice = voice or self.default_voice
        if voice not in self._VALID_VOICES:
            logger.warning(
                f"音色 {voice!r} 不在 OpenAI 支持列表 {self._VALID_VOICES}，"
                f"回退到 {self.default_voice!r}"
            )
            voice = self.default_voice

        speed = kwargs.pop("speed", self.speed)
        fmt = kwargs.pop("response_format", self.format)

        # 1. 缓存查询
        if self.cache is not None:
            cached = self.cache.get(text, voice, fmt, extra=f"{self.model}:{speed}")
            if cached is not None:
                return TTSResult(
                    audio=cached, format=fmt, voice=voice,
                    cached=True, sample_rate=24000,
                )

        # 2. 调用 API
        params = {
            "model": kwargs.pop("model", self.model),
            "input": text,
            "voice": voice,
            "response_format": fmt,
            "speed": speed,
        }
        try:
            resp = self.client.audio.speech.create(**params)
        except APITimeoutError as e:
            logger.error(f"OpenAI TTS 超时: {e}")
            raise
        except APIConnectionError as e:
            logger.error(f"OpenAI TTS 连接失败: {e}")
            raise
        except APIError as e:
            logger.error(f"OpenAI TTS API 错误: {e}")
            raise

        audio_bytes = resp.content if hasattr(resp, "content") else bytes(resp)

        # 3. 写入缓存
        if self.cache is not None and audio_bytes:
            self.cache.put(text, voice, audio_bytes, fmt, extra=f"{self.model}:{speed}")

        logger.debug(
            f"OpenAI TTS 返回 voice={voice} fmt={fmt} "
            f"bytes={len(audio_bytes)} cached=False"
        )
        return TTSResult(
            audio=audio_bytes, format=fmt, voice=voice,
            cached=False, sample_rate=24000,
            raw=resp,
        )
