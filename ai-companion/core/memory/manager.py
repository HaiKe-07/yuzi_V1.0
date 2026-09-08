"""长期记忆系统（T2-05）。

负责：
1. 从对话中提取值得长期记住的事实（规则式，避免 LLM 调用开销）
2. 用纯 Python 字符 n-gram TF-IDF + 余弦相似度做语义检索
   （无需 chromadb / numpy / jieba 等外部依赖）
3. 召回相关记忆，格式化为 prompt 中的 memory_brief
4. 去重（相似度高于阈值则更新而非新增）

设计要点：
- 零外部依赖：chromadb/numpy/sklearn 不可用时降级到纯 Python 检索
- 接口预留 vector_id 字段，未来可无缝接入 ChromaDB（替换 _search_semantic）
- 提取规则基于中文关键词模式，覆盖基本信息/兴趣/日程/人际/经历/情绪/偏好
- 重要度由规则推断（用户主动透露 > 偶尔提及），可手动调整

记忆类型：
    基本信息  姓名/年龄/职业/城市
    兴趣      喜欢/爱好
    偏好      讨厌/不喜欢/习惯
    日程      时间相关事项
    人际      家人/朋友/同事
    经历      过去发生的事
    情绪      情绪模式/心理状态
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

from utils.config import config
from utils.logger import logger

from .db import Database, db as default_db


# ============================================================
# 记忆类型常量
# ============================================================
class MemoryType:
    """长期记忆类型枚举（字符串常量，便于 SQL 查询）。"""
    PROFILE = "基本信息"      # 姓名/年龄/职业/城市
    INTEREST = "兴趣"          # 喜欢/爱好
    PREFERENCE = "偏好"        # 讨厌/不喜欢/习惯
    SCHEDULE = "日程"          # 时间相关事项
    RELATION = "人际"          # 家人/朋友/同事
    EXPERIENCE = "经历"        # 过去发生的事
    EMOTION = "情绪"           # 情绪模式/心理状态


# ============================================================
# 提取规则（关键词模式 → 记忆类型 + 推断重要度）
# ============================================================
@dataclass
class _ExtractRule:
    """单条提取规则。"""
    type: str
    patterns: tuple[str, ...]      # 命中任一模式则提取
    importance: int = 3            # 默认重要度
    # 提取内容裁剪：从命中关键词后取 N 字符作为记忆内容
    take_chars_after: int = 30


# 按优先级排序：更具体的规则在前，避免被通用规则抢占
_EXTRACT_RULES: list[_ExtractRule] = [
    # 基本信息（最高优先级，主动自报）
    _ExtractRule(
        MemoryType.PROFILE,
        (r"我叫(\S{1,8})", r"我是(\S{1,8})", r"我的名字是(\S{1,8})",
         r"我住在(\S{1,10})", r"我在(\S{1,8})(?:工作|上班|读|上学)"),
        importance=5, take_chars_after=20,
    ),
    # 兴趣（用户主动分享偏好）
    _ExtractRule(
        MemoryType.INTEREST,
        (r"我(?:很)?喜欢(\S{1,15})", r"我(?:很)?爱(\S{1,10})",
         r"我的爱好是(\S{1,15})", r"我(?:很)?迷(\S{1,10})"),
        importance=4, take_chars_after=25,
    ),
    # 偏好（讨厌/习惯）
    _ExtractRule(
        MemoryType.PREFERENCE,
        (r"我(?:很)?讨厌(\S{1,10})", r"我不喜欢(\S{1,10})",
         r"我习惯(\S{1,15})", r"我不能(\S{1,10})"),
        importance=3, take_chars_after=20,
    ),
    # 日程（时间相关）
    _ExtractRule(
        MemoryType.SCHEDULE,
        (r"(?:下周|明天|后天|大后天|周[一二三四五六日天末]|月底|下个月|今天)"
         r"[^。？\?]*",
         r"\d{1,2}月\d{1,2}日[^。？\?]*",
         r"\d{1,2}号[^。？\?]*"),
        importance=4, take_chars_after=0,  # 整句提取
    ),
    # 人际（家人朋友）
    _ExtractRule(
        MemoryType.RELATION,
        (r"我(?:的)?(爸爸|妈妈|父亲|母亲|哥哥|姐姐|弟弟|妹妹|老婆|丈夫|"
         r"男朋友|女朋友|男友|女友|儿子|女儿|朋友|同事|同学|老师)"
         r"[^。？\?]*",
         r"(?:爸爸|妈妈|父亲|母亲|哥哥|姐姐|弟弟|妹妹)说[^。？\?]*"),
        importance=4, take_chars_after=0,
    ),
    # 经历（过去时态）
    _ExtractRule(
        MemoryType.EXPERIENCE,
        (r"我(?:昨天|前天|上次|之前|上周|上个月|去年|刚才)"
         r"[^。？\?]*",
         r"我(?:去|做|买|吃|看|听|玩)过(\S{1,15})"),
        importance=2, take_chars_after=0,
    ),
    # 情绪（心理状态描述）
    _ExtractRule(
        MemoryType.EMOTION,
        (r"我(?:最近)?(?:很)?(开心|难过|焦虑|孤独|疲惫|累|压力大|"
         r"抑郁|emo|失落|害怕|担心)[^。？\?]*",
         r"我(?:最近)?状态[^。？\?]*"),
        importance=3, take_chars_after=0,
    ),
]


# ============================================================
# 纯 Python 文本向量化（字符 bigram TF-IDF）
# ============================================================
def _char_bigrams(text: str) -> list[str]:
    """中英文混合的字符 bigram 提取。

    中文按字切分取相邻二字，英文按词取整词。
    适合无分词器的场景，对中文语义检索效果尚可。
    """
    tokens: list[str] = []
    # 先提取英文单词（在去空白前，保留词边界）
    en_words = re.findall(r"[a-zA-Z]+", text)
    tokens.extend(w.lower() for w in en_words if len(w) >= 2)
    # 去标点、空白后按中文字符 bigram
    cn_text = re.sub(r"[\s，。！？、；：\"\"''（）()\[\]【】《》.,!?;:\-—]", "", text)
    # 移除英文/数字
    cn_text = re.sub(r"[a-zA-Z0-9]+", "", cn_text)
    for i in range(len(cn_text) - 1):
        tokens.append(cn_text[i:i + 2])
    return tokens


def _tf(tokens: list[str]) -> dict[str, float]:
    """词频向量。"""
    tf: dict[str, float] = {}
    n = len(tokens)
    if n == 0:
        return tf
    for t in tokens:
        tf[t] = tf.get(t, 0.0) + 1.0
    # 归一化
    for k in tf:
        tf[k] /= n
    return tf


def _cosine(v1: dict[str, float], v2: dict[str, float]) -> float:
    """两个稀疏向量的余弦相似度（纯 Python）。"""
    if not v1 or not v2:
        return 0.0
    # 点积
    dot = sum(v1[k] * v2.get(k, 0.0) for k in v1 if k in v2)
    if dot == 0.0:
        return 0.0
    # 模长
    norm1 = math.sqrt(sum(x * x for x in v1.values()))
    norm2 = math.sqrt(sum(x * x for x in v2.values()))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


# ============================================================
# 记忆条目数据类
# ============================================================
@dataclass
class Memory:
    """单条长期记忆。"""
    id: int | None
    type: str
    content: str
    importance: int = 3
    created_at: str | None = None
    last_recalled_at: str | None = None
    recall_count: int = 0
    vector_id: str | None = None

    @classmethod
    def from_row(cls, row: dict) -> "Memory":
        return cls(
            id=row.get("id"),
            type=row.get("type", ""),
            content=row.get("content", ""),
            importance=row.get("importance", 3),
            created_at=row.get("created_at"),
            last_recalled_at=row.get("last_recalled_at"),
            recall_count=row.get("recall_count", 0),
            vector_id=row.get("vector_id"),
        )

    def to_brief_line(self) -> str:
        """格式化为 memory_brief 中的一行。"""
        return f"- [{self.type}] {self.content}"


# ============================================================
# 记忆管理器
# ============================================================
class MemoryManager:
    """长期记忆管理器：提取、存储、检索、摘要。

    用法：
        mm = MemoryManager()
        mm.extract_and_store("我叫小明，我喜欢科幻电影")
        brief = mm.recall_brief("你最近看了什么电影")
        # brief → "- [兴趣] 科幻电影\\n- [基本信息] 小明"
    """

    def __init__(self, database: Database | None = None):
        self._db = database or default_db
        cfg = config.get("memory", {}) or {}
        self._recall_top_k = cfg.get("recall_top_k", 5)
        # 召回相似度阈值（低于此值不召回）
        self._recall_threshold = cfg.get("recall_threshold", 0.15)
        # 去重相似度阈值（高于此值视为重复，更新而非新增）
        self._dedup_threshold = cfg.get("dedup_threshold", 0.75)
        # 缓存：所有记忆的 TF 向量（id → tf），避免每次检索重算
        self._tf_cache: dict[int, dict[str, float]] = {}
        self._cache_loaded = False

    # ============================================================
    # 记忆提取
    # ============================================================
    def extract(self, text: str) -> list[Memory]:
        """从用户输入文本中提取值得记住的事实。

        规则式，无 LLM 调用，速度快、可控。
        返回提取出的 Memory 列表（尚未入库，id=None）。
        """
        if not text or not text.strip():
            return []
        memories: list[Memory] = []
        seen_contents: set[str] = set()  # 同一句内去重

        for rule in _EXTRACT_RULES:
            for pattern in rule.patterns:
                for m in re.finditer(pattern, text):
                    # 提取内容：整句匹配 or 关键词后 N 字
                    if rule.take_chars_after == 0:
                        content = m.group(0)
                    elif m.lastindex and m.lastindex >= 1:
                        # 有捕获组：取捕获组内容
                        content = m.group(1)
                    else:
                        content = m.group(0)

                    # 裁剪到合理长度
                    content = content.strip()[:60]
                    if len(content) < 2:
                        continue
                    # 句内去重
                    if content in seen_contents:
                        continue
                    seen_contents.add(content)

                    memories.append(Memory(
                        id=None, type=rule.type, content=content,
                        importance=rule.importance,
                    ))
        return memories

    def extract_and_store(self, text: str) -> list[Memory]:
        """提取并入库，返回实际存储的记忆（已去重）。"""
        candidates = self.extract(text)
        stored: list[Memory] = []
        if not candidates:
            return stored
        try:
            self._ensure_cache()
            for mem in candidates:
                # 去重：检查是否已有相似记忆
                dup_id = self._find_duplicate(mem.content, mem.type)
                if dup_id is not None:
                    # 已有相似记忆，更新重要度（取较高值）+ 内容
                    self._update_on_duplicate(dup_id, mem)
                    mem.id = dup_id
                    stored.append(mem)
                    logger.debug(f"记忆已存在(更新) id={dup_id} type={mem.type} content={mem.content!r}")
                else:
                    # 新记忆
                    mid = self._db.add_memory(
                        type=mem.type,
                        content=mem.content,
                        importance=mem.importance,
                    )
                    mem.id = mid
                    # 缓存 TF 向量
                    self._tf_cache[mid] = _tf(_char_bigrams(mem.content))
                    stored.append(mem)
                    logger.info(f"新记忆入库 id={mid} type={mem.type} content={mem.content!r}")
        except Exception as e:
            logger.warning(f"记忆提取入库失败（不影响对话）: {e}")
        return stored

    def _find_duplicate(self, content: str, mem_type: str) -> int | None:
        """在同类型记忆中找相似度超过 dedup_threshold 的，返回其 id。"""
        if not self._tf_cache:
            return None
        target_tf = _tf(_char_bigrams(content))
        if not target_tf:
            return None
        # 只在同类型记忆中找（提高效率 + 减少误判）
        best_id = None
        best_sim = 0.0
        for mid, tf in self._tf_cache.items():
            row = self._db.get_memory(mid)
            if not row or row.get("type") != mem_type:
                continue
            sim = _cosine(target_tf, tf)
            if sim > best_sim:
                best_sim = sim
                best_id = mid
        if best_sim >= self._dedup_threshold:
            return best_id
        return None

    def _update_on_duplicate(self, mem_id: int, new_mem: Memory) -> None:
        """重复记忆：提升重要度（取较高值），不覆盖原内容。"""
        try:
            old = self._db.get_memory(mem_id)
            if not old:
                return
            new_importance = max(old.get("importance", 3), new_mem.importance)
            with self._db.transaction() as cur:
                cur.execute(
                    "UPDATE memories SET importance = ? WHERE id = ?",
                    (new_importance, mem_id),
                )
            # 更新缓存 TF 向量（融合新旧内容提升召回）
            merged_tf = _tf(_char_bigrams(
                old.get("content", "") + " " + new_mem.content
            ))
            self._tf_cache[mem_id] = merged_tf
        except Exception as e:
            logger.debug(f"更新重复记忆失败: {e}")

    # ============================================================
    # 记忆检索
    # ============================================================
    def recall(
        self,
        query: str,
        top_k: int | None = None,
        min_importance: int = 1,
    ) -> list[Memory]:
        """从长期记忆中召回与 query 相关的 top_k 条。

        检索策略：
        1. 纯 Python TF-IDF 余弦相似度（语义层）
        2. 叠加重要度权重（important memories 优先）
        3. 召回计数衰减（频繁召回的稍微降权，避免老调重弹）
        """
        top_k = top_k or self._recall_top_k
        if not query.strip():
            return []
        try:
            self._ensure_cache()
            return self._search_semantic(query, top_k, min_importance)
        except Exception as e:
            logger.warning(f"记忆检索失败，降级到关键词: {e}")
            return self._search_keyword_fallback(query, top_k, min_importance)

    def _search_semantic(
        self, query: str, top_k: int, min_importance: int,
    ) -> list[Memory]:
        """纯 Python 语义检索：TF-IDF 余弦相似度 + 重要度加权。"""
        query_tf = _tf(_char_bigrams(query))
        if not query_tf or not self._tf_cache:
            return []
        scored: list[tuple[float, int]] = []
        for mid, tf in self._tf_cache.items():
            row = self._db.get_memory(mid)
            if not row:
                continue
            if row.get("importance", 3) < min_importance:
                continue
            sim = _cosine(query_tf, tf)
            if sim < self._recall_threshold:
                continue
            # 综合分 = 语义相似度 * 0.7 + 重要度归一化 * 0.3
            imp_norm = (row.get("importance", 3) - 1) / 4.0
            # 召回次数衰减：频繁召回的稍降权
            recall_decay = 1.0 / (1.0 + row.get("recall_count", 0) * 0.1)
            score = (sim * 0.7 + imp_norm * 0.3) * recall_decay
            scored.append((score, mid))
        if not scored:
            return []
        scored.sort(reverse=True)
        result: list[Memory] = []
        for score, mid in scored[:top_k]:
            row = self._db.get_memory(mid)
            if row:
                result.append(Memory.from_row(row))
                # 更新召回计数
                self._db.mark_memory_recalled(mid)
        logger.debug(f"记忆召回 query={query!r} 召回 {len(result)} 条")
        return result

    def _search_keyword_fallback(
        self, query: str, top_k: int, min_importance: int,
    ) -> list[Memory]:
        """语义检索失败时的关键词回退。"""
        rows = self._db.search_memories(
            keyword=query, min_importance=min_importance, limit=top_k,
        )
        return [Memory.from_row(r) for r in rows]

    # ============================================================
    # 记忆摘要（注入 prompt）
    # ============================================================
    def recall_brief(
        self,
        query: str,
        top_k: int | None = None,
    ) -> str:
        """召回相关记忆并格式化为 prompt 中的 memory_brief 字符串。

        语义检索无匹配时，回退到最近入库的记忆（保证 AI 总有上下文）。
        Returns:
            多行字符串，每行一条记忆。无记忆时返回空串。
        """
        memories = self.recall(query, top_k=top_k)
        if not memories:
            # 回退：取最近的几条记忆作为上下文
            memories = [
                Memory.from_row(r)
                for r in self._db.search_memories(
                    min_importance=2, limit=top_k or self._recall_top_k,
                )
            ]
        if not memories:
            return ""
        lines = [m.to_brief_line() for m in memories]
        return "\n".join(lines)

    def recent_brief(self, limit: int = 5) -> str:
        """获取最近入库的记忆摘要（供会话开始时注入）。

        与 recall_brief 不同：不基于 query 检索，按时间倒序取最新。
        """
        try:
            # 复用 search_memories 但不限关键词，按重要度+时间排序
            rows = self._db.search_memories(
                min_importance=2, limit=limit,
            )
            memories = [Memory.from_row(r) for r in rows]
            return "\n".join(m.to_brief_line() for m in memories) if memories else ""
        except Exception as e:
            logger.debug(f"获取近期记忆摘要失败: {e}")
            return ""

    # ============================================================
    # 缓存管理
    # ============================================================
    def _ensure_cache(self) -> None:
        """懒加载所有记忆的 TF 向量到缓存。"""
        if self._cache_loaded:
            return
        try:
            # 取所有记忆（重要度 >= 1）
            rows = self._db.search_memories(min_importance=1, limit=10000)
            self._tf_cache.clear()
            for row in rows:
                mid = row.get("id")
                if mid is None:
                    continue
                self._tf_cache[mid] = _tf(_char_bigrams(row.get("content", "")))
            self._cache_loaded = True
            logger.debug(f"记忆 TF 缓存已加载 {len(self._tf_cache)} 条")
        except Exception as e:
            logger.warning(f"加载记忆缓存失败: {e}")

    def invalidate_cache(self) -> None:
        """清空缓存（用于测试或手动重置）。"""
        self._tf_cache.clear()
        self._cache_loaded = False

    # ============================================================
    # 调试/统计
    # ============================================================
    def count(self) -> int:
        """记忆总数。"""
        try:
            self._ensure_cache()
            return len(self._tf_cache)
        except Exception:
            return 0

    def all_memories(self, mem_type: str | None = None) -> list[Memory]:
        """获取所有记忆（调试用）。"""
        rows = self._db.search_memories(
            type=mem_type, min_importance=1, limit=10000,
        )
        return [Memory.from_row(r) for r in rows]


# ============================================================
# 全局单例
# ============================================================
_memory_manager: MemoryManager | None = None


def get_memory_manager() -> MemoryManager:
    """获取全局 MemoryManager 单例。"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager


def reset_memory_manager() -> None:
    """重置全局单例（测试用）。"""
    global _memory_manager
    _memory_manager = None
