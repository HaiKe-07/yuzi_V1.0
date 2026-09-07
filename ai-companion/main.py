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
from speech import get_default_asr, get_default_tts
from speech.player import is_available as player_available
from speech.recorder import is_available as mic_available
from core.conversation import ConversationManager, ConversationState


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

    # T1-05 自检：实例化 TTS 适配器并验证抽象接口可用
    tts = get_default_tts()
    logger.info(f"TTS 适配器就绪: {tts!r}")
    logger.info(
        f"播放环境可用: {player_available()} "
        f"（无 audio device 时只影响真实播放，不影响 TTS 模块加载）"
    )

    # T1-06/T1-07 自检：实例化对话管理器，串联 LLM+ASR+TTS+SQLite 持久化
    conv = ConversationManager(llm=llm, asr=asr, tts=tts)
    # 从数据库恢复最近对话历史
    conv.load_history()
    logger.info(f"对话管理器就绪: {conv.status()}")

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

    # T1-08 自检：FastAPI app 可创建 + 桌面端骨架文件齐全
    try:
        from server import create_app
        app = create_app()
        routes = {r.path for r in app.routes if hasattr(r, "path")}
        expected = {"/api/health", "/api/status", "/api/chat", "/api/settings"}
        assert expected <= routes, f"缺失路由: {expected - routes}"
        logger.info(f"HTTP API 路由就绪: {sorted(routes)[:6]}...")
    except Exception as e:
        logger.error(f"HTTP API 初始化失败: {e}")
        return 1

    ui_dir = ROOT / "ui"
    scaffold_files = [
        "package.json", "electron/main.js", "electron/preload.js",
        "src/App.vue", "src/components/ChatPanel.vue",
        "src/components/SettingsPanel.vue",
    ]
    missing = [f for f in scaffold_files if not (ui_dir / f).exists()]
    if missing:
        logger.warning(f"桌面端骨架文件缺失: {missing}")
    else:
        logger.info(f"桌面端骨架就绪（{len(scaffold_files)} 文件 + Electron + Vue）")

    logger.success("框架初始化完成，等待后续任务挂入核心模块。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
