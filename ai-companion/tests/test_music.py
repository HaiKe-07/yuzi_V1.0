"""T3-07 酷狗音乐基础控制测试。

覆盖：
1. parse_command：点歌/暂停/继续/切歌/音量/无效指令全部解析正确
2. MusicController：启用状态、平台判别、handle 分派、describe 工具声明
3. 模块可导入、config 读取 tools.music.enabled/kugou_path

说明：真实操控酷狗需 Windows + 已安装酷狗；这里验证纯逻辑与降级行为。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.music import (  # noqa: E402
    INTENT_NEXT,
    INTENT_NONE,
    INTENT_PAUSE,
    INTENT_PLAY,
    INTENT_PREV,
    INTENT_RESUME,
    INTENT_VOLUME,
    MusicController,
    parse_command,
)


# ---------- parse_command ----------
def test_parse_play():
    c = parse_command("播放 晴天")
    assert c.intent == INTENT_PLAY
    assert c.song in ("晴天", " 晴天")
    print(f"  [✓] 播放指令 → song={c.song!r}")


def test_parse_play_variants():
    for txt, expect_song in [("放一首 稻香", "稻香"), ("来一首 稻香", "稻香"), ("帮我放周杰伦的歌", "周杰伦的歌")]:
        c = parse_command(txt)
        assert c.intent == INTENT_PLAY
        assert expect_song in c.song, (txt, c.song)
    print("  [✓] 多种点歌措辞")


def test_parse_pause_resume():
    assert parse_command("先暂停").intent == INTENT_PAUSE
    assert parse_command("暂停一下").intent == INTENT_PAUSE
    assert parse_command("继续播放").intent == INTENT_RESUME
    print("  [✓] 暂停/继续")


def test_parse_next_prev():
    assert parse_command("下一首").intent == INTENT_NEXT
    assert parse_command("切一首歌").intent == INTENT_NEXT
    assert parse_command("上一首").intent == INTENT_PREV
    print("  [✓] 切歌")


def test_parse_volume():
    c = parse_command("音量调到 60")
    assert c.intent == INTENT_VOLUME
    assert c.volume == 60
    c2 = parse_command("把声音调低到20")
    assert c2.intent == INTENT_VOLUME and c2.volume == 20
    c3 = parse_command("音量100")
    assert c3.volume == 100
    print("  [✓] 音量指令")


def test_parse_volume_clamp():
    c = parse_command("音量150")
    assert c.intent == INTENT_VOLUME and c.volume == 100
    print("  [✓] 音量为 0-100 裁剪")


def test_parse_none():
    assert parse_command("") .intent == INTENT_NONE
    assert parse_command("今天天气不错").intent == INTENT_NONE
    print("  [✓] 无效/无关指令 → none")


# ---------- MusicController ----------
def _make_ctrl(enabled=False, path=""):
    cfg = {"tools": {"music": {"enabled": enabled, "kugou_path": path}}}
    return MusicController(cfg)


def test_enabled_property():
    assert _make_ctrl(enabled=True).enabled is True
    assert _make_ctrl(enabled=False).enabled is False
    print("  [✓] enabled 由 config 决定")


def test_platform_without_win_graceful():
    """非 Windows 下 play 返回降级提示，不抛异常。"""
    c = _make_ctrl(enabled=True)
    if sys.platform != "win32":
        r = c.play("晴天")
        assert "无法操控" in r or "已开始播放" in r or "未启用" in r
        assert parse_command("播放 晴天").intent == INTENT_PLAY
    print("  [✓] 跨平台降级（无异常）")


def test_handle_dispatch(monkeypatch=None):
    c = _make_ctrl(enabled=True)
    if sys.platform == "win32":
        # Windows 下替换发送按键/剪贴板为 no-op，验证分派路径
        import tools.music as m
        monkeypatch.setattr(m, "_win_sendkey", lambda *_a: None)
        monkeypatch.setattr(m, "_send", lambda *_a: None)
        assert "已继续播放" in c.handle("继续")   # 无歌名 play → resume
        r = c.handle("下一首")
        assert "已切到下一首" in r
    else:
        # 非 Windows：确保 handle 正常返回（降级文案），不崩
        assert isinstance(c.handle("播放 晴天"), str)
    print("  [✓] handle 分派")


def test_describe_schema():
    d = MusicController({"tools": {"music": {"enabled": True}}}).describe()
    assert d["name"] == "music_control"
    assert d["parameters"]["required"] == ["action"]
    assert INTENT_PLAY in d["parameters"]["properties"]["action"]["enum"]
    print("  [✓] LLM 工具 schema")


def test_config_read():
    from utils.config import config as cfg
    enabled = cfg.get("tools.music.enabled", False)
    assert isinstance(enabled, bool)
    assert "kugou_path" in (cfg.get("tools.music", {}) or {})
    print("  [✓] config 读取 tools.music")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        test_parse_play,
        test_parse_play_variants,
        test_parse_pause_resume,
        test_parse_next_prev,
        test_parse_volume,
        test_parse_volume_clamp,
        test_parse_none,
        test_enabled_property,
        test_platform_without_win_graceful,
        test_handle_dispatch,
        test_describe_schema,
        test_config_read,
    ]

    passed = failed = 0
    for t in tests:
        try:
            # test_handle_dispatch 需要 monkeypatch 参数，包装一下
            if t is test_handle_dispatch:
                class _MP:
                    def setattr(self, mod, name, fn): setattr(mod, name, fn)
                t(_MP())
            else:
                t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  [✗] {t.__name__} 失败: {e}")
            traceback.print_exc()

    print(f"\n结果: {passed} 通过, {failed} 失败 / 共 {len(tests)}")