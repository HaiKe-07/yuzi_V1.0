"""T3-04 系统托盘常驻测试。

覆盖：
1. main.js 结构：含 Tray / requestSingleInstanceLock / setLoginItemSettings
2. main.js 关窗→托盘拦截逻辑、托盘菜单、开机自启
3. preload.js ipc 窗口控制（window:minimize / window:close 走主进程）
4. 托盘图标资源（ui/assets/tray.png）存在且为有效 PNG
5. config.yaml 含 T3-04 配置（auto_start / minimize_to_tray / tray_tooltip）
6. Electron 单例正确性（不允许 app.quit 逻辑破坏）

注：Tray/BrowserWindow 需要真实显示环境，沙箱 CI 无法实例化，
故采用源码结构断言 + 图标/配置校验（与 test_server 的骨架检查一致）。
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
UI_DIR = ROOT / "ui"
APP_ROOT = ROOT / "ai-companion" if (ROOT / "ai-companion").exists() else ROOT

# 尝试定位项目根（ai-companion），兼容不同挂载方式
for cand in (ROOT, ROOT / "ai-companion"):
    if (cand / "config.yaml").exists():
        APP_ROOT = cand
        UI_DIR = cand / "ui"
        break


# ============================================================
# 1. main.js 托盘功能
# ============================================================
def test_main_imports_tray_modules():
    """main.js 应引入 Tray/Menu/nativeImage/ipcMain。"""
    p = UI_DIR / "electron" / "main.js"
    assert p.exists(), f"main.js 不存在: {p}"
    content = p.read_text(encoding="utf-8")
    for kw in ["Tray", "Menu", "nativeImage", "ipcMain"]:
        assert kw in content, f"main.js 缺少 {kw}"
    print("  [✓] main.js 引入托盘相关模块")


def test_main_single_instance_lock():
    """main.js 应实现单实例锁。"""
    p = UI_DIR / "electron" / "main.js"
    content = p.read_text(encoding="utf-8")
    assert "requestSingleInstanceLock" in content
    assert "second-instance" in content   # 二次启动聚焦已有窗口
    print("  [✓] 单实例锁")


def test_main_auto_start():
    """main.js 应实现开机自启。"""
    p = UI_DIR / "electron" / "main.js"
    content = p.read_text(encoding="utf-8")
    assert "setLoginItemSettings" in content
    assert "openAtLogin" in content
    print("  [✓] 开机自启")


def test_main_minimize_to_tray():
    """关窗最小化到托盘：close 事件拦截 + preventDefault + hide。"""
    p = UI_DIR / "electron" / "main.js"
    content = p.read_text(encoding="utf-8")
    assert "minimizeToTray" in content
    assert "preventDefault" in content
    assert ".hide()" in content
    assert "isQuitting" in content
    print("  [✓] 关窗最小化到托盘")


def test_main_tray_menu():
    """托盘菜单应有 显示/隐藏、总在最前、开机自启、退出。"""
    p = UI_DIR / "electron" / "main.js"
    content = p.read_text(encoding="utf-8")
    assert "showMainWindow" in content
    assert "总在最前" in content
    assert "开机自启" in content
    assert "退出" in content
    assert "setContextMenu" in content
    print("  [✓] 托盘菜单项")


def test_main_keeps_backend_lifecycle():
    """T3-04 改造不应破坏原有后端启动逻辑。"""
    p = UI_DIR / "electron" / "main.js"
    content = p.read_text(encoding="utf-8")
    assert "BACKEND_PORT" in content
    assert "18731" in content
    assert "server.py" in content
    assert "waitForBackend" in content
    assert "stopPythonBackend" in content
    print("  [✓] 保留后端生命周期")


# ============================================================
# 2. preload.js ipc 窗口控制
# ============================================================
def test_preload_exposes_ipc():
    """preload 应用 ipcRenderer 实现窗口控制。"""
    p = UI_DIR / "electron" / "preload.js"
    content = p.read_text(encoding="utf-8")
    assert "ipcRenderer" in content
    assert "window:minimize" in content    # TitleBar 最小化
    assert "window:close" in content       # TitleBar 关闭→触发托盘隐藏
    print("  [✓] preload 暴露 ipc 窗口控制")


# ============================================================
# 3. 托盘图标资源
# ============================================================
def test_tray_icon_exists_and_valid():
    """ui/assets/tray.png 存在且是有效 PNG。"""
    p = UI_DIR / "assets" / "tray.png"
    assert p.exists(), f"托盘图标缺失: {p}"
    data = p.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是有效 PNG 头"
    w, h = struct.unpack(">II", data[16:24])
    assert w >= 16 and h >= 16, f"图标过小: {w}x{h}"
    print(f"  [✓] 托盘图标有效 ({w}x{h})")


# ============================================================
# 4. config.yaml T3-04 配置
# ============================================================
def test_config_has_tray_settings():
    """config.yaml ui 段应含 auto_start / minimize_to_tray / tray_tooltip。"""
    p = APP_ROOT / "config.yaml"
    assert p.exists(), f"config.yaml 不存在: {p}"
    content = p.read_text(encoding="utf-8")
    for key in ["auto_start", "minimize_to_tray", "tray_tooltip"]:
        assert key in content, f"config.yaml 缺少 {key}（ui 段）"
    print("  [✓] config.yaml 含托盘配置")


def test_config_minimize_default_true():
    """minimize_to_tray 默认 true。"""
    p = APP_ROOT / "config.yaml"
    content = p.read_text(encoding="utf-8")
    assert "minimize_to_tray: true" in content
    print("  [✓] 关窗默认最小化到托盘")


# ============================================================
# 5. package.json 打包配置
# ============================================================
def test_package_includes_assets():
    """打包 files 应包含 assets（托盘图标）。"""
    p = UI_DIR / "package.json"
    j = json.loads(p.read_text(encoding="utf-8"))
    files = j.get("build", {}).get("files", [])
    assert any("assets" in f for f in files), f"build.files 未含 assets: {files}"
    win = j.get("build", {}).get("win", {})
    assert "assets/tray.png" in win.get("icon", "")
    print("  [✓] 打包包含托盘图标")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        test_main_imports_tray_modules,
        test_main_single_instance_lock,
        test_main_auto_start,
        test_main_minimize_to_tray,
        test_main_tray_menu,
        test_main_keeps_backend_lifecycle,
        test_preload_exposes_ipc,
        test_tray_icon_exists_and_valid,
        test_config_has_tray_settings,
        test_config_minimize_default_true,
        test_package_includes_assets,
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