"""火山引擎情感 TTS 适配器（T2-06 实现）。

通过火山引擎 HTTP API 提供带情感的中文女声：
- 鉴权：Bearer;{access_token} Header（注意是分号不是空格）
- 请求：JSON（app/user/audio/request 四段）+ base64 音频响应
- 情感：audio.emotion 字段映射 AI 当前情绪（开心/难过/生气/平静/…）
- 音色：audio.voice_type（默认 zh_female_wanwan 温柔女声）

接口文档：https://www.volcengine.com/docs/6561/1257584

设计要点：
- 零外部依赖：用标准库 urllib，无需 requests/websocket-client
- 缓存：与 OpenAI TTS 共用 TTSCache，相同 text+voice+emotion 命中
- 情感映射：EmotionType → 火山 emotion 字符串（happy/sad/angry/neutral/…）
- 错误处理：超时、HTTP 错误、业务错误码分别抛出可读异常
- 流式：火山 V1 不支持真流式，stream() 用整段+chunk 切模拟
  （V3 WebSocket 真流式留作后续扩展，接口已预留）
"""
from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from utils.config import config
from utils.logger import logger

from .tts_base import BaseTTS, TTSResult
from .tts_cache import TTSCache


# ============================================================
# 火山引擎 TTS API 常量
# ============================================================
_VOLC_TTS_URL = "https://openspeech.bytedance.com/api/v1/tts"
_VOLC_CLUSTER = "volcano_tts"
_VOLC_TIMEOUT = 30  # 秒


# ============================================================
# EmotionType → 火山 emotion 字符串映射
# ============================================================
# 火山支持的 emotion 值（小写英文）：happy/sad/angry/neutral/excited/surprised/…
# 参考 https://www.volcengine.com/docs/6561/1257544 音色列表-多情感音色
_EMOTION_MAP: dict[str, str] = {
    # 正面 → happy/excited
    "开心": "happy",
    "兴奋": "excited",
    "平静": "neutral",
    "感恩": "happy",
    "自豪": "happy",
    "期待": "excited",
    "亲昵": "happy",
    "受鼓舞": "excited",
    # 负面 → sad/angry
    "难过": "sad",
    "焦虑": "sad",
    "生气": "angry",
    "挫败": "angry",
    "孤独": "sad",
    "内疚": "sad",
    "尴尬": "sad",
    "失望": "sad",
    "嫉妒": "angry",
    "无聊": "neutral",
    "疲惫": "sad",
    # 复杂 → 各自映射
    "感动": "happy",
    "怀念": "sad",
    # 中性兜底
    "中性": "neutral",
}


def map_emotion(emotion_label: str | None) -> str:
    """把 AI 情绪标签（中文）映射到火山 emotion 参数。

    Args:
        emotion_label: EmotionType.value（如 "开心"/"难过"/"中性"）
    Returns:
        火山 emotion 字符串（"happy"/"sad"/"angry"/"neutral"/…）
    """
    if not emotion_label:
        return "neutral"
    return _EMOTION_MAP.get(emotion_label, "neutral")


def get_supported_emotions() -> list[str]:
    """获取火山 TTS 支持的情感列表（调试用）。"""
    return sorted(set(_EMOTION_MAP.values()))


