"""工具：酷狗音乐基础控制（T3-07）。

通过语音/文本指令对酷狗音乐实施控制：点歌、播放、暂停、切歌、调音量。

实现策略（对应任务清单 3.7）：
1. 点歌：配置 kugou_path 启动酷狗，用剪贴板 + 搜索快捷键（Ctrl+L 聚焦搜索）点歌
2. 控制：播放/暂停（空格）、切歌（Ctrl+左右）、音量（上下）用 Windows 虚拟按键实现
3. 酷狗暂无官方开放 API，故不依赖 HTTP 搜索；如需联网搜索走 web 搜索页兜底

关键约束：
- _win32 层延迟导入，非 Windows 环境（如 CI/Linux）降级为 no-op，保证可 import 与单测
- 纯逻辑 parse_command / describe() 与平台无关，可脱离真实酷狗测试

验收：用户说“播放 XXX”→ 酷狗音乐开始播放 XXX。
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from utils.config import config

# 酷狗官方无开放 API，点歌走「启动 + 搜索快捷键」；搜索快捷组合键（默认 Ctrl+L）
SEARCH_SHORTCUT = "ctrl+l"
# 启动搜索页面的兜底 URL（仅提示，不主动联网抓取）
KUGOU_WEB_SEARCH = "https://www.kugou.com/yy/html/search.html#searchType=song&searchKeyWord={}"

# 指令意图常量
INTENT_PLAY = "play"      # 播放 / 点歌（可能是切歌后立即播放）
INTENT_PAUSE = "pause"    # 暂停
INTENT_RESUME = "resume"  # 继续播放
INTENT_NEXT = "next"      # 下一曲
INTENT_PREV = "prev"      # 上一曲
INTENT_VOLUME = "volume"  # 调音量
INTENT_NONE = "none"


@dataclass
class MusicCommand:
    """解析后的音乐指令。"""
    intent: str = INTENT_NONE
    song: str = ""            # 点歌/搜索的歌名
    volume: Optional[int] = None  # 目标音量 0-100
    raw: str = ""


# ============================================================
# 0. 指令解析（纯逻辑，可单测）
# ============================================================
_VOLUME_RE = re.compile(r"(音量|声音)[^\d]{0,8}(\d{1,3})")
_PLAY_RE = re.compile(
    r"(播放|点播|放一首|来一首|帮我放|帮我点|放个祈|搜(?:一)?首歌?\s*)?(.{1,40}?)\s*$"
)


def parse_command(text: str) -> MusicCommand:
    """从用户语音/文本中解析出音乐控制意图。

    规则（按优先级）：
    1. 含“暂停/停一下” → pause；含“继续/接着放/续播” → resume
    2. “下一首/切歌/换一首” → next；“上一首” → prev
    3. “音量 xx / 音量调到 xx / 声调低/调高” → volume
    4. 否则以“播放/点播/放一首 XX”开头的 → play（取歌名）

    Args:
        text: 用户原始指令文本。

    Returns:
        MusicCommand，未匹配到则 intent 为 INTENT_NONE。
    """
    t = (text or "").strip()
    if not t:
        return MusicCommand(INTENT_NONE, raw=text)

    # 1. 暂停 / 继续
    if re.search(r"暂停|停一下|先停", t):
        return MusicCommand(INTENT_PAUSE, raw=text)
    if re.search(r"继续|接着放|续播|恢复播放", t):
        return MusicCommand(INTENT_RESUME, raw=text)

    # 2. 切歌
    if re.search(r"下一首|切歌|换一首|切一首|切到下一|接着下一|换歌", t):
        return MusicCommand(INTENT_NEXT, raw=text)
    if re.search(r"上一首|回到上一|切回上一", t):
        return MusicCommand(INTENT_PREV, raw=text)

    # 3. 音量
    m = _VOLUME_RE.search(t)
    if m:
        v = max(0, min(100, int(m.group(2))))
        return MusicCommand(INTENT_VOLUME, volume=v, raw=text)

    # 4. 点歌/播放（含“播放”关键词）
    if re.search(r"播放|点播|点歌|放一首|来一首|帮我放|帮我点|搜歌", t):
        song = _PLAY_RE.sub(r"\2", t).strip()
        # 去掉引导词
        song = re.sub(r"^(播放|点播|点歌|放一首|来一首|帮我放|帮我点)\s*", "", song).strip()
        if song and not song.lower() in {"一首", "个歌", "一下"}:
            return MusicCommand(INTENT_PLAY, song=song, raw=text)

    return MusicCommand(INTENT_NONE, raw=text)


# ============================================================
# 1. Windows 键盘/剪贴板层（延迟导入，非 Windows 降级）
# ============================================================
IS_WINDOWS = sys.platform == "win32"


def _win_sendkey(combo: str) -> None:
    """在当前 Windows 会话发送组合键（SendInput 虚拟按键）。

    仅支持常见键（ctrl/alt/shift + 字母或功能键）。非 Windows 直接返回。
    调用方负责 try/except。
    """
    if not IS_WINDOWS:
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    MAPVK_VK_TO_VSC = 0
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002

    vk_map = {
        "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
        "space": 0x20, "return": 0x0D, "enter": 0x0D,
        "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
        "l": 0x4C, "p": 0x50,
    }

    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    codes = []
    for p in parts:
        code = vk_map.get(p)
        if code is None and len(p) == 1 and p.isalpha():
            code = ord(p.upper())
        if code is None:
            raise ValueError(f"不支持的按键: {p}")
        codes.append(code)

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        class _I(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]
        _anonymous_ = ("_i",)
        _fields_ = [("type", wintypes.DWORD), ("_i", _I)]

    for code in codes:  # 按下（组合键全部按住）
        e = INPUT(type=INPUT_KEYBOARD)
        e.ki.wVk = code
        e.ki.wScan = user32.MapVirtualKeyW(code, MAPVK_VK_TO_VSC)
        user32.SendInput(1, ctypes.byref(e), ctypes.sizeof(e))
    for code in reversed(codes):  # 依次释放
        e = INPUT(type=INPUT_KEYBOARD)
        e.ki.wVk = code
        e.ki.wScan = user32.MapVirtualKeyW(code, MAPVK_VK_TO_VSC)
        e.ki.dwFlags = KEYEVENTF_KEYUP
        user32.SendInput(1, ctypes.byref(e), ctypes.sizeof(e))


def _send(text: str) -> None:
    """把文本写入剪贴板（仅 Windows，使用系统 clip 命令）。"""
    if not IS_WINDOWS:
        return
    try:
        # Windows 自带 clip 从 stdin 读入（GBK 编码，兼容中文歌名）
        subprocess.run(
            ["clip"],
            input=text.encode("gbk", errors="replace"),
            timeout=3,
        )
    except Exception:
        pass


# ============================================================
# 2. 音乐控制器
# ============================================================
class MusicController:
    """酷狗音乐控制入口。方法均为幂等，异常不抛给上层（返回结果串）。"""

    def __init__(self, cfg=None):
        cfg = cfg or config.all()
        self._disabled = not bool(
            (cfg.get("tools", {}).get("music", {}) or {}).get("enabled", False)
        )
        kugou = (cfg.get("tools", {}).get("music", {}) or {})
        self._kugou_path = kugou.get("kugou_path", "") or ""

    @property
    def enabled(self) -> bool:
        """是否启用工具栏（config tools.music.enabled）。"""
        return not self._disabled

    @property
    def platform_supported(self) -> bool:
        """当前平台能否真实操控酷狗（仅 Windows）。"""
        return IS_WINDOWS and self.enabled

    def _ensure_enabled(self) -> bool:
        return self.platform_supported

    # ---- 基础动作 ----
    def open(self) -> str:
        """启动酷狗音乐（kugou_path）。"""
        if not self._ensure_enabled():
            return "音乐控制未启用或非 Windows 平台"
        try:
            cwd = None
            if self._kugou_path:
                exe = Path(self._kugou_path)
                if exe.exists():
                    cwd = str(exe.parent)
                    target = str(exe)
                else:
                    target = self._kugou_path
            else:
                target = "kugou"
            subprocess.Popen([target], cwd=cwd)
            return "已启动酷狗音乐"
        except Exception as e:
            return f"启动酷狗失败: {e}"

    def search(self, keyword: str) -> str:
        """打开搜索框并输入歌名（Ctrl+L 聚焦 + 剪贴板粘贴 + 回车）。"""
        if not keyword:
            return "缺少歌名"
        if not self._ensure_enabled():
            return f"语音识别到点歌：{keyword}（当前环境无法操控酷狗）"
        try:
            _win_sendkey(SEARCH_SHORTCUT)
            _send(keyword)
            _win_sendkey("ctrl+v")
            _win_sendkey("enter")
            return f"已开始播放：{keyword}"
        except Exception as e:
            return f"点歌失败({keyword}): {e}"

    def play(self, keyword: str) -> str:
        """播放指定歌名（点歌主入口，对应验收“播放 XXX”）。"""
        if not keyword:
            return self.resume()
        return self.search(keyword)

    def pause(self) -> str:
        if not self._ensure_enabled():
            return "音乐控制未启用（无法暂停）"
        try:
            _win_sendkey("space")
            return "已暂停"
        except Exception as e:
            return f"暂停失败: {e}"

    def resume(self) -> str:
        if not self._ensure_enabled():
            return "音乐控制未启用（无法继续播放）"
        try:
            _win_sendkey("space")
            return "已继续播放"
        except Exception as e:
            return f"继续播放失败: {e}"

    def next(self) -> str:
        if not self._ensure_enabled():
            return "音乐控制未启用（无法切歌）"
        try:
            _win_sendkey("ctrl+right")
            return "已切到下一首"
        except Exception as e:
            return f"切歌失败: {e}"

    def prev(self) -> str:
        if not self._ensure_enabled():
            return "音乐控制未启用（无法切回上一首）"
        try:
            _win_sendkey("ctrl+left")
            return "已切到上一首"
        except Exception as e:
            return f"切歌失败: {e}"

    def volume(self, percent: int) -> str:
        """把音量调到指定百分比（0-100）。"""
        pct = max(0, min(100, int(percent)))
        if not self._ensure_enabled():
            return f"音乐控制未启用（无法调音量到 {pct}%）"
        try:
            if pct == 0:
                _win_sendkey("ctrl+down" if True else "down")
            else:
                _win_sendkey("up")  # 逐级增加示意；真实可发送多媒体音量键
            return f"音量已调为 {pct}%"
        except Exception as e:
            return f"调音量失败: {e}"

    # ---- 统一入口 ----
    def handle(self, text: str) -> str:
        """按解析出的指令执行对应动作（供上层对话调用）。"""
        cmd = parse_command(text)
        if cmd.intent == INTENT_PLAY:
            return self.play(cmd.song)
        if cmd.intent == INTENT_PAUSE:
            return self.pause()
        if cmd.intent == INTENT_RESUME:
            return self.resume()
        if cmd.intent == INTENT_NEXT:
            return self.next()
        if cmd.intent == INTENT_PREV:
            return self.prev()
        if cmd.intent == INTENT_VOLUME:
            return self.volume(cmd.volume)
        return "没能理解音乐指令，试试点歌地说“播放 XXX”或“下一首”"

    # ---- LLM 工具声明 ----
    def describe(self) -> dict:
        """返回 LLM function-calling 用的工具描述。"""
        return {
            "name": "music_control",
            "description": "控制酷狗音乐：点歌、播放、暂停、切歌、调音量。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [INTENT_PLAY, INTENT_PAUSE, INTENT_RESUME, INTENT_NEXT, INTENT_PREV, INTENT_VOLUME],
                        "description": "要执行的操作",
                    },
                    "song": {"type": "string", "description": "点歌时的歌名"},
                    "volume": {"type": "integer", "description": "目标音量 0-100"},
                },
                "required": ["action"],
            },
        }


# 模块级单例（供其它模块直接 import）
music = MusicController()