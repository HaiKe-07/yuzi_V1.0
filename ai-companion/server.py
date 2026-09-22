"""HTTP API 层。

把 ConversationManager 暴露给 Electron 前端，让 Vue 渲染进程
通过本地 HTTP 调用对话/语音/设置能力。

设计原则：
- 单例 conversation_manager，与 FastAPI app 一起在主进程跑
- 端口默认 18731（可在 config.server.port 覆盖）
- 启动时 load_history，运行时所有对话自动落库
- WS / SSE 流式回复在 T2 阶段扩展（T1-08 先做同步接口）

接口：
    GET  /api/health            健康检查
    GET  /api/status            对话状态
    GET  /api/history           拉取最近对话
    POST /api/chat              文本对话（不调用 TTS）
    POST /api/speak             文本转语音（仅 TTS）
    POST /api/voice             语音对话（需音频上传，T1-08 占位）
    GET  /api/proactive/check   主动话题检测（T2-07）
    POST /api/wake/check        唤醒词检测（文本模式，T2-08）
    POST /api/wake/listening    启动/停止唤醒词音频监听（T2-08）
    POST /api/interrupt         手动触发打断（T2-08）
    GET  /api/live2d/status     Live2D 形象状态（含表情名，T3-01/T3-02）
    POST /api/live2d/wake       唤醒形象事件（T3-05：记录唤醒并返回轻应声）
    GET  /api/emotion/stream    情绪变化 SSE 推送（T3-02）
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


class WakeCheckRequest(BaseModel):
    """唤醒词文本检测请求（T2-08）。"""
    text: str = Field(..., description="待检测文本")


class WakeListeningRequest(BaseModel):
    """唤醒词音频监听开关请求（T2-08）。"""
    enabled: bool = Field(True, description="True=启动监听；False=停止")


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

    # -------- 主动话题检测（T2-07）--------
    @app.get("/api/proactive/check")
    async def proactive_check():
        """前端定时轮询（建议每 5-10 分钟）。

        返回主动话题文本，或 None（无需触发）。
        前端拿到文本后自行调用 /api/chat 发起对话。
        """
        m = get_manager()
        text = m.check_proactive()
        return {"triggered": text is not None, "text": text}

    # -------- 唤醒词检测（T2-08）--------
    @app.post("/api/wake/check")
    async def wake_check(req: WakeCheckRequest):
        """文本模式唤醒词检测。

        前端在用户输入框收到文本后，发送文本到这里检测是否含唤醒词。
        若命中，state 会置 IDLE（就绪），前端可继续走正常对话流程。

        音频唤醒词监听需另外调用 /api/wake/listening 启动。
        """
        m = get_manager()
        triggered = m.check_wake_word(text=req.text)
        return {"triggered": triggered, "state": m.state.value}

    @app.post("/api/wake/listening")
    async def wake_listening(req: WakeListeningRequest):
        """启动/停止唤醒词音频监听。

        前端启动应用后调用 enabled=true 开启持续监听；
        退出应用或需要安静时调用 enabled=false 停止。

        注意：需要麦克风和 numpy 支持，沙箱/无麦克风环境会返回 ok=false。
        """
        m = get_manager()
        if req.enabled:
            ok = m.start_wake_listening()
            return {"ok": ok, "listening": m.wake_word.is_listening if m.wake_word else False}
        else:
            m.stop_wake_listening()
            return {"ok": True, "listening": False}

    # -------- 手动打断（T2-08）--------
    @app.post("/api/interrupt")
    async def interrupt():
        """手动触发打断。

        前端在用户点击"停止播放"按钮或检测到用户开口时调用。
        会停止当前 TTS 播放并把状态置为 IDLE。
        """
        m = get_manager()
        m.interrupt()
        return {"ok": True, "state": m.state.value}

    # -------- Live2D 状态（T3-01/T3-02）--------
    @app.get("/api/live2d/status")
    async def live2d_status():
        """前端 Live2D 形象轮询用（建议每 1-2 秒）。

        返回 AI 当前情绪标签 + 对话状态，前端据此切换 Live2D 表情。
        T3-01: 前端启动时调用获取初始状态。
        T3-02: 轮询检测情绪变化 → 切换表情。
        """
        m = get_manager()
        s = m.status()
        # T3-02: 把情绪标签映射成 Live2D 表情名
        emotion_label = s.get("ai_emotion")
        expression = None
        intensity = 0.5
        if emotion_label and m.emotion:
            try:
                from core.emotion import emotion_to_expression
                state = m.emotion.get_state()
                expression = emotion_to_expression(emotion_label, state.intensity)
                intensity = round(state.intensity, 2)
            except Exception:
                pass
        return {
            "ai_emotion": emotion_label,
            "expression": expression,        # Live2D 表情文件名
            "intensity": intensity,           # 情绪强度 0~1
            "state": s.get("state"),
            "wake_word": s.get("wake_word"),
            "wake_listening": s.get("wake_listening"),
            # T3-05: 唤醒计数/最近唤醒时间/轻应声（前端轮询检测点亮）
            "wake_count": s.get("wake_count"),
            "last_wake_ts": s.get("last_wake_ts"),
            "wake_greeting": s.get("wake_greeting"),
            "intimacy_level": s.get("intimacy_level"),
            "intimacy_score": s.get("intimacy_score"),
        }

    @app.post("/api/live2d/wake")
    async def live2d_wake():
        """唤醒形象（T3-05）。

        唤醒词触发后，前端调用此接口让后端记录唤醒事件。
        后端会把状态置为 IDLE，并返回轻应声文案供前端播报。
        """
        m = get_manager()
        m._on_wake()
        return {
            "ok": True,
            "state": m.state.value,
            "wake_count": m._wake_count,
            "greeting": m.wake_greeting(),
        }

    # -------- 情绪推送（T3-02 SSE）--------
    @app.get("/api/emotion/stream")
    async def emotion_stream():
        """SSE 推送：情绪变化时主动通知前端切换表情。

        比 /api/live2d/status 轮询更实时：
        - 前端 EventSource 连接此端点
        - 后端在情绪变化时推送 {expression, intensity} 事件
        - 前端收到后调用 manager.setExpression()

        事件格式：
            data: {"expression": "happy_strong", "intensity": 0.8, "emotion": "开心"}
        """
        from fastapi.responses import StreamingResponse
        import asyncio
        import json

        m = get_manager()
        last_expression = None

        async def event_generator():
            nonlocal last_expression
            while True:
                try:
                    s = m.status()
                    emotion_label = s.get("ai_emotion")
                    expression = None
                    intensity = 0.5
                    if emotion_label and m.emotion:
                        from core.emotion import emotion_to_expression
                        state = m.emotion.get_state()
                        expression = emotion_to_expression(emotion_label, state.intensity)
                        intensity = round(state.intensity, 2)
                    # 只在表情变化时推送
                    if expression and expression != last_expression:
                        last_expression = expression
                        yield f"data: {json.dumps({'expression': expression, 'intensity': intensity, 'emotion': emotion_label}, ensure_ascii=False)}\n\n"
                    yield ": heartbeat\n\n"
                except Exception:
                    yield f"data: {json.dumps({'error': 'emotion_unavailable'})}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

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
