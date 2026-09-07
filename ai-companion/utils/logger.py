"""日志系统。

基于 loguru：
- 控制台彩色输出 + 文件按天滚动
- 等级由 config.yaml 的 app.log_level 控制
- 子模块直接 `from utils.logger import logger` 即可使用
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger as _logger

from .config import config

__all__ = ["logger"]


def _setup_logger() -> "loguru_logger":
    # 移除默认 handler
    _logger.remove()

    level = str(config.get("app.log_level", "INFO")).upper()

    # 控制台输出
    _logger.add(
        sys.stdout,
        level=level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        ),
        backtrace=False,
        diagnose=False,
    )

    # 文件输出：项目根/logs/companion.log，按天滚动，保留 14 天
    log_dir: Path = config.path(str(config.get("app.log_dir", "logs")))
    log_file = log_dir / "companion.log"
    _logger.add(
        str(log_file),
        level=level,
        rotation="00:00",          # 每天 0 点滚动
        retention="14 days",
        encoding="utf-8",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "{name}:{function}:{line} - {message}"
        ),
        backtrace=True,
        diagnose=False,
        enqueue=True,              # 多进程安全
    )

    return _logger


logger = _setup_logger()
