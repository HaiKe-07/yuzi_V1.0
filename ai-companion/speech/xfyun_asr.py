"""讯飞 WebSocket 流式 ASR 适配器（骨架，T1-04 阶段未联调）。

讯飞 ASR 使用 WebSocket，需要：
1. 鉴权 URL（hmac-sha1 签名）
2. 持续推送 1280 字节音频帧
3. 接收 JSON 帧拼接结果

完整实现留待拿到 app_id / api_key / api_secret 后补全；
此处给出接口骨架与配置加载，避免阻塞 T1-04 联调。
"""
from __future__ import annotations

from typing import Any

from utils.config import config
from utils.logger import logger

from .base import ASRResult, BaseASR


class XfyunASRAdapter(BaseASR):
    """讯飞流式 ASR 适配器（骨架）。

    实现 TODO：
    - _build_auth_url(): 用 hmac-sha1 签名生成鉴权 ws URL
    - _send_frames():   分帧推送 PCM
    - _recv_result():   接收并合并 JSON 帧
    """

    name = "cloud_xfyun"

    def __init__(
        self,
        app_id: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
    ):
        cfg = config.get("asr.xfyun", {}) or {}
        self.app_id = app_id or cfg.get("app_id", "")
        self.api_key = api_key or cfg.get("api_key", "")
        self.api_secret = api_secret or cfg.get("api_secret", "")

        self._ws = None  # WebSocket 连接占位

        if not all([self.app_id, self.api_key, self.api_secret]):
            logger.warning(
                "讯飞 ASR 凭证未配置完整，transcribe() 调用会报错。"
                "请在 .env 设置 XFYUN_APP_ID / XFYUN_API_KEY / XFYUN_API_SECRET。"
            )
        logger.info(
            f"XfyunASRAdapter 初始化: app_id={self.app_id[:6] + '***' if self.app_id else '(空)'}"
        )

    def transcribe(self, audio: bytes, **kwargs: Any) -> ASRResult:
        """整段语音识别（待实现）。

        实现步骤：
        1. 把 PCM 切成 1280 字节帧
        2. 通过 ws 推送，按状态帧（首/中间/尾）
        3. 收集所有 JSON 响应帧，合并文本
        """
        if not all([self.app_id, self.api_key, self.api_secret]):
            raise RuntimeError(
                "讯飞 ASR 凭证未配置完整，无法调用。"
                "请设置 XFYUN_APP_ID / XFYUN_API_KEY / XFYUN_API_SECRET。"
            )
        raise NotImplementedError(
            "讯飞 WebSocket 流式 ASR 实现待补全。"
            "T1-04 暂用 cloud_openai_whisper 作为默认 ASR provider。"
        )
