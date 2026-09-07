"""T1-05 TTS 单元测试。

无需真实 API key / 麦克风：用 mock 验证：
1. TTSResult 字段映射
2. 缓存命中/未命中逻辑
3. 缓存淘汰机制（LRU by atime）
4. 空/异常文本不触发 API
5. 音色校验回退
6. 工厂选择适配器
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from speech import TTSResult, TTSCache, get_tts


def test_tts_result_defaults():
    r = TTSResult(audio=b"x")
    assert r.audio == b"x"
    assert r.format == "mp3"
    assert r.sample_rate == 24000
    assert r.cached is False
    print("  [✓] TTSResult 默认字段")


def test_cache_hit_and_miss():
    with tempfile.TemporaryDirectory() as tmp:
        cache = TTSCache(cache_dir=tmp, max_entries=10)
        # 未命中
        assert cache.get("hello", "nova", "mp3") is None
        # 写入
        cache.put("hello", "nova", b"AUDIO_BYTES", "mp3")
        # 命中
        data = cache.get("hello", "nova", "mp3")
        assert data == b"AUDIO_BYTES"
        print("  [✓] 缓存命中/未命中逻辑")


def test_cache_lru_eviction():
    """超出 max_entries 时按 atime 淘汰最旧。"""
    with tempfile.TemporaryDirectory() as tmp:
        cache = TTSCache(cache_dir=tmp, max_entries=3)
        # 写入 4 条，第一条应被淘汰
        for i in range(4):
            cache.put(f"text{i}", "nova", b"bytes", "mp3")
            time.sleep(0.05)
            os.utime(cache._path_for(
                cache._make_key(f"text{i}", "nova", ""), "mp3"
            ), None)
            time.sleep(0.05)
        files = list(cache.cache_dir.iterdir())
        assert len(files) == 3, f"应淘汰到 3 条，实际 {len(files)}"
        # 第一条应已不在
        assert cache.get("text0", "nova", "mp3") is None
        # 最后一条应还在
        assert cache.get("text3", "nova", "mp3") == b"bytes"
        print("  [✓] LRU 淘汰机制")


def test_cache_clear():
    with tempfile.TemporaryDirectory() as tmp:
        cache = TTSCache(cache_dir=tmp, max_entries=10)
        cache.put("a", "v", b"x", "mp3")
        cache.put("b", "v", b"y", "mp3")
        assert cache.clear() == 2
        assert cache.get("a", "v", "mp3") is None
        print("  [✓] 缓存清空")


def test_empty_text_returns_empty():
    """空文本不应触发 API 调用。"""
    adapter = get_tts("cloud_openai_tts")
    adapter._client = MagicMock()
    adapter.cache = None  # 防止缓存命中干扰

    res = adapter.synthesize("")
    assert res.audio == b""
    # 不应调用 client
    adapter._client.audio.speech.create.assert_not_called()
    print("  [✓] 空文本不触发 API")


def test_voice_validation_fallback():
    """非法音色应回退到 default_voice。"""
    adapter = get_tts("cloud_openai_tts")
    adapter._client = MagicMock()
    adapter.cache = None

    fake_resp = MagicMock()
    fake_resp.content = b"FAKE_AUDIO"
    adapter._client.audio.speech.create.return_value = fake_resp

    adapter.synthesize("你好", voice="invalid_voice_name")
    call_kwargs = adapter._client.audio.speech.create.call_args.kwargs
    assert call_kwargs["voice"] == "nova", \
        f"非法音色应回退到 nova，实际 {call_kwargs['voice']}"
    print("  [✓] 非法音色回退到 default_voice")


def test_synthesize_mock():
    """mock OpenAI 验证 synthesize 字段映射。"""
    adapter = get_tts("cloud_openai_tts")
    adapter.cache = None  # 关缓存，专注于 API 调用验证

    fake_resp = MagicMock()
    fake_resp.content = b"REAL_AUDIO_BYTES"
    fake_client = MagicMock()
    fake_client.audio.speech.create.return_value = fake_resp
    adapter._client = fake_client

    res = adapter.synthesize(
        "你好呀",
        voice="shimmer",
        speed=1.2,
    )
    assert isinstance(res, TTSResult)
    assert res.audio == b"REAL_AUDIO_BYTES"
    assert res.voice == "shimmer"
    assert res.format == "mp3"
    assert res.cached is False

    call_kwargs = fake_client.audio.speech.create.call_args.kwargs
    assert call_kwargs["model"] == "tts-1"
    assert call_kwargs["voice"] == "shimmer"
    assert call_kwargs["input"] == "你好呀"
    assert call_kwargs["response_format"] == "mp3"
    assert call_kwargs["speed"] == 1.2
    print("  [✓] synthesize 参数契约正确（model/voice/input/format/speed）")


def test_cache_hit_skips_api():
    """缓存命中时应跳过 API 调用，cached=True。"""
    adapter = get_tts("cloud_openai_tts")
    # 预填缓存
    adapter.cache.put("缓存的句子", "nova", b"CACHED", "mp3",
                      extra=f"{adapter.model}:1.0")

    fake_client = MagicMock()
    adapter._client = fake_client

    res = adapter.synthesize("缓存的句子")
    assert res.cached is True
    assert res.audio == b"CACHED"
    fake_client.audio.speech.create.assert_not_called()
    print("  [✓] 缓存命中跳过 API 调用")


def test_factory_unknown_provider():
    try:
        get_tts("nonexistent")
    except ValueError as e:
        assert "nonexistent" in str(e)
        print("  [✓] 工厂拒绝未知 provider")
        return
    raise AssertionError("未知 provider 应抛 ValueError")


def test_volc_raises_without_credentials():
    """火山骨架：无凭证应可读地报错。"""
    from speech.volc_tts import VolcTTSAdapter
    adapter = VolcTTSAdapter()
    try:
        adapter.synthesize("你好")
    except RuntimeError as e:
        assert "凭证" in str(e)
        print("  [✓] 火山骨架无凭证时报 RuntimeError（可读）")
        return
    raise AssertionError("火山骨架应报错")


def main() -> int:
    print("T1-05 TTS 单元测试")
    test_tts_result_defaults()
    test_cache_hit_and_miss()
    test_cache_lru_eviction()
    test_cache_clear()
    test_empty_text_returns_empty()
    test_voice_validation_fallback()
    test_synthesize_mock()
    test_cache_hit_skips_api()
    test_factory_unknown_provider()
    test_volc_raises_without_credentials()
    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
