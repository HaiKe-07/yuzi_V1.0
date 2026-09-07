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
from llm import get_default_llm, Message
from core.personality import personality, PersonalityContext
from speech import get_default_asr
from speech.recorder import is_available as mic_available


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

    # T1-02 自检：实例化 LLM 适配器并验证抽象接口可用
    llm = get_default_llm()
    logger.info(f"LLM 适配器就绪: {llm!r}")
    api_key = getattr(llm, "api_key", "")

    # T1-03 自检：用基础人格 prompt 生成 system_prompt
    sys_prompt = personality.build_system_prompt()
    logger.info(f"人格 prompt 已生成 长度={len(sys_prompt)}")

    # T1-04 自检：实例化 ASR 适配器并验证抽象接口可用
    asr = get_default_asr()
    logger.info(f"ASR 适配器就绪: {asr!r}")
    logger.info(
        f"录音环境可用: {mic_available()} "
        f"（无 sounddevice/PortAudio 时只影响真实录音，不影响 ASR 模块加载）"
    )

    if api_key:
        try:
            msgs = [Message(role="user", content="你好，请用一句话自我介绍。")]
            resp = llm.chat(msgs, system_prompt=sys_prompt)
            logger.success(f"LLM 回复（带人格）: {resp.text[:80]}")
            logger.info(f"token 用量: {resp.usage}")
        except Exception as e:
            logger.warning(f"LLM 联调失败（可能是密钥未配置）: {e}")
    else:
        logger.warning("LLM api_key 为空，跳过在线调用，仅验证人格 prompt 已生成。")
        # 打印 prompt 头部预览，确认无残留占位符
        logger.info(f"prompt 预览:\n{personality.preview()[:200]}")

    logger.success("框架初始化完成，等待后续任务挂入核心模块。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
