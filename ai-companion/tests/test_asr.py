"""T1-04 ASR 单元测试。

无需真实麦克风/API key：用 mock 与生成合成 WAV 验证：
1. _pcm_to_wav 包装正确（WAV header / 数据长度）
2. ASRResult 字段映射正确
3. OpenAIWhisperAdapter.chat 接口契约（mock client）
4. 工厂能按 provider 选到适配器
5. record() 在缺 sounddevice 时给出可读报错
"""
from __future__ import annotations

import io
import sys
import wave
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from speech import ASRResult, BaseASR, get_asr
from speech.recorder import _pcm_to_wav


def test_pcm_to_wav_header():
    """WAV 头应遵循标准格式，便于 ASR API 解析。"""
    pcm = b"\x00\x00" * 16000  # 1 秒静音 16kHz mono
    wav = _pcm_to_wav(pcm, sample_rate=16000, channels=1, sample_width=2)

    with wave.open(io.BytesIO(wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 16000
        assert wf.readframes(16000) == pcm

    # RIFF 头
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    print("  [✓] _pcm_to_wav 生成标准 WAV（RIFF/WAVE/单声道/16bit/16kHz）")


def test_asr_result_defaults():
    r = ASRResult(text="你好")
    assert r.text == "你好"
    assert r.language == "zh"
    assert r.duration == 0.0
    assert r.confidence == 0.0
    print("  [✓] ASRResult 默认字段")


def test_empty_audio_returns_empty():
    """空音频不应触发 API 调用。"""
    adapter = get_asr("cloud_openai_whisper")
    # 不设 key 也能跑（早返回路径）
    adapter._client = MagicMock()  # 防止意外触发
    res = adapter.transcribe(b"")
    assert res.text == ""
    # 不应调用 client
    adapter._client.audio.transcriptions.create.assert_not_called()
    print("  [✓] 空/过短音频不触发 API 调用")


def test_whisper_transcribe_mock():
    """mock OpenAI 验证 transcribe 字段映射。"""
    adapter = get_asr("cloud_openai_whisper")

    fake_resp = MagicMock()
    fake_resp.text = "  你好世界  "
    fake_resp.language = "zh"
    fake_resp.duration = 1.5
    fake_resp.segments = []  # 无 segments，confidence 应为 0
    fake_resp.model_dump.return_value = {"id": "fake"}

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = fake_resp
    adapter._client = fake_client

    pcm = b"\x00\x00" * 16000
    wav = _pcm_to_wav(pcm, 16000, 1, 2)

    res = adapter.transcribe(wav, prompt="用户在聊科幻电影")
    assert isinstance(res, ASRResult)
    assert res.text == "你好世界"  # 去空白
    assert res.language == "zh"
    assert res.duration == 1.5

    call_kwargs = fake_client.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["model"] == "whisper-1"
    assert call_kwargs["language"] == "zh"
    assert call_kwargs["response_format"] == "verbose_json"
    assert call_kwargs["prompt"] == "用户在聊科幻电影"
    # file 参数结构 (filename, fileobj, mime)
    assert call_kwargs["file"][0] == "audio.wav"
    assert call_kwargs["file"][2] == "audio/wav"
    print("  [✓] transcribe 返回 ASRResult，参数 model/language/prompt 正确")


def test_whisper_confidence_from_segments():
    """有 segments 时 confidence 由 avg_logprob 转换。"""
    import math
    adapter = get_asr("cloud_openai_whisper")

    fake_seg = MagicMock()
    fake_seg.avg_logprob = -0.2  # exp(-0.2) ≈ 0.819
    fake_resp = MagicMock()
    fake_resp.text = "test"
    fake_resp.language = "zh"
    fake_resp.duration = 1.0
    fake_resp.segments = [fake_seg]
    fake_resp.model_dump.return_value = {}

    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = fake_resp
    adapter._client = fake_client

    wav = _pcm_to_wav(b"\x00\x00" * 800, 16000, 1, 2)
    res = adapter.transcribe(wav)
    assert abs(res.confidence - math.exp(-0.2)) < 0.01
    print(f"  [✓] confidence 由 avg_logprob 转换 ≈ {res.confidence:.3f}")


def test_factory_unknown_provider():
    try:
        get_asr("nonexistent")
    except ValueError as e:
        assert "nonexistent" in str(e)
        print("  [✓] 工厂拒绝未知 provider")
        return
    raise AssertionError("未知 provider 应抛 ValueError")


def test_xfyun_raises_without_credentials():
    """讯飞骨架：无凭证应可读地报错。"""
    from speech.xfyun_asr import XfyunASRAdapter
    adapter = XfyunASRAdapter()
    # 凭证都空时应早返回 RuntimeError，而不是 NotImplementedError
    try:
        adapter.transcribe(b"\x00\x00" * 100)
    except RuntimeError as e:
        assert "凭证" in str(e)
        print("  [✓] 讯飞骨架无凭证时报 RuntimeError（可读）")
        return
    except NotImplementedError:
        # 如果凭证配置了则走到 NotImplementedError，也算合理
        print("  [✓] 讯飞骨架有凭证时报 NotImplementedError（待实现）")
        return
    raise AssertionError("讯飞骨架应报错")


def main() -> int:
    print("T1-04 ASR 单元测试")
    test_pcm_to_wav_header()
    test_asr_result_defaults()
    test_empty_audio_returns_empty()
    test_whisper_transcribe_mock()
    test_whisper_confidence_from_segments()
    test_factory_unknown_provider()
    test_xfyun_raises_without_credentials()
    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
