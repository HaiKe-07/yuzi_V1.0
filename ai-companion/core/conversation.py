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


def _maybe_create_emotion_engine():
    """按配置懒加载 AI 情绪引擎。

    config.emotion.ai_emotion_enabled = false 或加载失败时返回 None。
    """
    if not config.get("emotion.ai_emotion_enabled", True):
        return None
    try:
        from core.emotion import AIEmotionEngine
        return AIEmotionEngine(persistence=config.get("emotion.ai_emotion_enabled", True) is not False)
    except Exception as e:
        logger.warning(f"AI 情绪引擎加载失败（对话继续，无情绪能力）: {e}")
        return None


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
        emotion_engine=None,
        intimacy_manager=None,
        max_turns: int | None = None,
        db: Database | None = None,
        persist: bool = True,
    ):
        # 默认使用全局单例，便于测试时注入 mock
        self.llm = llm or get_default_llm()
        self.asr = asr or get_default_asr()
        self.tts = tts or get_default_tts()
        self.personality = personality_engine or personality

        # 情绪引擎：默认懒加载，注入 None 可关闭（纯文本测试）
        # 传入 False 显式禁用；传入对象则直接使用
        if emotion_engine is False:
            self.emotion = None
        else:
            self.emotion = emotion_engine or _maybe_create_emotion_engine()

        # 亲密度管理器（T2-03）：默认懒加载，注入 False 可关闭
        if intimacy_manager is False:
            self.intimacy = None
        else:
            try:
                from core.intimacy import get_intimacy_manager
                self.intimacy = intimacy_manager or get_intimacy_manager()
            except Exception as e:
                logger.warning(f"亲密度管理器加载失败: {e}")
                self.intimacy = None

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
            "emotion": [],
            "intimacy": [],
            "error": [],
        }

        # 同步亲密度到情绪引擎（T2-02/T2-03 联动）
        self._sync_intimacy_to_emotion()

        logger.info(
            f"ConversationManager 初始化 max_turns={max_turns} "
            f"llm={self.llm!r} asr={self.asr!r} tts={self.tts!r} "
            f"emotion={'on' if self.emotion else 'off'} "
            f"intimacy={'on' if self.intimacy else 'off'} "
            f"persist={self._persist}"
        )

    def _sync_intimacy_to_emotion(self) -> None:
        """把当前亲密度分数同步到情绪引擎（调节共鸣强度）。"""
        if self.emotion is None or self.intimacy is None:
            return
        try:
            self.emotion.set_intimacy_score(self.intimacy.get_score())
        except Exception as e:
            logger.debug(f"亲密度同步到情绪失败: {e}")

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
        - state_change(old, new):           状态变更
        - user_input(text, emotion=None):   用户输入（含检测到的情绪）
        - ai_reply(text, ai_emotion=None):  AI 回复（含 AI 当前情绪）
        - emotion(user_emotion, ai_emotion):情绪更新（T2-01）
        - error(exception):                 发生异常
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

        T2-01 起在每轮对话中：
        1. 检测用户情绪（关键词规则）
        2. 用用户情绪 + 内容极性更新 AI 独立情绪
        3. 把 AI 情绪上下文注入 system_prompt
        4. 对话记录携带情绪标签，便于回放与训练

        T2-03 起增加亲密度联动：
        5. 每轮日常闲聊 +0.5（边际递减）
        6. 检测伤害性话语 → on_hurt
        7. 同步亲密度到情绪引擎（调节共鸣）
        8. 亲密度上下文注入 system_prompt
        """
        if not user_text or not user_text.strip():
            return ""

        self._set_state(ConversationState.THINKING)

        # ---- T2-01/T2-02 情绪检测与 AI 情绪更新 ----
        user_emo_label = None
        user_emo_intensity = None
        ai_emo_label = None
        if self.emotion is not None:
            try:
                from core.emotion import detect_emotion
                # T2-02: 先应用时段影响 + 沉默累积（基于 last_interaction_at）
                self.emotion.update_from_time_context()
                self.emotion.update_from_silence()
                # T2-01: 用户情绪共鸣
                user_emotion = detect_emotion(user_text)
                # 用户情绪非中性时才更新 AI 情绪（避免中性把 AI 拉平）
                if user_emotion.primary.value != "中性":
                    self.emotion.update_from_user_emotion(user_emotion)
                # 内容极性影响（被夸/被骂）
                self.emotion.update_from_content(user_text)
                user_emo_label = user_emotion.primary.value
                user_emo_intensity = round(user_emotion.intensity, 2)
                ai_emo_label = self.emotion.get_label()
                self._emit(
                    "emotion",
                    user_emotion=user_emo_label,
                    ai_emotion=ai_emo_label,
                )
            except Exception as e:
                logger.debug(f"情绪处理失败（不影响对话）: {e}")

        # ---- T2-03 亲密度联动 ----
        intimacy_ctx = None
        if self.intimacy is not None:
            try:
                # 检测伤害性话语（强负面内容极性 → 触发 on_hurt）
                polarity = self._infer_polarity_for_intimacy(user_text)
                if polarity <= -0.5:
                    # severity: 0~1，越负面越严重
                    severity = min(1.0, abs(polarity))
                    self.intimacy.on_hurt(severity=severity)
                else:
                    # 日常闲聊 +0.5
                    self.intimacy.on_daily_chat()
                # 同步亲密度到情绪引擎
                self._sync_intimacy_to_emotion()
                intimacy_ctx = self.intimacy.context()
                self._emit(
                    "intimacy",
                    score=intimacy_ctx["score"],
                    level=intimacy_ctx["level"],
                )
            except Exception as e:
                logger.debug(f"亲密度处理失败（不影响对话）: {e}")

        self._emit("user_input", text=user_text, emotion=user_emo_label)
        self._add_turn(
            "user", user_text,
            emotion=user_emo_label,
            emotion_intensity=user_emo_intensity,
            intimacy_score=(
                self.intimacy.get_score() if self.intimacy else None
            ),
        )

        try:
            sys_prompt = self.personality.build_system_prompt()
            # 注入 AI 当前情绪上下文
            if self.emotion is not None and ai_emo_label:
                try:
                    ctx = self.emotion.emotion_context()
                    sys_prompt = sys_prompt + "\n" + ctx["prompt_hint"]
                except Exception as e:
                    logger.debug(f"情绪上下文注入失败: {e}")
            # T2-03: 注入亲密度上下文（称呼风格提示）
            if intimacy_ctx is not None:
                sys_prompt = sys_prompt + "\n" + intimacy_ctx["prompt_hint"]
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
            ai_emotion=ai_emo_label,
        )
        self._emit("ai_reply", text=resp.text, ai_emotion=ai_emo_label)
        self._set_state(ConversationState.IDLE)
        return resp.text

    def _infer_polarity_for_intimacy(self, text: str) -> float:
        """推断用户话语对 AI 的友好度（用于亲密度伤害检测）。

        复用 AIEmotionEngine 的极性推断逻辑；情绪引擎关闭时用简易规则。
        """
        if self.emotion is not None:
            try:
                return self.emotion._infer_polarity(text)
            except Exception:
                pass
        # 简易回退：检测常见伤害词
        hurt_words = ["讨厌", "滚", "烦死", "闭嘴", "笨", "恶心", "去死"]
        pos_words = ["谢谢", "喜欢", "爱你", "想你"]
        neg = sum(1 for w in hurt_words if w in text)
        pos = sum(1 for w in pos_words if w in text)
        if pos == neg:
            return 0.0
        return (pos - neg) / max(pos, neg)

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
            "emotion": "on" if self.emotion else "off",
            "ai_emotion": self.emotion.get_label() if self.emotion else None,
            "intimacy": "on" if self.intimacy else "off",
            "intimacy_score": self.intimacy.get_score() if self.intimacy else None,
            "intimacy_level": (
                self.intimacy.get_level().value if self.intimacy else None
            ),
            "llm": repr(self.llm),
            "asr": repr(self.asr),
            "tts": repr(self.tts),
        }
