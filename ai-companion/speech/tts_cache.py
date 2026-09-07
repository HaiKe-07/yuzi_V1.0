"""TTS 语音文件缓存。

避免重复合成相同文本（如常用应答语），降低延迟与 API 成本。

设计要点：
- 缓存键 = hash(text + voice + 适配器参数) + format 后缀
- 命中时直接读字节返回，TTSResult.cached=True
- 缓存目录由 config.tts.cache_dir 控制
- 默认上限 200 条，超出按 atime 淘汰最旧
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from utils.config import config
from utils.logger import logger


class TTSCache:
    """基于文件系统的 TTS 结果缓存。"""

    def __init__(self, cache_dir: str | Path | None = None, max_entries: int = 200):
        cfg_dir = str(config.get("tts.cache_dir", "data/tts_cache"))
        self.cache_dir = Path(cache_dir) if cache_dir else config.path(cfg_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        logger.debug(f"TTSCache 初始化: {self.cache_dir} max={max_entries}")

    @staticmethod
    def _make_key(text: str, voice: str, extra: str = "") -> str:
        """生成缓存键：sha1(text+voice+extra) 前 16 位。"""
        raw = f"{text}|{voice}|{extra}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _path_for(self, key: str, fmt: str) -> Path:
        return self.cache_dir / f"{key}.{fmt}"

    def get(self, text: str, voice: str, fmt: str = "mp3", extra: str = "") -> bytes | None:
        """查询缓存。命中返回字节，否则 None。同时更新 atime。"""
        key = self._make_key(text, voice, extra)
        p = self._path_for(key, fmt)
        if not p.exists():
            return None
        try:
            data = p.read_bytes()
            os.utime(p, None)  # 更新访问时间
            logger.debug(f"TTS 缓存命中: {key} ({len(data)} bytes)")
            return data
        except OSError as e:
            logger.warning(f"TTS 缓存读取失败: {e}")
            return None

    def put(
        self,
        text: str,
        voice: str,
        audio: bytes,
        fmt: str = "mp3",
        extra: str = "",
    ) -> None:
        """写入缓存。同时触发 LRU 淘汰。"""
        if not audio:
            return
        key = self._make_key(text, voice, extra)
        p = self._path_for(key, fmt)
        try:
            p.write_bytes(audio)
            logger.debug(f"TTS 缓存写入: {key} ({len(audio)} bytes)")
        except OSError as e:
            logger.warning(f"TTS 缓存写入失败: {e}")
            return
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        """超出 max_entries 时按 atime 淘汰最旧。"""
        try:
            files = [
                (p, p.stat().st_atime)
                for p in self.cache_dir.iterdir() if p.is_file()
            ]
        except OSError:
            return
        if len(files) <= self.max_entries:
            return
        files.sort(key=lambda x: x[1])  # atime 升序，最旧在前
        evict_n = len(files) - self.max_entries
        for p, _ in files[:evict_n]:
            try:
                p.unlink()
                logger.debug(f"TTS 缓存淘汰: {p.name}")
            except OSError:
                pass

    def clear(self) -> int:
        """清空缓存，返回删除条数。"""
        n = 0
        for p in self.cache_dir.iterdir():
            if p.is_file():
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        logger.info(f"TTS 缓存已清空 ({n} 条)")
        return n

    def stats(self) -> dict:
        """缓存统计，调试用。"""
        files = [p for p in self.cache_dir.iterdir() if p.is_file()]
        total_size = sum(p.stat().st_size for p in files)
        return {
            "entries": len(files),
            "total_bytes": total_size,
            "cache_dir": str(self.cache_dir),
        }
