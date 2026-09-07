"""对话管理器。

负责串联 ASR → LLM → TTS 的完整对话流程，并管理对话状态。

状态机：
    IDLE → LISTENING → THINKING → SPEAKING → IDLE

第一阶段（T1-06）实现：
- 单轮对话闭环（text 输入 → LLM → TTS → 播放）
- 短期记忆：保留最近 N 轮对话（默认 20，来自 config.memory.short_term_turns）
- 事件钩子：on_state_change / on_user_input / on_ai_reply / on_error
- 不依赖真实麦克风：text_chat() 可独立运行

T2-08 阶段扩展：
- 语音唤醒 / 打断
- 流式 LLM + 流式 TTS（边想边说）
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from utils.config import config
from utils.logger import logger

from llm import LLMResponse, Message, get_default_llm
from core.personality import PersonalityContext, personality
from core.memory import db as db_module
from core.memory.db import Database, ConversationRecord
from speech import (
    ASRResult, BaseASR, BaseTTS, TTSResult,
    get_default_asr, get_default_tts,
)

# 事件回调类型
EventCallback = Callable[..., Any]


class ConversationState(str, Enum):
    """对话状态机。"""
    IDLE = "idle"           # 待机
    LISTENING = "listening" # 录音中
    THINKING = "thinking"   # LLM 思考中
    SPEAKING = "speaking"   # TTS 播报中


@dataclass
class ConversationTurn:
    """单轮对话记录。"""
    role: str               # "user" / "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class ConversationManager:
    """对话管理器：状态机 + 短期记忆 + 事件钩子。"""

    def __init__(
        self,
        llm=None,
        asr: BaseASR | None = None,
        tts: BaseTTS | None = None,
        personality_engine=None,
        max_turns: int | None = None,
        db: Database | None = None,
        persist: bool = True,
    ):
        # 默认使用全局单例，便于测试时注入 mock
        self.llm = llm or get_default_llm()
        self.asr = asr or get_default_asr()
        self.tts = tts or get_default_tts()
        self.personality = personality_engine or personality

        # 持久化：默认开启，注入 None 可关闭（用于纯内存测试）
        self._db = db if db is not None else db_module
        self._persist = persist
        if self._persist:
            try:
                self._db.init()
            except Exception as e:
                logger.warning(f"数据库初始化失败，关闭持久化: {e}")
                self._persist = False

        # 短期记忆：保留最近 N 轮（user+assistant 各算 1 轮）
        max_turns = max_turns or config.get("memory.short_term_turns", 20)
        self._history: deque[ConversationTurn] = deque(maxlen=max_turns * 2)
        self._max_turns = max_turns

        # 状态机
        self._state = ConversationState.IDLE

        # 事件钩子
        self._hooks: dict[str, list[EventCallback]] = {
            "state_change": [],
            "user_input": [],
            "ai_reply": [],
            "error": [],
        }

        logger.info(
            f"ConversationManager 初始化 max_turns={max_turns} "
            f"llm={self.llm!r} asr={self.asr!r} tts={self.tts!r} "
            f"persist={self._persist}"
        )

    # ============================================================
    # 状态机
    # ============================================================
    @property
    def state(self) -> ConversationState:
        return self._state

    def _set_state(self, new_state: ConversationState) -> None:
        if new_state == self._state:
            return
        old = self._state
        self._state = new_state
        logger.debug(f"状态变更: {old.value} → {new_state.value}")
        self._emit("state_change", old=old, new=new_state)

    # ============================================================
    # 事件钩子
    # ============================================================
    def on(self, event: str, callback: EventCallback) -> None:
        """注册事件回调。

        事件：
        - state_change(old, new): 状态变更
        - user_input(text):        用户输入（文本或 ASR 结果）
        - ai_reply(text):          AI 回复文本
        - error(exception):        发生异常
        """
        if event not in self._hooks:
            raise ValueError(f"未知事件: {event!r}")
        self._hooks[event].append(callback)

    def _emit(self, event: str, **kwargs: Any) -> None:
        for cb in self._hooks.get(event, []):
            try:
                cb(**kwargs)
            except Exception as e:
                logger.error(f"事件回调 {event!r} 异常: {e}")

    # ============================================================
    # 短期记忆管理
    # ============================================================
    @property
    def history(self) -> list[ConversationTurn]:
        return list(self._history)

    def _add_turn(self, role: str, content: str, **meta: Any) -> None:
        self._history.append(ConversationTurn(
            role=role, content=content, metadata=meta,
        ))
        # 持久化到 SQLite
        if self._persist:
            try:
                self._db.add_conversation(
                    role=role,
                    content=content,
                    emotion=meta.get("emotion"),
                    emotion_intensity=meta.get("emotion_intensity"),
                    intimacy_score=meta.get("intimacy_score"),
                    ai_emotion=meta.get("ai_emotion"),
                    usage=meta.get("usage"),
                    metadata={
                        k: v for k, v in meta.items()
                        if k not in (
                            "emotion", "emotion_intensity",
                            "intimacy_score", "ai_emotion", "usage",
                        )
                    },
                )
            except Exception as e:
                logger.warning(f"对话持久化失败（不影响对话流程）: {e}")

    def _to_llm_messages(self) -> list[Message]:
        """把短期记忆转为 LLM 输入格式。"""
        return [
            Message(role=t.role, content=t.content)
            for t in self._history
        ]

    def clear_history(self) -> None:
        self._history.clear()
        logger.info("短期记忆已清空")

    def load_history(self, limit: int | None = None) -> int:
        """从数据库恢复短期记忆。

        启动时调用，把最近 N 条对话填回短期记忆缓冲区。
        Args:
            limit: 取多少条，None 用 max_turns*2
        Returns:
            实际加载条数
        """
        if not self._persist:
            return 0
        n = limit or (self._max_turns * 2)
        try:
            records = self._db.get_recent_conversations(limit=n)
        except Exception as e:
            logger.warning(f"加载历史失败: {e}")
            return 0
        self._history.clear()
        for r in records:
            self._history.append(ConversationTurn(
                role=r.role, content=r.content,
                metadata={
                    "id": r.id, "timestamp": r.timestamp,
                    "emotion": r.emotion, "ai_emotion": r.ai_emotion,
                    "intimacy_score": r.intimacy_score,
                    "usage": r.usage,
                },
            ))
        logger.info(f"已从数据库恢复 {len(records)} 条对话历史")
        return len(records)

    # ============================================================
    # 对话循环
    # ============================================================
    def text_chat(self, user_text: str) -> str:
        """文本对话：用户输入 → LLM → 返回 AI 回复文本。

        不调用 TTS/ASR，便于测试与纯文本场景。
        TTS 通过 speak() 单独触发。
        """
        if not user_text or not user_text.strip():
            return ""

        self._set_state(ConversationState.THINKING)
        self._emit("user_input", text=user_text)
        self._add_turn("user", user_text)

        try:
            sys_prompt = self.personality.build_system_prompt()
            resp = self.llm.chat(
                self._to_llm_messages(),
                system_prompt=sys_prompt,
            )
        except Exception as e:
            self._set_state(ConversationState.IDLE)
            self._emit("error", exception=e)
            logger.error(f"LLM 调用失败: {e}")
            raise

        self._add_turn(
            "assistant", resp.text,
            usage=resp.usage, finish_reason=resp.finish_reason,
        )
        self._emit("ai_reply", text=resp.text)
        self._set_state(ConversationState.IDLE)
        return resp.text

    def speak(self, text: str) -> TTSResult:
        """把文本转为语音并播放。

        Args:
            text: 待播报文本
        Returns:
            TTSResult（含 cached 标记）
        """
        if not text or not text.strip():
            return TTSResult(audio=b"", voice=self.tts.default_voice)

        self._set_state(ConversationState.SPEAKING)
        try:
            result = self.tts.synthesize(text)
            # 播放（无 audio device 时静默失败）
            from speech.player import is_available as player_ok
            if player_ok() and result.audio:
                from speech.player import play
                play(result.audio, format=result.format, blocking=True)
        except Exception as e:
            self._set_state(ConversationState.IDLE)
            self._emit("error", exception=e)
            logger.error(f"TTS 调用失败: {e}")
            raise
        finally:
            self._set_state(ConversationState.IDLE)
        return result

    def voice_chat(self, audio: bytes | None = None) -> str:
        """语音对话：录音 → ASR → LLM → TTS → 播放。

        Args:
            audio: 已录好的 WAV 字节。None 则现场录音。
        Returns:
            AI 回复文本
        """
        # 1. 录音
        if audio is None:
            self._set_state(ConversationState.LISTENING)
            from speech.recorder import record_until_silence
            audio = record_until_silence()
        else:
            self._set_state(ConversationState.LISTENING)

        # 2. ASR
        try:
            asr_result: ASRResult = self.asr.transcribe(audio)
        except Exception as e:
            self._set_state(ConversationState.IDLE)
            self._emit("error", exception=e)
            logger.error(f"ASR 调用失败: {e}")
            raise

        user_text = asr_result.text
        if not user_text:
            logger.info("ASR 识别为空，跳过本轮")
            self._set_state(ConversationState.IDLE)
            return ""

        # 3. LLM
        reply = self.text_chat(user_text)

        # 4. TTS + 播放
        if reply:
            self.speak(reply)

        return reply

    # ============================================================
    # 调试
    # ============================================================
    def status(self) -> dict:
        """当前状态摘要，调试用。"""
        return {
            "state": self._state.value,
            "history_len": len(self._history),
            "max_turns": self._max_turns,
            "llm": repr(self.llm),
            "asr": repr(self.asr),
            "tts": repr(self.tts),
        }
