"""OpenAI Whisper 云端 ASR 适配器。

通过 OpenAI SDK 调用 whisper-1 模型，把 WAV 字节转为文字。
- 与 DeepSeek 复用同一份 openai 包
- 可通过 base_url 指向兼容 OpenAI Audio API 的中转服务
- 沙箱环境无 key 也能 import，调用时报错

切换本地 FunASR 时另写适配器，不修改本类。
"""
from __future__ import annotations

import io
from typing import Any

from utils.config import config
from utils.logger import logger

from .base import ASRResult, BaseASR

try:
    from openai import OpenAI
    from openai import APIError, APIConnectionError, APITimeoutError
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore


class OpenAIWhisperAdapter(BaseASR):
    """OpenAI Whisper API 适配器。"""

    name = "cloud_openai_whisper"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "whisper-1",
        language: str = "zh",
        timeout: int = 30,
    ):
        if not _HAS_OPENAI:
            raise ImportError(
                "未安装 openai 包，请运行: pip install openai>=1.12.0"
            )
        cfg = config.get("asr.openai", {}) or {}
        # 也允许用通用 openai key 配置
        self.api_key = api_key or cfg.get("api_key") or \
            config.get("llm.deepseek.api_key", "") or ""
        # base_url 默认走 OpenAI 官方，可指向兼容服务
        self.base_url = base_url or cfg.get("base_url") or None
        self.model = model or cfg.get("model", "whisper-1")
        self.language = language or cfg.get("language", "zh")
        self.timeout = timeout or cfg.get("timeout", 30)

        if not self.api_key:
            logger.warning(
                "Whisper api_key 为空，调用 transcribe() 时会报错。"
                "请在 .env 设置 OPENAI_API_KEY 或 config.asr.openai.api_key。"
            )

        self._client = None
        logger.info(
            f"OpenAIWhisperAdapter 初始化: model={self.model} "
            f"language={self.language} base_url={self.base_url or 'official'}"
        )

    @property
    def client(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "Whisper api_key 未配置，无法调用。"
                    "请设置 OPENAI_API_KEY 环境变量后重试。"
                )
            kwargs = {"api_key": self.api_key, "timeout": self.timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def transcribe(self, audio: bytes, **kwargs: Any) -> ASRResult:
        """整段语音识别。

        Args:
            audio: WAV 字节流
            **kwargs: 透传 language / prompt / temperature 等
        """
        if not audio or len(audio) < 44:
            return ASRResult(text="", language=self.language)

        language = kwargs.pop("language", self.language)
        prompt = kwargs.pop("prompt", None)  # 提示词，提升专业词识别
        temperature = kwargs.pop("temperature", 0.0)

        params = {
            "model": kwargs.pop("model", self.model),
            "language": language,
            "temperature": temperature,
            "response_format": "verbose_json",
        }
        if prompt:
            params["prompt"] = prompt

        try:
            resp = self.client.audio.transcriptions.create(
                file=("audio.wav", io.BytesIO(audio), "audio/wav"),
                **params,
            )
        except APITimeoutError as e:
            logger.error(f"Whisper 超时: {e}")
            raise
        except APIConnectionError as e:
            logger.error(f"Whisper 连接失败: {e}")
            raise
        except APIError as e:
            logger.error(f"Whisper API 错误: {e}")
            raise

        # verbose_json 模式下返回 dict-like 对象
        text = (getattr(resp, "text", "") or "").strip()
        language_detected = getattr(resp, "language", language) or language
        duration = float(getattr(resp, "duration", 0.0) or 0.0)

        # 平均置信度（如有 segments）
        confidence = 0.0
        segments = getattr(resp, "segments", None) or []
        if segments:
            avg_logprob = sum(
                float(getattr(s, "avg_logprob", 0.0)) for s in segments
            ) / len(segments)
            # logprob → 概率近似：exp(avg_logprob)
            try:
                import math
                confidence = math.exp(avg_logprob)
            except (OverflowError, ValueError):
                confidence = 0.0

        logger.debug(
            f"Whisper 返回 text_len={len(text)} "
            f"lang={language_detected} duration={duration:.2f}s "
            f"conf={confidence:.3f}"
        )
        return ASRResult(
            text=text,
            language=language_detected,
            duration=duration,
            confidence=confidence,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else resp,
        )
