"""T3-05 唤醒点亮交互测试。

覆盖：
1. 后端：/api/live2d/wake 返回 ok + state + wake_count + greeting（轻应声）
2. 后端：/api/live2d/status 暴露 wake_count / last_wake_ts / wake_greeting
3. 后端：conversation 的 _record_wake 递增计数、check_wake_word 文本命中会点亮、wake_greeting 读配置
4. 配置：config.yaml wake_word 段含 greeting / light_up_duration
5. 前端：manager.js 含 wakeUp / goToSleep / setAwake / onAwakeChange / awake
6. 前端：Live2DView.vue 含 sleep/lit 点亮视觉、wake_count 轮询、轻应声 speak、窗口弹出 window.show
7. 前端：preload.js / main.js window:show IPC（窗口从托盘弹出）

注：GUI 渲染在浏览器/Electron 中手动验证，这里测后端 API 逻辑 + 前端结构。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_ROOT = ROOT
UI_DIR = ROOT / "ui"


# ============================================================
# 1. 后端 API 端点
# ============================================================
def _mock_manager():
    from core.conversation import ConversationManager
    return ConversationManager(
        llm=MagicMock(), asr=MagicMock(), tts=MagicMock(),
        emotion_engine=False, intimacy_manager=False,
        memory_manager=False, proactive_manager=False,
        wake_word_detector=False, interruption_detector=False,
        persist=False,
    )


def test_live2d_wake_endpoint_returns_greeting():
    """唤醒端点应返回 ok + state + wake_count + greeting。"""
    from fastapi.testclient import TestClient
    from server import app, reset_manager
    import server

    m = _mock_manager()
    server._manager = m

    client = TestClient(app)
    resp = client.post("/api/live2d/wake")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["state"] == "idle"
    assert data["wake_count"] == 1            # 唤醒计数递增
    assert isinstance(data["greeting"], str)   # 轻应声文案
    assert data["greeting"] != ""
    print(f"  [✓] /api/live2d/wake 返回 greeting={data['greeting']!r}")

    reset_manager()


def test_live2d_wake_increments_count():
    """连续唤醒应递增 wake_count。"""
    from fastapi.testclient import TestClient
    from server import app, reset_manager
    import server

    m = _mock_manager()
    server._manager = m
    client = TestClient(app)
    client.post("/api/live2d/wake")
    client.post("/api/live2d/wake")
    assert m._wake_count == 2
    print("  [✓] wake_count 随唤醒递增")
    reset_manager()


def test_live2d_status_exposes_wake_fields():
    """/api/live2d/status 暴露 wake_count / last_wake_ts / wake_greeting。"""
    from fastapi.testclient import TestClient
    from server import app, reset_manager
    import server

    m = _mock_manager()
    server._manager = m
    client = TestClient(app)
    resp = client.get("/api/live2d/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "wake_count" in data
    assert "last_wake_ts" in data
    assert "wake_greeting" in data
    print("  [✓] /api/live2d/status 含唤醒点亮字段")
    reset_manager()


def test_check_wake_word_records_wake():
    """文本模式命中唤醒词应触发 _record_wake + wake 事件。"""
    from core.conversation import ConversationManager
    from speech.wake_word import TextWakeWordDetector

    det = TextWakeWordDetector(keyword="陪伴助手")
    m = ConversationManager(
        llm=MagicMock(), asr=MagicMock(), tts=MagicMock(),
        emotion_engine=False, intimacy_manager=False,
        memory_manager=False, proactive_manager=False,
        wake_word_detector=det, interruption_detector=False,
        persist=False,
    )
    assert m._wake_count == 0
    triggered = m.check_wake_word("陪伴助手？")
    assert triggered is True
    assert m._wake_count == 1
    # 未命中不计数
    assert m.check_wake_word("随便聊聊") is False
    assert m._wake_count == 1
    print("  [✓] 文本唤醒命中 → wake_count 递增")


def test_wake_greeting_reads_config():
    """wake_greeting 应读取 config.wake_word.greeting。"""
    from core.conversation import ConversationManager
    m = ConversationManager(
        llm=MagicMock(), asr=MagicMock(), tts=MagicMock(),
        emotion_engine=False, intimacy_manager=False,
        memory_manager=False, proactive_manager=False,
        wake_word_detector=False, interruption_detector=False,
        persist=False,
    )
    g = m.wake_greeting()
    assert isinstance(g, str) and g != ""
    print(f"  [✓] wake_greeting={g!r}（读配置）")


# ============================================================
# 2. 配置
# ============================================================
def test_config_has_wake_light_settings():
    """config.yaml wake_word 段含 greeting / light_up_duration。"""
    p = APP_ROOT / "config.yaml"
    content = p.read_text(encoding="utf-8")
    for key in ["greeting", "light_up_duration"]:
        assert key in content, f"config.yaml 缺少 {key}"
    from utils.config import config as cfg
    assert cfg.get("wake_word.greeting", "") != ""
    assert cfg.get("wake_word.light_up_duration", 0) > 0
    print("  [✓] config.yaml 唤醒点亮配置")


# ============================================================
# 3. 前端 manager.js
# ============================================================
def test_manager_js_has_wake_light():
    """manager.js 含 wakeUp / goToSleep / setAwake / awake / onAwakeChange。"""
    p = UI_DIR / "src" / "live2d" / "manager.js"
    content = p.read_text(encoding="utf-8")
    for kw in ["wakeUp", "goToSleep", "setAwake", "onAwakeChange", "TapBody"]:
        assert kw in content, f"manager.js 缺少 {kw}"
    print("  [✓] manager.js 唤醒点亮接口完整")


# ============================================================
# 4. 前端 Live2DView.vue
# ============================================================
def test_live2d_view_has_wake_light():
    """Live2DView.vue 含点亮视觉、wake_count 轮询、轻应声、窗口弹出。"""
    p = UI_DIR / "src" / "components" / "Live2DView.vue"
    content = p.read_text(encoding="utf-8")
    for kw in [
        "sleeping", "lit", "light-breath",                      # 点亮视觉
        "wake_count", "startWakePoller", "triggerLightUp",      # 轮询探测唤醒
        "store.speak",                                          # 轻应声
        "window?.show",                                         # 窗口弹出（可选链）
    ]:
        assert kw in content, f"Live2DView.vue 缺少 {kw}"
    print("  [✓] Live2DView.vue 唤醒点亮交互完整")


# ============================================================
# 5. Electron window:show IPC
# ============================================================
def test_main_js_has_window_show():
    """main.js 注册 window:show IPC（托盘弹出窗口）。"""
    p = UI_DIR / "electron" / "main.js"
    content = p.read_text(encoding="utf-8")
    assert "window:show" in content
    assert "showMainWindow" in content
    print("  [✓] main.js window:show IPC")


def test_preload_has_window_show():
    """preload.js 暴露 window.show。"""
    p = UI_DIR / "electron" / "preload.js"
    content = p.read_text(encoding="utf-8")
    assert "show" in content
    assert "window:show" in content
    print("  [✓] preload.js window.show")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        test_live2d_wake_endpoint_returns_greeting,
        test_live2d_wake_increments_count,
        test_live2d_status_exposes_wake_fields,
        test_check_wake_word_records_wake,
        test_wake_greeting_reads_config,
        test_config_has_wake_light_settings,
        test_manager_js_has_wake_light,
        test_live2d_view_has_wake_light,
        test_main_js_has_window_show,
        test_preload_has_window_show,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  [✗] {t.__name__} 失败: {e}")
            traceback.print_exc()

    print(f"\n{'='*50}")
    print(f"结果: {passed} 通过, {failed} 失败 / 共 {len(tests)}")
    if failed == 0:
        print("🎉 全部通过！")