"""工具：天气查询（T4-01）。

接入和风天气（QWeather），支持当前天气与逐日预报查询。

验收：用户问天气，AI 能准确回答当前和未来天气。

设计要点：
1. 纯标准库实现（urllib），无第三方 HTTP 依赖
2. HTTP 层可注入 fetcher，便于离线单测（不依赖真实网络）
3. 城市解析：支持中文城市名（走 geo city/lookup 转 LocationID）、数字 LocationID、经纬度
4. 未指定城市时使用 config tools.weather.default_city（或通过文本“XX天气”提取）
5. 异常统一收敛为 WeatherError，handle() 返回可读文案

配置（config.yaml）：
    tools.weather.api: "qweather"      # 供应商
    tools.weather.api_key: "${QWEATHER_API_KEY}"  # 和风天气 key
    tools.weather.default_city: "北京"  # 默认城市
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional

from utils.config import config


class WeatherError(Exception):
    """天气查询统一异常。"""


# 城市名匹配：文本中“XX天气”提取城市
_CITY_RE = re.compile(r"([\u4e00-\u9fa5]{2,6}?)(?:的)?(?:今天|明天|未来|天气)")
# 经纬度 "lng,lat" 或 "lng,lat"
_COORD_RE = re.compile(r"-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?")
# 数字 LocationID
_DIGIT_RE = re.compile(r"\d+")


def _http_get(url: str, params: Dict[str, str], timeout: int = 6) -> dict:
    """标准库 GET，返回解析后的 JSON。"""
    q = urllib.parse.urlencode({k: str(v) for k, v in params.items()})
    with urllib.request.urlopen(url + "?" + q, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class QWeatherClient:
    """和风天气客户端。可注入 fetcher 以便测试。"""

    BASE = "https://devapi.qweather.com/v7"
    GEO = "https://devapi.qweather.com/geo/v2/city/lookup"

    def __init__(
        self,
        api_key: str,
        default_city: Optional[str] = None,
        fetcher: Optional[Callable] = None,
        timeout: int = 6,
    ):
        self._key = (api_key or "").strip()
        self._default_city = default_city
        self._fetcher = fetcher or _http_get
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._key)

    # ---- 城市解析 ----
    def geo_lookup(self, city: str) -> dict:
        """把中文城市名解析成 LocationID 及标准地名。"""
        data = self._fetcher(
            self.GEO, {"location": city, "key": self._key}, self._timeout
        )
        if int(data.get("code", "404")) != 200:
            raise WeatherError(f"城市解析失败(code={data.get('code')})")
        locs = data.get("location") or []
        if not locs:
            raise WeatherError(f"未找到城市：{city}")
        first = locs[0]
        return {
            "id": first.get("id"),
            "name": first.get("name"),
            "adm": first.get("adm2") or first.get("adm1"),
        }

    def resolve(self, location: Optional[str]) -> str:
        """把输入归一到和风 LocationID 或经纬度串。"""
        loc = (location or "").strip() or self._default_city
        if not loc:
            raise WeatherError("未指定城市，且未配置默认城市")
        if _DIGIT_RE.fullmatch(loc):
            return loc
        if _COORD_RE.fullmatch(loc):
            return loc
        info = self.geo_lookup(loc)
        return info["id"]

    # ---- 业务查询 ----
    def get_now(self, location: Optional[str] = None) -> dict:
        """当前天气实时。"""
        loc = self.resolve(location)
        data = self._fetcher(
            self.BASE + "/weather/now",
            {"location": loc, "key": self._key},
            self._timeout,
        )
        if int(data.get("code", "-1")) != 200:
            raise WeatherError(f"实时天气查询失败(code={data.get('code')})")
        n = data.get("now") or {}
        return {
            "obsTime": n.get("obsTime"),
            "temp": n.get("temp"),
            "feelslike": n.get("feelsLike"),
            "text": n.get("text"),
            "windDir": n.get("windDir"),
            "windScale": n.get("windScale"),
            "humidity": n.get("humidity"),
        }

    def get_forecast(self, location: Optional[str] = None, days: int = 3) -> List[dict]:
        """逐日预报（days 取 1-7，和风 3d/7d 接口）。"""
        days = max(1, min(7, int(days)))
        endpoint = "3d" if days <= 3 else "7d"
        loc = self.resolve(location)
        data = self._fetcher(
            self.BASE + "/weather/" + endpoint,
            {"location": loc, "key": self._key},
            self._timeout,
        )
        if int(data.get("code", "-1")) != 200:
            raise WeatherError(f"预报查询失败(code={data.get('code')})")
        out = []
        for d in (data.get("daily") or []):
            out.append({
                "fxDate": d.get("fxDate"),
                "tempMax": d.get("tempMax"),
                "tempMin": d.get("tempMin"),
                "textDay": d.get("textDay"),
                "textNight": d.get("textNight"),
            })
        return out[:days]

    def get_weather(self, location: Optional[str] = None, days: int = 3) -> dict:
        """组合：当前 + 预报。"""
        return {"now": self.get_now(location), "forecast": self.get_forecast(location, days)}


def parse_location(text: Optional[str]) -> Optional[str]:
    """从文本提取城市（“北京今天天气”/“上海天气”）。无则返回 None。"""
    t = text or ""
    m = _CITY_RE.search(t)
    if m:
        city = m.group(1)
        # 排除常见非城市词
        if city not in {"今天", "明天", "这个", "那个"}:
            return city
    return None


# ============================================================
# 2. 高层服务：解析文本 → 组装可读回复（供 LLM/对话调用）
# ============================================================
class WeatherService:
    """天气服务统一入口。"""

    def __init__(
        self,
        client: Optional[QWeatherClient] = None,
        default_city: Optional[str] = None,
        enabled: Optional[bool] = None,
    ):
        cfg = config.all()
        weather = (cfg.get("tools", {}).get("weather", {}) or {})
        provider = weather.get("api", "qweather")
        api_key = weather.get("api_key", "") or ""
        default_city = default_city or weather.get("default_city")
        if enabled is None:
            enabled = bool(weather.get("enabled", False)) and bool(api_key)
        self.enabled = enabled
        if client is None:
            client = QWeatherClient(api_key, default_city=default_city)
        self._client = client
        self._provider = provider

    @property
    def configured(self) -> bool:
        return self.enabled and self._client.configured

    def get_weather(self, text: str = "", days: int = 3) -> str:
        """根据用户文本返回天气摘要文案。"""
        if not self.configured:
            return "天气工具未启用或缺少 API Key（请在 config.yaml 配置 tools.weather）"
        location = parse_location(text)
        try:
            data = self._client.get_weather(location, days)
        except WeatherError as e:
            return f"天气查询失败：{e}"
        except Exception as e:  # urllib 网络异常等
            return f"天气查询出错：{e}"
        now = data["now"]
        fc = data["forecast"]
        lines = []
        loc_msg = location or self._client._default_city or ""
        lines.append(f"{loc_msg} 当前 {now.get('text') or '未知'}，{now.get('temp') or '-'}℃，"
                     f"体感 {now.get('feelslike') or '-'}℃，"
                     f"{now.get('windDir') or ''}风{now.get('windScale') or ''}级，"
                     f"湿度 {now.get('humidity') or '-'}%")
        for d in fc:
            lines.append(f"{d.get('fxDate')}：{d.get('textDay') or '-'}，"
                         f"{d.get('tempMin')}~{d.get('tempMax')}℃")
        return "\n".join(lines)

    def handle(self, text: str = "") -> str:
        """对话工具入口（LLM function-calling 用）。"""
        return self.get_weather(text)

    def describe(self) -> dict:
        """返回 LLM 工具声明。"""
        return {
            "name": "weather_query",
            "description": "查询天气（当前与未来几日预报）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "城市名，如 北京"},
                    "days": {"type": "integer", "description": "预报天数 1-7", "default": 3},
                },
            },
        }


def get_provider(cfg=None) -> WeatherService:
    """工厂：根据 config 创建天气服务（目前支持 qweather）。"""
    return WeatherService()


# 模块级单例
weather = get_provider()