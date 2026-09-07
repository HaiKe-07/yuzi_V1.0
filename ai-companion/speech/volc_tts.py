"""火山引擎 TTS 适配器（骨架，T1-05 阶段未联调）。

火山引擎 TTS 通过 WebSocket 提供带情感的中文女声：
- 鉴权：token + app_id
- 请求：UTF-8 JSON + base64 文本
- 响应：分片二进制音频帧

完整实现留待拿到 app_id / access_token 后补全；
T2-06 情感 TTS 阶段接入。此处给出接口骨架与配置加载。
"""
from __future__ import annotations

from typing import Any

from utils.config import config
from utils.logger import logger

from .tts_base import BaseTTS, TTSResult
from .tts_cache import TTSCache


class VolcTTSAdapter(BaseTTS):
    """火山引擎情感 TTS 适配器（骨架）。

    实现 TODO：
    - _build_auth_url(): token 鉴权
    - _send_request():    JSON + base64 文本
    - _recv_audio():      接收并拼接二进制帧
    - emotion 参数映射（开心/难过/生气/平静/期待/疲惫）
    """

    name = "cloud_volc"
    default_voice = "zh_female_wanwan"  # 温柔女声占位
    supports_stream = True

    def __init__(
        self,
        app_id: str | None = None,
        access_token: str | None = None,
        voice_type: str | None = None,
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: float = 1.0,
        enable_cache: bool = True,
    ):
        cfg = config.get("tts.volc", {}) or {}
        self.app_id = app_id or cfg.get("app_id", "")
        self.access_token = access_token or cfg.get("access_token", "")
        self.default_voice = voice_type or cfg.get("voice_type", "zh_female_wanwan")
        self.speed = speed if speed != 1.0 else cfg.get("speed", 1.0)
        self.pitch = pitch if pitch != 1.0 else cfg.get("pitch", 1.0)
        self.volume = volume if volume != 1.0 else cfg.get("volume", 1.0)

        self._ws = None
        self.cache = TTSCache() if enable_cache else None

        if not all([self.app_id, self.access_token]):
            logger.warning(
                "火山 TTS 凭证未配置完整，synthesize() 调用会报错。"
                "请在 .env 设置 VOLC_APP_ID / VOLC_ACCESS_TOKEN。"
            )
        logger.info(
            f"VolcTTSAdapter 初始化: voice={self.default_voice} "
            f"app_id={self.app_id[:6] + '***' if self.app_id else '(空)'}"
        )

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        **kwargs: Any,
    ) -> TTSResult:
        """整段语音合成（待实现）。

        实现步骤：
        1. 鉴权 + 建立 WebSocket
        2. 发送 JSON 请求（含 emotion 参数，T2-06 接入）
        3. 接收二进制音频帧，拼接成完整音频
        4. 写入缓存
        """
        if not all([self.app_id, self.access_token]):
            raise RuntimeError(
                "火山 TTS 凭证未配置完整，无法调用。"
                "请设置 VOLC_APP_ID / VOLC_ACCESS_TOKEN。"
            )
        raise NotImplementedError(
            "火山引擎 WebSocket TTS 实现待补全。"
            "T1-05 暂用 cloud_openai_tts 作为默认 TTS provider；"
            "T2-06 情感 TTS 阶段补全。"
        )
