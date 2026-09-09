"""T2-06 火山引擎情感 TTS 单元测试。

无需真实 API 凭证：用 mock 验证：
1. EmotionType → 火山 emotion 字符串映射
2. 请求 JSON 结构（app/user/audio/request 四段）
3. HTTP Header 鉴权格式（Bearer;token 分号分隔）
4. synthesize 字段映射（audio/voice/format/cached）
5. 情感参数传递（emotion 注入到请求）
6. 缓存命中/未命中（含 emotion 区分）
7. 错误处理（凭证缺失/HTTP 错误/业务错误码）
8. stream() 分块生成器
9. ConversationManager.speak 注入 AI 情绪
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib import error as urllib_error

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from speech.volc_tts import (
    VolcTTSAdapter, map_emotion, get_supported_emotions,
    _VOLC_TTS_URL, _VOLC_CLUSTER,
)
from speech.tts_base import TTSResult


# ============================================================
# 测试工具
# ============================================================
def _make_adapter(
    app_id: str = "test_appid",
    access_token: str = "test_token",
    enable_cache: bool = False,
) -> VolcTTSAdapter:
    """构造测试用适配器（默认带凭证，关缓存）。"""
    return VolcTTSAdapter(
        app_id=app_id,
        access_token=access_token,
        enable_cache=enable_cache,
    )


def _mock_api_response(
    audio_bytes: bytes = b"FAKE_MP3_AUDIO",
    code: int = 3000,
    message: str = "Success",
) -> dict:
    """构造火山 API 成功响应 JSON。"""
    return {
        "code": code,
        "message": message,
        "data": base64.b64encode(audio_bytes).decode("utf-8"),
        "reqid": "test-reqid",
    }


class _FakeHTTPResponse:
    """模拟 urllib urlopen 返回的 HTTP 响应对象。"""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# ============================================================
# 1. 情感映射
# ============================================================
def test_emotion_map_happy():
    assert map_emotion("开心") == "happy"
    print("  [✓] 开心 → happy")


def test_emotion_map_sad():
    assert map_emotion("难过") == "sad"
    print("  [✓] 难过 → sad")


def test_emotion_map_angry():
    assert map_emotion("生气") == "angry"
    print("  [✓] 生气 → angry")


def test_emotion_map_neutral():
    assert map_emotion("中性") == "neutral"
    print("  [✓] 中性 → neutral")


def test_emotion_map_excited():
    assert map_emotion("兴奋") == "excited"
    print("  [✓] 兴奋 → excited")


def test_emotion_map_unknown_falls_back():
    """未知情绪标签应回退到 neutral。"""
    assert map_emotion("未知情绪") == "neutral"
    assert map_emotion(None) == "neutral"
    assert map_emotion("") == "neutral"
    print("  [✓] 未知/空情绪 → neutral")


def test_supported_emotions():
    """支持的情感列表应包含核心情感。"""
    emotions = get_supported_emotions()
    assert "happy" in emotions
    assert "sad" in emotions
    assert "angry" in emotions
    assert "neutral" in emotions
    print(f"  [✓] 支持情感: {emotions}")


# ============================================================
# 2. 请求构造
# ============================================================
def test_build_request_structure():
    """请求 JSON 应含 app/user/audio/request 四段。"""
    adapter = _make_adapter()
    payload = adapter._build_request(
        text="你好呀", voice="zh_female_wanwan", emotion="开心",
    )
    assert "app" in payload
    assert "user" in payload
    assert "audio" in payload
    assert "request" in payload
    print("  [✓] 请求结构含 app/user/audio/request")


def test_build_request_app_section():
    """app 段应含 appid/cluster。"""
    adapter = _make_adapter(app_id="my_app_123")
    payload = adapter._build_request("test", "voice")
    assert payload["app"]["appid"] == "my_app_123"
    assert payload["app"]["cluster"] == _VOLC_CLUSTER
    print("  [✓] app 段 appid/cluster 正确")


def test_build_request_audio_section():
    """audio 段应含 voice_type/encoding/emotion。"""
    adapter = _make_adapter()
    payload = adapter._build_request(
        "test", "zh_female_wanwan", emotion="难过",
    )
    audio = payload["audio"]
    assert audio["voice_type"] == "zh_female_wanwan"
    assert audio["encoding"] == "mp3"
    assert audio["emotion"] == "sad"  # 难过 → sad
    assert audio["language"] == "cn"
    print("  [✓] audio 段 voice_type/encoding/emotion 正确")


def test_build_request_emotion_injected():
    """emotion 参数应映射到 audio.emotion。"""
    adapter = _make_adapter()
    # 开心
    p1 = adapter._build_request("t", "v", emotion="开心")
    assert p1["audio"]["emotion"] == "happy"
    # 生气
    p2 = adapter._build_request("t", "v", emotion="生气")
    assert p2["audio"]["emotion"] == "angry"
    # 无 emotion → neutral
    p3 = adapter._build_request("t", "v", emotion=None)
    assert p3["audio"]["emotion"] == "neutral"
    print("  [✓] emotion 参数注入正确")


def test_build_request_reqid_unique():
    """reqid 应每次不同（UUID）。"""
    adapter = _make_adapter()
    p1 = adapter._build_request("t", "v")
    p2 = adapter._build_request("t", "v")
    assert p1["request"]["reqid"] != p2["request"]["reqid"]
    print("  [✓] reqid 唯一")


def test_build_request_text():
    """request 段应含原始文本。"""
    adapter = _make_adapter()
    payload = adapter._build_request("你好世界", "v")
    assert payload["request"]["text"] == "你好世界"
    assert payload["request"]["text_type"] == "plain"
    assert payload["request"]["operation"] == "query"
    print("  [✓] request 段文本正确")


def test_build_headers_bearer_format():
    """Header 鉴权应为 Bearer;{token}（分号分隔）。"""
    adapter = _make_adapter(access_token="my_secret_token")
    headers = adapter._build_headers()
    assert headers["Authorization"] == "Bearer;my_secret_token"
    assert headers["Content-Type"] == "application/json"
    print("  [✓] Header Bearer;token 格式")


# ============================================================
# 3. synthesize（mock HTTP）
# ============================================================
def test_synthesize_basic():
    """synthesize 应返回 TTSResult，字段正确。"""
    adapter = _make_adapter(enable_cache=False)
    fake_resp = _FakeHTTPResponse(
        body=json.dumps(_mock_api_response(b"AUDIO_DATA")).encode("utf-8"),
    )
    with patch("speech.volc_tts.urllib_request.urlopen", return_value=fake_resp):
        result = adapter.synthesize("你好呀", voice="zh_female_wanwan")

    assert isinstance(result, TTSResult)
    assert result.audio == b"AUDIO_DATA"
    assert result.format == "mp3"
    assert result.voice == "zh_female_wanwan"
    assert result.cached is False
    assert result.sample_rate == 24000
    print("  [✓] synthesize 基本字段映射")


def test_synthesize_with_emotion():
    """synthesize 应把 emotion 传到请求。"""
    adapter = _make_adapter(enable_cache=False)
    fake_resp = _FakeHTTPResponse(
        body=json.dumps(_mock_api_response()).encode("utf-8"),
    )
    captured_payload = {}

    def _capture_urlopen(req, **kwargs):
        captured_payload["body"] = json.loads(req.data.decode("utf-8"))
        return fake_resp

    with patch("speech.volc_tts.urllib_request.urlopen", side_effect=_capture_urlopen):
        adapter.synthesize("你好", emotion="开心")

    assert captured_payload["body"]["audio"]["emotion"] == "happy"
    print("  [✓] synthesize emotion 注入到请求")


def test_synthesize_empty_text():
    """空文本应直接返回空 TTSResult，不调 API。"""
    adapter = _make_adapter(enable_cache=False)
    result = adapter.synthesize("")
    assert result.audio == b""
    assert result.voice == adapter.default_voice

    result2 = adapter.synthesize("   ")
    assert result2.audio == b""
    print("  [✓] 空文本不调 API")


def test_synthesize_no_credentials():
    """无凭证应抛 RuntimeError 且消息可读。"""
    adapter = _make_adapter(app_id="", access_token="")
    try:
        adapter.synthesize("你好")
    except RuntimeError as e:
        assert "凭证" in str(e)
        print("  [✓] 无凭证报 RuntimeError（可读）")
        return
    raise AssertionError("无凭证应报错")


def test_synthesize_cache_hit():
    """缓存命中时应跳过 API 调用。"""
    adapter = _make_adapter(enable_cache=True)
    # 预填缓存
    cache_extra = f"volc:{adapter.default_voice}:happy:None:None:None"
    adapter.cache.put("你好", adapter.default_voice, b"CACHED", "mp3", extra=cache_extra)

    # mock urlopen 不应被调用
    with patch("speech.volc_tts.urllib_request.urlopen") as mock_urlopen:
        result = adapter.synthesize("你好", emotion="开心")

    assert mock_urlopen.call_count == 0
    assert result.audio == b"CACHED"
    assert result.cached is True
    print("  [✓] 缓存命中跳过 API")


def test_synthesize_cache_miss_writes():
    """缓存未命中时应调用 API 并写入缓存。"""
    with tempfile.TemporaryDirectory() as tmp:
        adapter = _make_adapter(enable_cache=True)
        adapter.cache = __import__("speech.tts_cache", fromlist=["TTSCache"]).TTSCache(
            cache_dir=tmp, max_entries=10,
        )
        fake_resp = _FakeHTTPResponse(
            body=json.dumps(_mock_api_response(b"NEW_AUDIO")).encode("utf-8"),
        )
        with patch("speech.volc_tts.urllib_request.urlopen", return_value=fake_resp):
            result = adapter.synthesize("新句子", emotion="难过")

        assert result.audio == b"NEW_AUDIO"
        assert result.cached is False
        # 验证缓存已写入
        cache_key = adapter.cache._make_key(
            "新句子", adapter.default_voice,
            f"volc:{adapter.default_voice}:sad:None:None:None",
        )
        cache_path = adapter.cache._path_for(cache_key, "mp3")
        assert cache_path.exists()
        print("  [✓] 缓存未命中 → 调 API → 写缓存")


def test_synthesize_different_emotions_different_cache():
    """不同情感的相同文本应分别缓存。"""
    adapter = _make_adapter(enable_cache=True)
    fake_resp = _FakeHTTPResponse(
        body=json.dumps(_mock_api_response(b"HAPPY_AUDIO")).encode("utf-8"),
    )
    with patch("speech.volc_tts.urllib_request.urlopen", return_value=fake_resp):
        adapter.synthesize("你好", emotion="开心")

    # 切换 emotion，应重新调 API（不命中开心缓存）
    fake_resp2 = _FakeHTTPResponse(
        body=json.dumps(_mock_api_response(b"SAD_AUDIO")).encode("utf-8"),
    )
    with patch("speech.volc_tts.urllib_request.urlopen", return_value=fake_resp2) as m:
        result = adapter.synthesize("你好", emotion="难过")

    assert m.call_count == 1  # 重新调了 API
    assert result.audio == b"SAD_AUDIO"
    print("  [✓] 不同情感分别缓存")


# ============================================================
# 4. 错误处理
# ============================================================
def test_error_http_401():
    """HTTP 401 应抛 RuntimeError。"""
    adapter = _make_adapter(enable_cache=False)
    http_err = urllib_error.HTTPError(
        url=_VOLC_TTS_URL, code=401,
        msg="Unauthorized", hdrs=None, fp=None,
    )
    with patch("speech.volc_tts.urllib_request.urlopen", side_effect=http_err):
        try:
            adapter.synthesize("test")
        except RuntimeError as e:
            assert "401" in str(e) or "HTTP" in str(e)
            print(f"  [✓] HTTP 401 报错: {str(e)[:40]}")
            return
    raise AssertionError("HTTP 401 应报 RuntimeError")


def test_error_url_network():
    """网络错误应抛 RuntimeError。"""
    adapter = _make_adapter(enable_cache=False)
    url_err = urllib_error.URLError("Connection refused")
    with patch("speech.volc_tts.urllib_request.urlopen", side_effect=url_err):
        try:
            adapter.synthesize("test")
        except RuntimeError as e:
            assert "网络" in str(e) or "错误" in str(e)
            print(f"  [✓] 网络错误报错: {str(e)[:40]}")
            return
    raise AssertionError("网络错误应报 RuntimeError")


def test_error_business_code():
    """业务错误码（code != 3000）应抛 ValueError。"""
    adapter = _make_adapter(enable_cache=False)
    err_resp = _FakeHTTPResponse(
        body=json.dumps({
            "code": 3001, "message": "Invalid voice_type",
        }).encode("utf-8"),
    )
    with patch("speech.volc_tts.urllib_request.urlopen", return_value=err_resp):
        try:
            adapter.synthesize("test")
        except ValueError as e:
            assert "3001" in str(e)
            print(f"  [✓] 业务错误码报错: {str(e)[:40]}")
            return
    raise AssertionError("业务错误码应报 ValueError")


def test_error_missing_data_field():
    """响应缺少 data 字段应报 ValueError。"""
    adapter = _make_adapter(enable_cache=False)
    bad_resp = _FakeHTTPResponse(
        body=json.dumps({
            "code": 3000, "message": "Success",
            # 缺少 data 字段
        }).encode("utf-8"),
    )
    with patch("speech.volc_tts.urllib_request.urlopen", return_value=bad_resp):
        try:
            adapter.synthesize("test")
        except ValueError as e:
            assert "data" in str(e)
            print("  [✓] 缺 data 字段报错")
            return
    raise AssertionError("缺 data 字段应报错")


def test_error_empty_audio():
    """返回空音频应报 ValueError。"""
    adapter = _make_adapter(enable_cache=False)
    empty_resp = _FakeHTTPResponse(
        body=json.dumps({
            "code": 3000, "message": "Success",
            "data": base64.b64encode(b"").decode("utf-8"),
        }).encode("utf-8"),
    )
    with patch("speech.volc_tts.urllib_request.urlopen", return_value=empty_resp):
        try:
            adapter.synthesize("test")
        except ValueError as e:
            assert "空" in str(e) or "empty" in str(e).lower()
            print("  [✓] 空音频报错")
            return
    raise AssertionError("空音频应报错")


# ============================================================
# 5. stream() 流式
# ============================================================
def test_stream_yields_chunks():
    """stream 应返回多个字节块。"""
    adapter = _make_adapter(enable_cache=False)
    fake_resp = _FakeHTTPResponse(
        body=json.dumps(_mock_api_response(b"X" * 10000)).encode("utf-8"),
    )
    with patch("speech.volc_tts.urllib_request.urlopen", return_value=fake_resp):
        chunks = list(adapter.stream("长文本测试"))

    assert len(chunks) > 1, f"应分多块，实际 {len(chunks)} 块"
    total = sum(len(c) for c in chunks)
    assert total == 10000
    print(f"  [✓] stream 分 {len(chunks)} 块，总 {total} bytes")


def test_stream_with_emotion():
    """stream 应支持 emotion 参数。"""
    adapter = _make_adapter(enable_cache=False)
    fake_resp = _FakeHTTPResponse(
        body=json.dumps(_mock_api_response(b"AUDIO")).encode("utf-8"),
    )
    captured = {}

    def _capture(req, **kw):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return fake_resp

    with patch("speech.volc_tts.urllib_request.urlopen", side_effect=_capture):
        list(adapter.stream("test", emotion="难过"))

    assert captured["body"]["audio"]["emotion"] == "sad"
    print("  [✓] stream 支持 emotion")


# ============================================================
# 6. 工厂注册
# ============================================================
def test_factory_registers_volc():
    """工厂应能实例化 cloud_volc provider。"""
    from speech.tts_factory import get_tts
    adapter = get_tts("cloud_volc")
    assert isinstance(adapter, VolcTTSAdapter)
    assert adapter.name == "cloud_volc"
    print("  [✓] 工厂注册 cloud_volc")


# ============================================================
# 7. ConversationManager.speak 注入情绪
# ============================================================
def _make_mock_llm(reply_text="嗯，我在呢"):
    llm = MagicMock()
    from llm import LLMResponse
    llm.chat.return_value = LLMResponse(text=reply_text, usage={"total_tokens": 10})
    return llm


def _make_mock_asr():
    asr = MagicMock()
    from speech import ASRResult
    asr.transcribe.return_value = ASRResult(text="你好")
    return asr


def _make_mock_tts():
    tts = MagicMock()
    from speech import TTSResult
    tts.default_voice = "zh_female_wanwan"
    tts.synthesize.return_value = TTSResult(
        audio=b"AUDIO", format="mp3", voice="zh_female_wanwan",
    )
    return tts


def test_speak_passes_emotion_to_tts():
    """speak 应把 AI 当前情绪传给 TTS 的 emotion 参数。"""
    from core.conversation import ConversationManager
    from core.emotion import AIEmotionEngine, EmotionState, EmotionType

    # 构造带情绪引擎的 manager
    emotion = AIEmotionEngine(persistence=False, empathy_weight=0.8)
    emotion.set_intimacy_score(50.0)
    emotion.update_from_user_emotion(EmotionState(
        primary=EmotionType.HAPPY, intensity=4.0,
    ))
    # 此时 AI 应处于开心状态
    assert emotion.get_label() == "开心"

    mock_tts = _make_mock_tts()
    m = ConversationManager(
        llm=_make_mock_llm(),
        asr=_make_mock_asr(),
        tts=mock_tts,
        emotion_engine=emotion,
        intimacy_manager=False,
        memory_manager=False,
        persist=False,
    )
    # speak 会播放音频，mock player 不可用
    with patch("speech.player.is_available", return_value=False):
        m.speak("你好呀")

    # 验证 TTS 收到 emotion 参数
    call_kwargs = mock_tts.synthesize.call_args.kwargs
    assert "emotion" in call_kwargs
    assert call_kwargs["emotion"] == "开心"
    print(f"  [✓] speak 注入情绪: {call_kwargs['emotion']}")


def test_speak_skips_emotion_when_neutral():
    """AI 情绪为中性时应跳过 emotion 参数（用默认）。"""
    from core.conversation import ConversationManager
    from core.emotion import AIEmotionEngine

    emotion = AIEmotionEngine(persistence=False)
    # 默认状态应为中性
    assert emotion.get_label() == "中性"

    mock_tts = _make_mock_tts()
    m = ConversationManager(
        llm=_make_mock_llm(),
        asr=_make_mock_asr(),
        tts=mock_tts,
        emotion_engine=emotion,
        intimacy_manager=False,
        memory_manager=False,
        persist=False,
    )
    with patch("speech.player.is_available", return_value=False):
        m.speak("你好")

    call_kwargs = mock_tts.synthesize.call_args.kwargs
    # 中性时不应传 emotion
    assert "emotion" not in call_kwargs
    print("  [✓] 中性时跳过 emotion")


def test_speak_no_emotion_engine():
    """情绪引擎关闭时不应传 emotion。"""
    from core.conversation import ConversationManager

    mock_tts = _make_mock_tts()
    m = ConversationManager(
        llm=_make_mock_llm(),
        asr=_make_mock_asr(),
        tts=mock_tts,
        emotion_engine=False,
        intimacy_manager=False,
        memory_manager=False,
        persist=False,
    )
    with patch("speech.player.is_available", return_value=False):
        m.speak("你好")

    call_kwargs = mock_tts.synthesize.call_args.kwargs
    assert "emotion" not in call_kwargs
    print("  [✓] 无情绪引擎时不传 emotion")


# ============================================================
# main
# ============================================================
def main() -> int:
    print("T2-06 火山引擎情感 TTS 单元测试\n")

    print("【1】情感映射")
    test_emotion_map_happy()
    test_emotion_map_sad()
    test_emotion_map_angry()
    test_emotion_map_neutral()
    test_emotion_map_excited()
    test_emotion_map_unknown_falls_back()
    test_supported_emotions()

    print("\n【2】请求构造")
    test_build_request_structure()
    test_build_request_app_section()
    test_build_request_audio_section()
    test_build_request_emotion_injected()
    test_build_request_reqid_unique()
    test_build_request_text()
    test_build_headers_bearer_format()

    print("\n【3】synthesize（mock HTTP）")
    test_synthesize_basic()
    test_synthesize_with_emotion()
    test_synthesize_empty_text()
    test_synthesize_no_credentials()
    test_synthesize_cache_hit()
    test_synthesize_cache_miss_writes()
    test_synthesize_different_emotions_different_cache()

    print("\n【4】错误处理")
    test_error_http_401()
    test_error_url_network()
    test_error_business_code()
    test_error_missing_data_field()
    test_error_empty_audio()

    print("\n【5】stream() 流式")
    test_stream_yields_chunks()
    test_stream_with_emotion()

    print("\n【6】工厂注册")
    test_factory_registers_volc()

    print("\n【7】ConversationManager.speak 注入情绪")
    test_speak_passes_emotion_to_tts()
    test_speak_skips_emotion_when_neutral()
    test_speak_no_emotion_engine()

    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
