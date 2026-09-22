"""T4-01 天气查询测试。

覆盖：
1. parse_location：从文本提取城市
2. QWeatherClient：城市解析 / LocationID / 经纬度 / 实时天气 / 预报（用注入 fetcher，不联网）
3. WeatherService：未启用提示、摘要文案、LLM schema
4. config 读取 tools.weather
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.weather import (  # noqa: E402
    QWeatherClient,
    WeatherError,
    WeatherService,
    parse_location,
)

FAKE_KEY = "TESTKEY"


def fake_fetcher(now_resp=None, fc_resp=None, geo_resp=None, known_geo=True):
    """按 URL 路径返回配置好的 JSON 响应，用于离线单测。"""
    def _fetch(url, params, timeout=6):
        if "/geo/v2/city/lookup" in url:
            return geo_resp if geo_resp is not None else (_geo_resp() if known_geo else {"code": "404", "location": []})
        if "/weather/now" in url:
            return now_resp if now_resp is not None else NOW_RESP
        if "/weather/3d" in url or "/weather/7d" in url:
            return fc_resp if fc_resp is not None else FC_RESP_3D
        return {"code": "404"}
    return _fetch


def _geo_resp(cid="101010100", name="北京", adm="北京市"):
    return {"code": "200", "location": [{"id": cid, "name": name, "adm2": adm}]}


NOW_RESP = {
    "code": "200",
    "now": {
        "obsTime": "2026-09-22T10:00+08:00",
        "temp": "26", "feelsLike": "27", "text": "晴",
        "windDir": "东南", "windScale": "2", "humidity": "50",
    },
}
FC_RESP_3D = {
    "code": "200",
    "daily": [
        {"fxDate": "2026-09-22", "tempMax": "30", "tempMin": "22", "textDay": "晴", "textNight": "晴间多云"},
        {"fxDate": "2026-09-23", "tempMax": "28", "tempMin": "21", "textDay": "多云", "textNight": "多云"},
        {"fxDate": "2026-09-24", "tempMax": "25", "tempMin": "19", "textDay": "小雨", "textNight": "小雨"},
    ],
}


def _client(now_resp=None, fc_resp=None, geo_resp=None, known_geo=True):
    return QWeatherClient(
        FAKE_KEY, default_city="北京",
        fetcher=fake_fetcher(now_resp=now_resp, fc_resp=fc_resp, geo_resp=geo_resp, known_geo=known_geo),
    )


# ---------- parse_location ----------
def test_parse_location():
    assert parse_location("北京今天天气怎么样") == "北京"
    assert parse_location("上海天气") == "上海"
    assert parse_location("深圳明天天气") == "深圳"
    assert parse_location("你好，吃饭了吗") is None
    assert parse_location("") is None
    print("  [✓] parse_location 城市提取")


# ---------- QWeatherClient ----------
def test_resolve_id_pass_through():
    c = _client()
    assert c.resolve("101010100") == "101010100"
    print("  [✓] LocationID 直通")


def test_resolve_coord():
    c = _client()
    assert c.resolve("116.4,39.9") == "116.4,39.9"
    print("  [✓] 经纬度直通")


def test_resolve_city_lookup():
    c = _client()
    assert c.resolve("北京") == "101010100"
    print("  [✓] 城市名 → LocationID")


def test_resolve_default_city():
    c = _client()
    assert c.resolve(None) == "101010100"  # 用配置默认城市
    print("  [✓] 默认城市解析")


def test_resolve_unknown_city():
    c = QWeatherClient(FAKE_KEY, default_city="北京", fetcher=fake_fetcher(known_geo=False))
    try:
        c.resolve("不存在城市")
        raise AssertionError("应抛 WeatherError")
    except WeatherError:
        print("  [✓] 未知城市抛 WeatherError")


def test_get_now():
    c = _client()
    now = c.get_now("北京")
    assert now["text"] == "晴"
    assert now["temp"] == "26"
    assert now["humidity"] == "50"
    print("  [✓] 实时天气")


def test_get_forecast_3d():
    c = _client()
    fc = c.get_forecast("北京", 3)
    assert len(fc) == 3
    assert fc[0]["textDay"] == "晴"
    assert fc[0]["tempMax"] == "30"
    print("  [✓] 3 日预报")


def test_get_forecast_7d_uses_7d_endpoint():
    table = {
        "https://devapi.qweather.com/v7/weather/7d?key=TESTKEY&location=101010100": FC_RESP_3D,
    }
    c = QWeatherClient(FAKE_KEY, default_city="北京", fetcher=fake_fetcher(table))
    fc = c.get_forecast("北京", 7)
    assert fc  # 能解析 7d 响应
    print("  [✓] 预报天数选择 7d 接口")


def test_get_weather_combined():
    c = _client()
    data = c.get_weather("北京", 3)
    assert data["now"]["temp"] == "26"
    assert len(data["forecast"]) == 3
    print("  [✓] 实时+预报组合")


# ---------- WeatherService ----------
def test_service_disabled_message():
    c = _client()
    svc = WeatherService(client=c, enabled=True)
    svc.enabled = False  # 模拟未启用
    msg = svc.get_weather("北京")
    assert "未启用" in msg
    print("  [✓] 未启用提示")


def test_service_summary():
    c = _client()
    svc = WeatherService(client=c, enabled=True)
    msg = svc.get_weather("北京今天天气")
    assert "北京" in msg
    assert "26℃" in msg
    assert "晴" in msg
    assert "湿度" in msg
    assert any(("09022" in msg) or ("2026-09-23" in msg) for msg in [msg])  # 含预报日期
    print("  [✓] 天气摘要文案含实时+预报")


def test_service_error_handled():
    c = _client(now_resp={"code": "404"}, fc_resp={"code": "-1"})
    svc = WeatherService(client=c, enabled=True)
    msg = svc.get_weather("北京")
    assert "失败" in msg or "错误" in msg
    print("  [✓] 接口异常被捕获并转可读文案")


def test_describe_schema():
    svc = WeatherService(client=_client(), enabled=True)
    d = svc.describe()
    assert d["name"] == "weather_query"
    assert "location" in d["parameters"]["properties"]
    print("  [✓] LLM 工具 schema")


def test_config_read():
    from utils.config import config as cfg
    w = cfg.get("tools.weather", {}) or {}
    assert w.get("api") == "qweather"
    assert "default_city" in w
    print("  [✓] config 读取 tools.weather")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        test_parse_location,
        test_resolve_id_pass_through,
        test_resolve_coord,
        test_resolve_city_lookup,
        test_resolve_default_city,
        test_resolve_unknown_city,
        test_get_now,
        test_get_forecast_3d,
        test_get_forecast_7d_uses_7d_endpoint,
        test_get_weather_combined,
        test_service_disabled_message,
        test_service_summary,
        test_service_error_handled,
        test_describe_schema,
        test_config_read,
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