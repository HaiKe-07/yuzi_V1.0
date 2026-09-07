"""AI 陪伴助手 - 主入口。

第一阶段 T1-01 仅做框架自检与启动验证，后续任务（ASR/LLM/TTS/对话循环）
会逐步挂入 core/conversation.py 等。

运行：
    python main.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 把项目根目录加入 sys.path，方便 `from core.xxx import ...` 这种导入
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.config import config
from utils.logger import logger


def banner() -> str:
    name = config.get("app.companion_name") or "(未命名)"
    return (
        "\n========================================\n"
        f"  {config.get('app.name', 'AI 陪伴助手')}  v{config.get('app.version', '0.0.0')}\n"
        f"  助理名字 : {name}\n"
        f"  配置文件 : {config.config_path}\n"
        f"  数据目录 : {ROOT / config.get('app.data_dir', 'data')}\n"
        f"  日志目录 : {ROOT / config.get('app.log_dir', 'logs')}\n"
        "========================================\n"
    )


def main() -> int:
    logger.info("启动 AI 陪伴助手...")
    print(banner(), flush=True)

    # 简单自检：核心配置项可读
    logger.info(f"LLM provider = {config.get('llm.provider')}")
    logger.info(f"ASR provider = {config.get('asr.provider')}")
    logger.info(f"TTS provider = {config.get('tts.provider')}")
    logger.info(f"短期记忆轮数 = {config.get('memory.short_term_turns')}")

    logger.success("框架初始化完成，等待后续任务挂入核心模块。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
