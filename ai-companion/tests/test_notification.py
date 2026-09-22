"""T3-06 桌面通知测试。

覆盖：
1. Electron main.js：import Notification、showDesktopNotification、notify IPC、notify:enabled
2. Electron preload.js：暴露 notify
3. config.yaml：ui.notifications_enabled 开关
4. 前端 store：companion.js 的 notify 方法
5. SettingsPanel.vue：发送测试通知按钮

注：桌面通知需打包后在真实桌面环境手动验证；这里做结构 + 配置校验。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UI_DIR = ROOT / "ui"


def test_main_js_notification_support():
    p = UI_DIR / "electron" / "main.js"
    content = p.read_text(encoding="utf-8")
    assert "Notification" in content                     # import 自 electron
    assert "showDesktopNotification" in content           # 发送通知函数
    assert "ipcMain.on('notify'" in content               # 渲染进程触发
    assert "ipcMain.handle('notify:enabled'" in content   # 开关查询
    assert "Notification.isSupported" in content
    assert "notificationsEnabled" in content
    assert "n.show()" in content
    print("  [✓] main.js 桌面通知实现")


def test_preload_js_notify():
    p = UI_DIR / "electron" / "preload.js"
    content = p.read_text(encoding="utf-8")
    assert "notify" in content
    assert "ipcRenderer.send('notify'" in content
    print("  [✓] preload.js 暴露 notify")


def test_config_notifications_enabled():
    p = ROOT / "config.yaml"
    c = p.read_text(encoding="utf-8")
    assert "notifications_enabled" in c
    from utils.config import config as cfg
    assert cfg.get("ui.notifications_enabled", True) is True
    print("  [✓] config.yaml notifications_enabled")


def test_store_notify_method():
    p = UI_DIR / "src" / "stores" / "companion.js"
    content = p.read_text(encoding="utf-8")
    assert "function notify" in content
    assert "api().notify(payload)" in content
    assert "notify," in content   # 导出
    print("  [✓] companion store notify 方法")


def test_settings_panel_test_notify():
    p = UI_DIR / "src" / "components" / "SettingsPanel.vue"
    content = p.read_text(encoding="utf-8")
    assert "testNotify" in content
    assert "发送测试通知" in content
    assert "store.notify" in content
    print("  [✓] SettingsPanel 测试通知按钮")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        test_main_js_notification_support,
        test_preload_js_notify,
        test_config_notifications_enabled,
        test_store_notify_method,
        test_settings_panel_test_notify,
    ]

    passed = failed = 0
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
        print("全部通过！")