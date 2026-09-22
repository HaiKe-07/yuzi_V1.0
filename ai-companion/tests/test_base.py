"""T4-05 设备控制接口预留测试。

验证：新工具可通过注册方式接入，无需改动核心代码（验收）。
覆盖：
1. 抽象层：BaseTool 子类注册/构建/调用
2. 函数注册：普通函数自动适配
3. 全局注册表：builtins 已自动登记（weather/music/reminder/recommend_music/web_info）
4. describe_all：收集全部 LLM schema，name 与注册键一致
5. 未注册工具 → None；自定义 config 注入
6. ToolResult / ToolError 便捷类
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.base import (  # noqa: E402
    BaseTool, ToolError, ToolRegistry, ToolResult, build,
    registry, register,
)


# ---------- 子类注册（模拟新增智能家居工具） ----------
@register(name="smart_home_light")
class SmartHomeLight(BaseTool):
    description = "控制智能家居灯光。"
    parameters = {
        "type": "object",
        "properties": {"on": {"type": "boolean"}},
        "required": ["on"],
    }

    def handle(self, params):
        return ToolResult.ok(
            f"灯光已{'开' if params.get('on') else '关'}",
            on=bool(params.get("on")),
        )


def test_subclass_register_and_build():
    tool = registry.build("smart_home_light")
    assert tool is not None
    r = tool.handle({"on": True})
    assert r.status == "ok" and "开" in r.text and r.payload["on"] is True
    print("  [✓] 子类注册 → 构建 → 调用")


def test_function_register():
    @register(name="echo_tool")
    def echo_tool(params):
        return f"echo:{params.get('text', '')}"
    tool = registry.build("echo_tool")
    assert tool.handle({"text": "hi"}) == "echo:hi"
    print("  [✓] 函数注册自动适配")


# ---------- builtins 自动登记 ----------
def test_builtins_registered():
    for name in ("weather", "music", "reminder", "recommend_music", "web_info"):
        assert name in registry.names(), f"缺少 builtin: {name}"
    print(f"  [✓] builtins 自动登记: {sorted(registry.names())}")


def test_describe_all_schema_names_match():
    names = registry.names()
    for d in registry.describe_all():
        assert d["name"] in names, f"schema name 与注册键不一致: {d['name']}"
    print("  [✓] describe_all 收集全部 schema，name 与注册键一致")


def test_build_unknown_returns_none():
    assert build("not_exists_tool") is None
    print("  [✓] 未注册工具 → None")


def test_config_injection():
    @register(name="cfg_tool")
    class CfgTool(BaseTool):
        def handle(self, params):
            return self.config.get("token", "none")
    t = registry.build("cfg_tool", {"token": "abc"})
    assert t.handle({}) == "abc"
    print("  [✓] config 注入实例装配")


# ---------- 便捷类 ----------
def test_toolresult_helpers():
    ok = ToolResult.ok("好了", k=1)
    assert ok.status == "ok" and ok.payload["k"] == 1
    err = ToolResult.error("出错了")
    assert err.status == "error" and "出错" in err.text
    print("  [✓] ToolResult ok/error")


def test_toolerror_is_importable():
    assert issubclass(ToolError, Exception)
    print("  [✓] ToolError 异常基类")


def test_external_registration_without_core_change():
    """验收：新工具通过注册接入，不改核心代码。"""
    # 全新独立实例注册表，模拟第三方模块
    reg = ToolRegistry()

    @reg.register(name="phone_sms")
    class PhoneSms(BaseTool):
        description = "发送手机短信。"
        def handle(self, params):
            return f"sms->{params.get('number')}:{params.get('content')}"

    t = reg.build("phone_sms")
    assert t.handle({"number": "138", "content": "hi"}) == "sms->138:hi"
    # 核心代码（builtin）未被第三方工具污染
    assert "phone_sms" not in registry.names()
    print("  [✓] 第三方工具独立注册，不动核心代码")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        test_subclass_register_and_build,
        test_function_register,
        test_builtins_registered,
        test_describe_all_schema_names_match,
        test_build_unknown_returns_none,
        test_config_injection,
        test_toolresult_helpers,
        test_toolerror_is_importable,
        test_external_registration_without_core_change,
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

    print(f"\n结果: {passed} 通过, {failed} 失败 / 共 {len(tests)}")