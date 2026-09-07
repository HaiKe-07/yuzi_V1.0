"""T1-08 HTTP API 与桌面端骨架测试。

重点验证：
1. FastAPI app 创建无异常，路由表完整
2. /api/health 健康检查返回 ok
3. /api/status 返回对话状态
4. /api/chat 文本对话（用 mock manager 注入）
5. /api/settings GET 读配置
6. /api/settings PUT 改配置 + reset_manager
7. Electron 骨架：package.json / main.js / preload.js 文件齐全
8. Vue 组件：App.vue / ChatPanel.vue / SettingsPanel.vue 文件齐全
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 在导入 server 之前先注入 mock manager，避免触发真实 LLM/ASR/TTS
import server as server_mod
from core.conversation import ConversationState
from llm import LLMResponse
from speech import ASRResult, TTSResult


def make_mock_manager():
    m = MagicMock()
    m.state = ConversationState.IDLE
    m.status.return_value = {
        "state": "idle", "history_len": 0, "max_turns": 20,
    }
    m.history = []
    m.text_chat.return_value = "嗯，我在听呢"
    m.speak.return_value = TTSResult(
        audio=b"FAKE", format="mp3", voice="nova", cached=False,
    )
    m.load_history.return_value = 0
    return m


def setup_manager():
    server_mod.reset_manager()
    server_mod._manager = make_mock_manager()


# ---------- 测试 ----------
def test_app_routes():
    app = server_mod.create_app()
    routes = {r.path for r in app.routes}
    expected = {
        "/api/health", "/api/status", "/api/history",
        "/api/chat", "/api/speak",
        "/api/settings",
    }
    assert expected <= routes, f"缺失路由: {expected - routes}"
    print(f"  [✓] 路由表完整: {sorted(expected)}")


def test_health():
    setup_manager()
    from fastapi.testclient import TestClient
    client = TestClient(server_mod.app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    print("  [✓] /api/health 返回 ok=True")


def test_status():
    setup_manager()
    from fastapi.testclient import TestClient
    client = TestClient(server_mod.app)
    r = client.get("/api/status")
    assert r.status_code == 200
    j = r.json()
    assert j["state"] == "idle"
    print("  [✓] /api/status 返回对话状态")


def test_chat():
    setup_manager()
    from fastapi.testclient import TestClient
    client = TestClient(server_mod.app)
    r = client.post("/api/chat", json={"text": "你好"})
    assert r.status_code == 200
    assert r.json()["reply"] == "嗯，我在听呢"
    server_mod._manager.text_chat.assert_called_once_with("你好")
    print("  [✓] /api/chat 文本对话")


def test_speak():
    setup_manager()
    from fastapi.testclient import TestClient
    client = TestClient(server_mod.app)
    r = client.post("/api/speak", json={"text": "你好呀"})
    assert r.status_code == 200
    j = r.json()
    assert j["voice"] == "nova"
    assert j["cached"] is False
    assert j["bytes"] == 4  # b"FAKE"
    print("  [✓] /api/speak TTS 调用")


def test_get_settings():
    setup_manager()
    from fastapi.testclient import TestClient
    client = TestClient(server_mod.app)
    r = client.get("/api/settings")
    assert r.status_code == 200
    j = r.json()
    assert "llm" in j and "asr" in j and "tts" in j
    print("  [✓] /api/settings GET")


def test_update_settings():
    setup_manager()
    from fastapi.testclient import TestClient
    client = TestClient(server_mod.app)
    r = client.put("/api/settings", json={
        "user_name": "阿明",
        "tts_voice": "shimmer",
    })
    assert r.status_code == 200
    applied = r.json()["applied"]
    assert "user.name" in applied
    assert "tts.openai.voice" in applied

    # 验证配置确实写入了
    from utils.config import config
    assert config.get("user.name") == "阿明"
    assert config.get("tts.openai.voice") == "shimmer"
    print("  [✓] /api/settings PUT 改配置 + manager 重置")


def test_chat_error_handling():
    """LLM 异常应返回 500，不崩进程。"""
    setup_manager()
    server_mod._manager.text_chat.side_effect = RuntimeError("LLM 挂了")
    from fastapi.testclient import TestClient
    client = TestClient(server_mod.app)
    r = client.post("/api/chat", json={"text": "你好"})
    assert r.status_code == 500
    assert "LLM 挂了" in r.json()["detail"]
    print("  [✓] LLM 异常返回 500")


def test_chat_empty_text():
    """空文本 chat 走 manager，manager 返回空字符串。"""
    setup_manager()
    server_mod._manager.text_chat.return_value = ""
    from fastapi.testclient import TestClient
    client = TestClient(server_mod.app)
    r = client.post("/api/chat", json={"text": ""})
    assert r.status_code == 200
    assert r.json()["reply"] == ""
    print("  [✓] 空文本不报错")


# ---------- 桌面端骨架文件检查 ----------
def test_electron_scaffold_files():
    ui_dir = ROOT / "ui"
    required = [
        "package.json",
        "vite.config.js",
        "index.html",
        "electron/main.js",
        "electron/preload.js",
        "src/main.js",
        "src/App.vue",
        "src/components/TitleBar.vue",
        "src/components/ChatPanel.vue",
        "src/components/SettingsPanel.vue",
        "src/stores/companion.js",
        "src/styles/main.css",
    ]
    for rel in required:
        p = ui_dir / rel
        assert p.exists(), f"缺失文件: {rel}"
    print(f"  [✓] Electron + Vue 骨架 {len(required)} 个文件齐全")


def test_package_json_valid():
    p = ROOT / "ui" / "package.json"
    j = json.loads(p.read_text(encoding="utf-8"))
    assert j["name"] == "ai-companion-ui"
    assert j["main"] == "electron/main.js"
    assert "vue" in j["dependencies"]
    assert "electron" in j["devDependencies"]
    print("  [✓] package.json 字段完整（name/main/deps）")


def test_electron_main_uses_backend_port():
    p = ROOT / "ui" / "electron" / "main.js"
    content = p.read_text(encoding="utf-8")
    assert "BACKEND_PORT" in content
    assert "18731" in content            # 与 config 默认端口对齐
    assert "server.py" in content        # 引用了 Python 后端
    assert "waitForBackend" in content   # 健康检查轮询
    print("  [✓] electron/main.js 启动 Python 后端 + 健康检查")


def test_vue_components_reference_api():
    """Vue 组件应通过 window.companion 调用后端。"""
    files = [
        ROOT / "ui" / "src" / "stores" / "companion.js",
        ROOT / "ui" / "src" / "components" / "ChatPanel.vue",
        ROOT / "ui" / "src" / "components" / "SettingsPanel.vue",
    ]
    for f in files:
        content = f.read_text(encoding="utf-8")
        assert "companion" in content, f"{f.name} 未引用 companion API"
    print("  [✓] Vue 组件通过 window.companion 调用后端")


def main() -> int:
    print("T1-08 HTTP API + 桌面端骨架测试")
    test_app_routes()
    test_health()
    test_status()
    test_chat()
    test_speak()
    test_get_settings()
    test_update_settings()
    test_chat_error_handling()
    test_chat_empty_text()
    test_electron_scaffold_files()
    test_package_json_valid()
    test_electron_main_uses_backend_port()
    test_vue_components_reference_api()
    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
