"""工具包。

各工具独立模块，上层（对话/LLM）按需 import。
- music: 酷狗音乐基础控制（T3-07）
- weather: 天气查询（T4-01）
- reminder: 定时提醒（T4-02）
- recommend: 情绪推荐音乐（T4-03）
- web_info: 网络资讯查询（T4-04）
"""

from tools.music import MusicController, MusicCommand, parse_command  # noqa: F401
from tools.recommend import recommend, recommend_and_play  # noqa: F401
from tools.reminder import Reminder, ReminderManager, parse_time  # noqa: F401
from tools.web_info import NewsItem, WebInfo  # noqa: F401
from tools.weather import QWeatherClient, WeatherService, parse_location  # noqa: F401

__all__ = [
    "MusicController", "MusicCommand", "parse_command",
    "QWeatherClient", "WeatherService", "parse_location",
    "Reminder", "ReminderManager", "parse_time",
    "recommend", "recommend_and_play",
    "NewsItem", "WebInfo",
]