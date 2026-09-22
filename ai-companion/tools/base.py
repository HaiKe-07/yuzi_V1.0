"""工具统一抽象层 + 注册机制（T4-05）。

为后期接入电脑/手机/智能家居等设备控制预留统一扩展点。
验收：新工具可通过注册方式接入，无需改动核心代码。

设计：
- BaseTool：所有工具都要实现的抽象接口（name / description / handle / describe）
- register：快捷注册（函数或子类），自动发现并构建
- ToolRegistry：全局注册表，提供构建、按名取、枚举、LLM 工具 schema 收集
- builtins：自动登记本包既有工具（music/weather/reminder/recommend/web_info）

新工具接入（两种等价写法的示例，见文件底部注释）：
    1) 继承 BaseTool 注册：  @register(name="my_tool") class MyTool(BaseTool): ...
    2) 用函数注册：         @register(name="my_tool") def my_tool(params): return ...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union


class ToolError(Exception):
    """工具调用异常基类。"""


@dataclass
class ToolResult:
    """统一的工具调用返回。
    payload: 结构化数据；text: 给 LLM 的文本；status: "ok"|"error"。
    """
    status: str = "ok"
    text: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = ""

    @classmethod
    def ok(cls, text: str = "", **payload) -> "ToolResult":
        return cls(status="ok", text=text, payload=payload)

    @classmethod
    def error(cls, text: str = "", **payload) -> "ToolResult":
        return cls(status="error", text=text or "工具调用失败", payload=payload)


class BaseTool:
    """工具抽象基类。

    子类必须实现 handle()；也可覆盖 describe() 提供 LLM function-calling schema。
    """

    #: 工具名（注册用，缺省用类名小写下划线）与说明
    name: Optional[str] = None
    description: str = ""

    #: LLM function-calling 参数 schema（可选）
    parameters: Optional[dict] = None

    def __init__(self, **config):
        # 允许用 config 装配，供后续设备控制等带鉴权场景注入参数
        self.config = config

    def handle(self, params: Dict[str, Any]) -> Union[str, ToolResult, Any]:
        """执行工具。返回 str / ToolResult / 任意结构化结果均可。"""
        raise NotImplementedError(f"{self.name or type(self).__name__} 未实现 handle()")

    def describe(self) -> dict:
        return {
            "name": self.name or type(self).__name__,
            "description": self.description,
            "parameters": self.parameters or {"type": "object", "properties": {}},
        }


def name_of(cls_or_func) -> str:
    """从类/函数推导工具名。"""
    name = getattr(cls_or_func, "name", None)
    if name:
        return name
    cls_name = cls_or_func.__name__
    # CamelCase -> snake_case
    import re
    return re.sub(r"(?<!^)(?=[A-Z])", "_", cls_name).lower().rstrip("_")


class ToolRegistry:
    """工具注册表：注册、构建、枚举、收集 LLM schema。"""

    def __init__(self):
        self._builders: Dict[str, Callable[[Optional[dict]], BaseTool]] = {}

    # ---------- 注册 ----------
    def register(
        self,
        name: Optional[str] = None,
    ) -> Callable:
        """注册装饰器。可作用于 BaseTool 子类或普通函数。"""
        def deco(obj):
            key = name or getattr(obj, "name", None) or name_of(obj)
            if isinstance(obj, type):
                # 让 describe()/类属性与注册键保持一致
                obj.name = key
            self._builders[key] = self._wrap(obj)
            return obj
        return deco

    def build(self, name: str, config: Optional[dict] = None) -> Optional[BaseTool]:
        """按名称构建工具实例。未注册返回 None。"""
        builder = self._builders.get(name)
        if builder is None:
            return None
        return builder(config or {})

    def _wrap(self, obj) -> Callable[[Optional[dict]], BaseTool]:
        from tools.base import BaseTool as _BT
        if isinstance(obj, type) and issubclass(obj, _BT):
            return lambda cfg: obj(**cfg)
        # 普通函数 → 适配为兜底 handle
        def make_tool(cfg: Optional[dict]) -> BaseTool:
            class _FuncTool(_BT):
                name = getattr(obj, "__name__", None)
                description = (obj.__doc__ or "").strip()
                def handle(self, params):
                    return obj(params)
            return _FuncTool(**cfg)
        return make_tool

    # ---------- 查询 ----------
    def names(self) -> List[str]:
        return list(self._builders.keys())

    def describe_all(self) -> List[dict]:
        """返回所有已注册工具的 LLM schema。"""
        out = []
        for name in self._builders:
            tool = self.build(name)
            if tool is not None:
                out.append(tool.describe())
        return out


# 全局注册表（便于各处 import 复用）
registry = ToolRegistry()
register = registry.register


def build(name: str, config: Optional[dict] = None) -> Optional[BaseTool]:
    return registry.build(name, config)


# ============================================================
# 自动登记本包既有工具（无需改动核心代码即可纳入统一体系）
# ============================================================
@register(name="weather")
class WeatherTool(BaseTool):
    description = "查询天气：按城市返回当前与预报。"
    parameters = {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "城市名或 LocationID/经纬度"}},
        "required": ["city"],
    }

    def handle(self, params):
        from tools.weather import WeatherService
        return WeatherService().get_weather(params.get("city", ""))


@register(name="music")
class MusicTool(BaseTool):
    description = "控制酷狗音乐：点歌/暂停/继续/切歌/音量。"
    parameters = {
        "type": "object",
        "properties": {"action": {"type": "string"}, "keyword": {"type": "string"}, "volume": {"type": "integer"}},
        "required": ["action"],
    }

    def handle(self, params):
        from tools.music import MusicController
        return MusicController().handle(params)


@register(name="reminder")
class ReminderTool(BaseTool):
    description = "设置/取消/修改/查看定时提醒。"
    parameters = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["add", "cancel", "modify", "list"]},
            "content": {"type": "string"}, "time_text": {"type": "string"}, "rid": {"type": "integer"},
        },
        "required": ["op"],
    }

    def handle(self, params):
        from tools.reminder import ReminderManager
        mgr = ReminderManager()
        op = params.get("op", "list")
        if op == "list":
            return "\n".join(mgr.describe_all()) or "当前没有提醒。"
        if op == "add":
            return mgr.parse_and_add(params.get("time_text", ""), params.get("content", ""))
        if op == "cancel":
            rid = params.get("rid")
            return mgr.cancel(rid=rid) if rid is not None else mgr.cancel(keyword=params.get("content"))
        if op == "modify":
            return mgr.modify(rid=params.get("rid"), content=params.get("content"), time_text=params.get("time_text"))
        return "未知操作。"


@register(name="recommend_music")
class RecommendMusicTool(BaseTool):
    description = "根据情绪推荐音乐。"
    parameters = {
        "type": "object",
        "properties": {"emotion": {"type": "string"}},
        "required": ["emotion"],
    }

    def handle(self, params):
        from tools.recommend import recommend_and_play
        return recommend_and_play(params.get("emotion", ""), controller=None)


@register(name="web_info")
class WebInfoTool(BaseTool):
    description = "查询实时资讯/新闻或词条简介。"
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    def handle(self, params):
        from tools.web_info import WebInfo
        return WebInfo().handle(params.get("query", ""))


# ============================================================
# 示例：新工具三步接入（满足验收——不改核心代码）
#   1) from tools.base import BaseTool, register
#   2) @register(name="smart_home_light") class SmartHomeLight(BaseTool): ...
#   3) registry.build("smart_home_light") 即可使用
# ============================================================