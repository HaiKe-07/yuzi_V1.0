"""配置加载模块。

负责：
1. 读取项目根目录的 config.yaml
2. 用 .env / 环境变量覆盖形如 ${VAR} 的占位符
3. 提供全局单例 Config，模块按路径点取（如 config.get("llm.deepseek.api_key")）
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv 未装时回退到纯环境变量
    def load_dotenv(*_a, **_kw):
        return False


_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value: Any) -> Any:
    """递归把字符串中的 ${VAR} 替换为环境变量值，未设置则保留空串。"""
    if isinstance(value, str):
        return _VAR_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


class Config:
    """全局配置单例。

    用法：
        from utils.config import config
        api_key = config.get("llm.deepseek.api_key")
        all_cfg = config.all()
    """

    _instance: "Config | None" = None

    def __init__(self, config_path: str | Path | None = None):
        # 项目根目录 = 本文件上一级
        root = Path(__file__).resolve().parent.parent
        self._root = root
        env_path = root / ".env"
        if env_path.exists():
            load_dotenv(env_path)

        cfg_path = Path(config_path) if config_path else root / "config.yaml"
        if not cfg_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {cfg_path}")
        with cfg_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        self._data = _expand_env(raw)
        self._cfg_path = cfg_path

    @classmethod
    def get_instance(cls) -> "Config":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reload(cls, config_path: str | Path | None = None) -> "Config":
        """重新加载配置，测试或切换 profile 时用。"""
        cls._instance = cls(config_path)
        return cls._instance

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """按点号路径取值，如 config.get('llm.deepseek.api_key')。"""
        node: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted_key: str, value: Any) -> None:
        """按点号路径设值（内存中，不落盘）。

        用于运行时配置覆盖（如 settings 接口）。
        若中间节点不存在会自动创建。
        """
        parts = dotted_key.split(".")
        node: Any = self._data
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    def all(self) -> dict:
        return self._data

    @property
    def root(self) -> Path:
        return self._root

    @property
    def config_path(self) -> Path:
        return self._cfg_path

    def path(self, *relative: str) -> Path:
        """把 config 中相对路径转成项目根下的绝对路径，自动创建目录。"""
        p = self._root.joinpath(*relative)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


# 模块级单例
config = Config.get_instance()
