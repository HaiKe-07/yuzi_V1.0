"""HTTP API 层。

把 ConversationManager 暴露给 Electron 前端，让 Vue 渲染进程
通过本地 HTTP 调用对话/语音/设置能力。

设计原则：
- 单例 conversation_manager，与 FastAPI app 一起在主进程跑
- 端口默认 18731（可在 config.server.port 覆盖）
- 启动时 load_history，运行时所有对话自动落库
- WS / SSE 流式回复在 T2 阶段扩展（T1-08 先做同步接口）

接口：
    GET  /api/health           健康检查
    GET  /api/status           对话状态
    GET  /api/history           拉取最近对话
    POST /api/chat              文本对话（不调用 TTS）
    POST /api/speak             文本转语音（仅 TTS）
    POST /api/voice             语音对话（需音频上传，T1-08 占位）
    GET  /api/settings          读配置
    PUT  /api/settings          改配置（重启生效）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pydantic import Field

from utils.config import config
from utils.logger import logger

from core.conversation import ConversationManager
from core.personality import personality


# ============================================================
# 请求/响应模型
# ============================================================
class ChatRequest(BaseModel):
    text: str = Field(..., description="用户输入文本")


class SpeakRequest(BaseModel):
    text: str = Field(..., description="待播报文本")


class SettingsUpdate(BaseModel):
    """配置覆盖项，全部可选。"""
    user_name: str | None = None
    companion_name: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    asr_provider: str | None = None
    tts_provider: str | None = None
    tts_voice: str | None = None


# ============================================================
# 全局 conversation manager 单例
# ============================================================
_manager: ConversationManager | None = None


def get_manager() -> ConversationManager:
    global _manager
    if _manager is None:
        _manager = ConversationManager()
        _manager.load_history()
        logger.info("HTTP API: ConversationManager 已初始化并加载历史")
    return _manager


def reset_manager() -> None:
    """测试用：重置单例。"""
    global _manager
    _manager = None


# ============================================================
# FastAPI app
# ============================================================
def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Companion API",
        version="0.1.0",
        description="本地 HTTP 接口，供 Electron 前端调用",
    )
    # 允许 Electron 渲染进程跨域（file:// 协议）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------- 健康检查 --------
    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": "0.1.0"}

    # -------- 状态 --------
    @app.get("/api/status")
    async def status():
        return get_manager().status()

    # -------- 历史 --------
    @app.get("/api/history")
    async def history(limit: int = 50):
        m = get_manager()
        return [
            {
                "role": t.role,
                "content": t.content,
                "timestamp": t.timestamp,
                "metadata": t.metadata,
            }
            for t in m.history[-limit:]
        ]

    # -------- 文本对话 --------
    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        m = get_manager()
        try:
            reply = m.text_chat(req.text)
        except Exception as e:
            logger.exception("chat 接口异常")
            raise HTTPException(status_code=500, detail=str(e))
        return {"reply": reply, "state": m.state.value}

    # -------- 仅 TTS 播报 --------
    @app.post("/api/speak")
    async def speak(req: SpeakRequest):
        m = get_manager()
        try:
            result = m.speak(req.text)
        except Exception as e:
            logger.exception("speak 接口异常")
            raise HTTPException(status_code=500, detail=str(e))
        # 不回传音频字节（前端通过 player 直接听），只回传元信息
        return {
            "voice": result.voice,
            "format": result.format,
            "cached": result.cached,
            "bytes": len(result.audio),
        }

    # -------- 配置读 --------
    @app.get("/api/settings")
    async def get_settings():
        return {
            "user_name": config.get("user.name", ""),
            "companion_name": config.get("companion.name", ""),
            "llm": {
                "provider": config.get("llm.provider"),
                "model": config.get("llm.deepseek.model"),
                "base_url": config.get("llm.deepseek.base_url"),
            },
            "asr": {
                "provider": config.get("asr.provider"),
                "language": config.get("asr.openai.language", "zh"),
            },
            "tts": {
                "provider": config.get("tts.provider"),
                "voice": config.get("tts.openai.voice"),
                "model": config.get("tts.openai.model"),
            },
        }

    # -------- 配置改（覆盖 .env 值，重启生效） --------
    @app.put("/api/settings")
    async def update_settings(req: SettingsUpdate):
        updates = req.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="无更新字段")
        # 当前只允许改这些字段
        mapping = {
            "user_name": ("user.name", updates.get("user_name")),
            "companion_name": ("companion.name", updates.get("companion_name")),
            "llm_provider": ("llm.provider", updates.get("llm_provider")),
            "llm_model": ("llm.deepseek.model", updates.get("llm_model")),
            "asr_provider": ("asr.provider", updates.get("asr_provider")),
            "tts_provider": ("tts.provider", updates.get("tts_provider")),
            "tts_voice": ("tts.openai.voice", updates.get("tts_voice")),
        }
        applied = []
        for k, (path, v) in mapping.items():
            if v is not None:
                config.set(path, v)
                applied.append(path)
        # 覆盖后需要重建 manager 单例（适配器持有了旧配置）
        reset_manager()
        logger.info(f"配置已更新: {applied}（manager 单例已重置）")
        return {"applied": applied}

    return app


# 全局 app 实例
app = create_app()


def run(host: str = "127.0.0.1", port: int | None = None) -> None:
    """启动 HTTP server。

    仅供 main.py / electron main process 调用。
    """
    port = port or config.get("server.port", 18731)
    import uvicorn
    logger.info(f"HTTP API 启动于 http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
