"""工具包。

各工具独立模块，上层（对话/LLM）按需 import。
- music: 酷狗音乐基础控制（T3-07）
- weather: 天气查询（T4-01）
"""

from tools.music import MusicController, MusicCommand, parse_command  # noqa: F401
from tools.weather import QWeatherClient, WeatherService, parse_location  # noqa: F401

__all__ = [
    "MusicController", "MusicCommand", "parse_command",
    "QWeatherClient", "WeatherService", "parse_location",
]