# ============================================================
# 火山 TTS 适配器
# ============================================================
class VolcTTSAdapter(BaseTTS):
    """火山引擎情感 TTS 适配器。

    通过 HTTP API 合成带情感的中文语音。
    与 OpenAI TTS 相比，火山对中文和情感表达更自然。

    用法：
        adapter = VolcTTSAdapter()
        result = adapter.synthesize("你好呀", emotion="开心")
        # emotion 参数可传 EmotionType.value 或直接传火山 emotion 字符串

    切换音色：修改 config.tts.volc.voice_type
    切换情感：在 synthesize() 传 emotion 参数（由 ConversationManager 注入）
    """

    name = "cloud_volc"
    default_voice = "zh_female_wanwan"  # 温柔女声
    supports_stream = True  # 接口预留真流式（V3 WebSocket），当前用整段模拟

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

        self._ws = None  # 预留 WebSocket 连接（V3 真流式用）
        self.cache = TTSCache() if enable_cache else None

        if not all([self.app_id, self.access_token]):
            logger.warning(
                "火山 TTS 凭证未配置完整，synthesize() 调用会报错。"
                "请在 .env 设置 VOLC_APP_ID / VOLC_ACCESS_TOKEN。"
            )
        logger.info(
            f"VolcTTSAdapter 初始化: voice={self.default_voice} "
            f"app_id={self.app_id[:6] + '***' if self.app_id else '(空)'} "
            f"cache={'on' if self.cache else 'off'}"
        )

    # ============================================================
    # 请求构造
    # ============================================================
    def _build_request(
        self,
        text: str,
        voice: str,
        emotion: str | None = None,
        speed: float | None = None,
        pitch: float | None = None,
        volume: float | None = None,
    ) -> dict:
        """构造火山 TTS API 请求 JSON。

        结构：app（鉴权）/ user（标识）/ audio（音色+情感）/ request（文本）
        """
        # 情感映射：中文标签 → 火山 emotion 字符串
        volc_emotion = map_emotion(emotion) if emotion else "neutral"

        return {
            "app": {
                "appid": self.app_id,
                "token": "access_token",  # 无实际鉴权作用，可传任意非空
                "cluster": _VOLC_CLUSTER,
            },
            "user": {
                "uid": "ai_companion",  # 可追溯的调用方标识
            },
            "audio": {
                "voice_type": voice,
                "encoding": "mp3",
                "rate": 24000,             # 采样率
                "speed_ratio": speed if speed is not None else self.speed,
                "volume_ratio": volume if volume is not None else self.volume,
                "pitch_ratio": pitch if pitch is not None else self.pitch,
                "emotion": volc_emotion,  # 情感参数（T2-06 核心）
                "language": "cn",
            },
            "request": {
                "reqid": str(uuid.uuid4()),  # 每次请求唯一 ID
                "text": text,
                "text_type": "plain",
                "operation": "query",         # 一次性合成
            },
        }

    def _build_headers(self) -> dict:
        """构造 HTTP Header（Bearer 鉴权，注意是分号分隔）。"""
        return {
            "Authorization": f"Bearer;{self.access_token}",
            "Content-Type": "application/json",
        }

    # ============================================================
    # HTTP 调用
    # ============================================================
    def _call_api(self, payload: dict) -> dict:
        """调用火山 TTS HTTP API，返回解析后的 JSON。

        Raises:
            RuntimeError: 凭证缺失 / 网络错误 / HTTP 非 200
            ValueError:  业务错误码（火山返回 code != 3000）
        """
        if not all([self.app_id, self.access_token]):
            raise RuntimeError(
                "火山 TTS 凭证未配置完整，无法调用。"
                "请设置 VOLC_APP_ID / VOLC_ACCESS_TOKEN。"
            )

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = self._build_headers()
        req = urllib_request.Request(
            _VOLC_TTS_URL,
            data=body,
            headers=headers,
            method="POST",
        )

        try:
            with urllib_request.urlopen(req, timeout=_VOLC_TIMEOUT) as resp:
                status = resp.status
                resp_body = resp.read().decode("utf-8")
        except urllib_error.HTTPError as e:
            # HTTP 错误码（401/403/500 等）
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")[:200]
            except Exception:
                pass
            raise RuntimeError(
                f"火山 TTS HTTP 错误 {e.code}: {e.reason} {err_body}"
            ) from e
        except urllib_error.URLError as e:
            # 网络层错误（DNS/超时/连接拒绝）
            raise RuntimeError(
                f"火山 TTS 网络错误: {e.reason}（请检查网络或代理设置）"
            ) from e
        except Exception as e:
            raise RuntimeError(f"火山 TTS 请求异常: {e}") from e

        if status != 200:
            raise RuntimeError(
                f"火山 TTS HTTP 状态码异常: {status}"
            )

        try:
            result = json.loads(resp_body)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"火山 TTS 响应解析失败: {e} body={resp_body[:200]}"
            ) from e

        # 业务错误码检查（火山成功码 3000）
        code = result.get("code", 0)
        if code != 3000:
            msg = result.get("message", "未知错误")
            raise ValueError(
                f"火山 TTS 业务错误 code={code}: {msg}"
            )

        if "data" not in result:
            raise ValueError(
                f"火山 TTS 响应缺少 data 字段: {result}"
            )

        return result

    # ============================================================
    # synthesize（整段合成）
    # ============================================================
    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        **kwargs: Any,
    ) -> TTSResult:
        """整段语音合成（带情感）。

        Args:
            text:    待合成文本
            voice:   音色 ID，None 用 default_voice
            **kwargs:
                emotion:  情感标签（EmotionType.value 或火山 emotion 字符串）
                speed:    语速 0.2~3.0
                pitch:    音调 0.1~3.0
                volume:   音量 0.1~3.0
        Returns:
            TTSResult（format=mp3, sample_rate=24000）
        """
        if not text or not text.strip():
            return TTSResult(
                audio=b"", format="mp3",
                voice=voice or self.default_voice,
            )

        voice = voice or self.default_voice
        emotion = kwargs.pop("emotion", None)
        speed = kwargs.pop("speed", None)
        pitch = kwargs.pop("pitch", None)
        volume = kwargs.pop("volume", None)

        # 缓存键包含 emotion（不同情感的相同文本应分别缓存）
        cache_extra = f"volc:{voice}:{map_emotion(emotion)}:{speed}:{pitch}:{volume}"

        # 1. 缓存查询
        if self.cache is not None:
            cached = self.cache.get(text, voice, "mp3", extra=cache_extra)
            if cached is not None:
                logger.debug(
                    f"火山 TTS 缓存命中 voice={voice} emotion={emotion}"
                )
                return TTSResult(
                    audio=cached, format="mp3", voice=voice,
                    cached=True, sample_rate=24000,
                )

        # 2. 构造请求并调用 API
        payload = self._build_request(
            text, voice, emotion, speed, pitch, volume,
        )
        logger.debug(
            f"火山 TTS 请求 voice={voice} emotion={emotion} "
            f"text={text[:30]!r}..."
        )
        result = self._call_api(payload)

        # 3. 解码 base64 音频
        try:
            audio_bytes = base64.b64decode(result["data"])
        except Exception as e:
            raise RuntimeError(
                f"火山 TTS 音频解码失败: {e}"
            ) from e

        if not audio_bytes:
            raise ValueError("火山 TTS 返回空音频数据")

        # 4. 写入缓存
        if self.cache is not None:
            self.cache.put(text, voice, audio_bytes, "mp3", extra=cache_extra)

        logger.debug(
            f"火山 TTS 返回 voice={voice} emotion={emotion} "
            f"bytes={len(audio_bytes)} cached=False"
        )
        return TTSResult(
            audio=audio_bytes, format="mp3", voice=voice,
            cached=False, sample_rate=24000,
            raw=result,
        )

    # ============================================================
    # stream（流式合成，模拟）
    # ============================================================
    def stream(
        self,
        text: str,
        voice: str | None = None,
        **kwargs: Any,
    ):
        """流式合成，返回音频字节块生成器。

        火山 V1 HTTP 不支持真流式，此处用整段合成 + chunk 切模拟。
        后续可接入 V3 WebSocket 实现真流式（接口已预留）。
        """
        result = self.synthesize(text, voice, **kwargs)
        chunk_size = 4096
        for i in range(0, len(result.audio), chunk_size):
            yield result.audio[i:i + chunk_size]
