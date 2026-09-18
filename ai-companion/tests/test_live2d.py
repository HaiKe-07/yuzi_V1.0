"""T3-01 Live2D 集成测试。

覆盖后端部分：
1. /api/live2d/status 端点返回 AI 情绪 + 对话状态
2. /api/live2d/wake 端点触发 wake 事件
3. 情绪标签 → Live2D 表情映射逻辑（后端验证）
4. 前端文件结构验证（manager.js / Live2DView.vue 存在且结构完整）

注：前端 PIXI.js 渲染测试在浏览器/Electron 中手动验证，
    这里只测 Python 后端 API + 情绪映射逻辑。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试用：禁用所有外部依赖
import os
os.environ.setdefault("DEEPSEEK_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")


# ============================================================
# 1. 后端 API 端点测试
# ============================================================
def test_live2d_status_endpoint():
    """/api/live2d/status 返回 AI 情绪和对话状态。"""
    from fastapi.testclient import TestClient
    from server import app, reset_manager
    from core.conversation import ConversationManager

    # 注入 mock manager（避免真实 LLM/ASR/TTS 初始化）
    mock_manager = ConversationManager(
        llm=MagicMock(), asr=MagicMock(), tts=MagicMock(),
        emotion_engine=False, intimacy_manager=False,
        memory_manager=False, proactive_manager=False,
        wake_word_detector=False, interruption_detector=False,
        persist=False,
    )
    import server
    server._manager = mock_manager

    client = TestClient(app)
    resp = client.get("/api/live2d/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "ai_emotion" in data
    assert "state" in data
    assert "wake_word" in data
    assert "wake_listening" in data
    assert "intimacy_level" in data
    assert "intimacy_score" in data
    print("  [✓] /api/live2d/status 返回完整字段")

    # 清理
    reset_manager()


def test_live2d_wake_endpoint():
    """/api/live2d/wake 触发 wake 事件。"""
    from fastapi.testclient import TestClient
    from server import app, reset_manager
    from core.conversation import ConversationManager

    mock_manager = ConversationManager(
        llm=MagicMock(), asr=MagicMock(), tts=MagicMock(),
        emotion_engine=False, intimacy_manager=False,
        memory_manager=False, proactive_manager=False,
        wake_word_detector=False, interruption_detector=False,
        persist=False,
    )
    import server
    server._manager = mock_manager

    # 注册 wake 事件回调
    called = []
    mock_manager.on("wake", lambda text="": called.append(text))

    client = TestClient(app)
    resp = client.post("/api/live2d/wake")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["state"] == "idle"
    assert len(called) == 1
    print("  [✓] /api/live2d/wake 触发 wake 事件")

    reset_manager()


def test_live2d_status_with_emotion():
    """情绪引擎开启时 /api/live2d/status 应返回情绪标签。"""
    from fastapi.testclient import TestClient
    from server import app, reset_manager
    from core.conversation import ConversationManager

    # 用 mock 情绪引擎
    mock_emotion = MagicMock()
    mock_emotion.get_label.return_value = "开心"
    mock_manager = ConversationManager(
        llm=MagicMock(), asr=MagicMock(), tts=MagicMock(),
        emotion_engine=mock_emotion,
        intimacy_manager=False, memory_manager=False,
        proactive_manager=False, wake_word_detector=False,
        interruption_detector=False, persist=False,
    )
    import server
    server._manager = mock_manager

    client = TestClient(app)
    resp = client.get("/api/live2d/status")
    data = resp.json()
    assert data["ai_emotion"] == "开心"
    print("  [✓] 情绪引擎开启时返回情绪标签")

    reset_manager()


# ============================================================
# 2. 前端文件结构验证
# ============================================================
def test_live2d_manager_js_exists():
    """manager.js 文件存在且导出 Live2DManager 类。"""
    manager_path = ROOT / "ui" / "src" / "live2d" / "manager.js"
    assert manager_path.exists(), f"manager.js 不存在: {manager_path}"
    content = manager_path.read_text(encoding="utf-8")
    assert "class Live2DManager" in content
    assert "setExpression" in content
    assert "startMotion" in content
    assert "lipSync" in content
    assert "onWake" in content
    assert "EMOTION_TO_EXPRESSION" in content
    print("  [✓] manager.js 结构完整")


def test_live2d_view_vue_exists():
    """Live2DView.vue 组件存在且包含 canvas + 占位符。"""
    vue_path = ROOT / "ui" / "src" / "components" / "Live2DView.vue"
    assert vue_path.exists(), f"Live2DView.vue 不存在: {vue_path}"
    content = vue_path.read_text(encoding="utf-8")
    assert "<canvas" in content
    assert "placeholder" in content
    assert "getLive2DManager" in content
    assert "onMounted" in content
    print("  [✓] Live2DView.vue 结构完整")


def test_app_vue_includes_live2d():
    """App.vue 引入了 Live2DView 组件。"""
    vue_path = ROOT / "ui" / "src" / "App.vue"
    content = vue_path.read_text(encoding="utf-8")
    assert "Live2DView" in content
    assert "avatar-panel" in content
    assert "chat-panel-wrapper" in content
    print("  [✓] App.vue 引入 Live2DView 并有分栏布局")


def test_package_json_has_live2d_deps():
    """package.json 包含 pixi.js 和 pixi-live2d-display 依赖。"""
    pkg_path = ROOT / "ui" / "package.json"
    content = pkg_path.read_text(encoding="utf-8")
    assert "pixi.js" in content
    assert "pixi-live2d-display" in content
    print("  [✓] package.json 包含 Live2D 依赖")


def test_model_dir_has_readme():
    """占位模型目录存在且有 README。"""
    readme = ROOT / "ui" / "live2d" / "default" / "README.md"
    assert readme.exists(), f"README 不存在: {readme}"
    content = readme.read_text(encoding="utf-8")
    assert "model3.json" in content
    assert "moc3" in content
    print("  [✓] 模型目录 README 完整")


# ============================================================
# 3. 情绪 → 表情映射逻辑（Python 端验证）
# ============================================================
def test_emotion_to_expression_mapping():
    """Python 端情绪标签映射逻辑验证。"""
    # 后端把情绪标签传给前端，前端做映射
    # 这里验证映射表的关键映射
    # （前端 manager.js 中的 EMOTION_TO_EXPRESSION 在浏览器中测试，
    #   这里只测 Python 端的情绪标签输出）

    # 验证 AIEmotionEngine 的情绪标签是中文（前端映射表用中文 key）
    from core.emotion import AIEmotionEngine
    engine = AIEmotionEngine(persistence=False)
    label = engine.get_label()
    assert isinstance(label, str)
    assert label in ("中性", "开心", "难过", "生气", "平静", "期待", "焦虑", "疲惫")
    print(f"  [✓] AI 情绪标签 = {label}（前端可映射）")


def test_live2d_status_returns_valid_state():
    """status() 返回的 state 值是 Live2D 前端能处理的。"""
    from core.conversation import ConversationManager
    m = ConversationManager(
        llm=MagicMock(), asr=MagicMock(), tts=MagicMock(),
        emotion_engine=False, intimacy_manager=False,
        memory_manager=False, proactive_manager=False,
        wake_word_detector=False, interruption_detector=False,
        persist=False,
    )
    s = m.status()
    assert s["state"] in ("idle", "listening", "thinking", "speaking")
    print(f"  [✓] state={s['state']} 是合法值")


# ============================================================
# 4. ConversationManager status 完整性
# ============================================================
def test_status_includes_live2d_relevant_fields():
    """status() 应包含 Live2D 前端需要的字段。"""
    from core.conversation import ConversationManager
    m = ConversationManager(
        llm=MagicMock(), asr=MagicMock(), tts=MagicMock(),
        emotion_engine=False, intimacy_manager=False,
        memory_manager=False, proactive_manager=False,
        wake_word_detector=False, interruption_detector=False,
        persist=False,
    )
    s = m.status()
    # Live2D 前端需要的字段
    assert "state" in s        # 对话状态
    assert "ai_emotion" in s   # AI 情绪（表情联动）
    assert "intimacy_level" in s  # 亲密度等级（称呼/距离）
    assert "intimacy_score" in s  # 亲密度分数
    print("  [✓] status() 包含 Live2D 所需字段")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        test_live2d_status_endpoint,
        test_live2d_wake_endpoint,
        test_live2d_status_with_emotion,
        test_live2d_manager_js_exists,
        test_live2d_view_vue_exists,
        test_app_vue_includes_live2d,
        test_package_json_has_live2d_deps,
        test_model_dir_has_readme,
        test_emotion_to_expression_mapping,
        test_live2d_status_returns_valid_state,
        test_status_includes_live2d_relevant_fields,
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
