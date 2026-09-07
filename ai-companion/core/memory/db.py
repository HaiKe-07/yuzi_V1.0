"""对话历史持久化（SQLite）。

负责：
1. 初始化数据库 schema（conversations / memories / intimacy_state）
2. 提供对话记录的读写接口
3. 提供长期记忆的存储/检索接口（T2-05 启用向量检索后扩展）

表结构：
- conversations: 对话历史（含 role/content/emotion/intimacy_score 预留字段）
- memories:      长期记忆（type/content/importance/recall_count）
- intimacy_state: 亲密度状态快照（T2-03 用）

设计要点：
- 单连接 + check_same_thread=False，便于跨线程访问
- 上下文管理器保证 commit/rollback
- WAL 模式提升并发读
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from utils.config import config
from utils.logger import logger


# ============================================================
# Schema 定义（按需扩展，迁移用 SCHEMA_VERSION 控制）
# ============================================================
SCHEMA_VERSION = 1

_SCHEMA_SQL = [
    # 对话历史表
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
        content TEXT NOT NULL,
        emotion TEXT,              -- 预留：情绪标签（T2-01 启用）
        emotion_intensity REAL,    -- 预留：情绪强度 1-5
        intimacy_score REAL,       -- 预留：当时亲密度
        ai_emotion TEXT,           -- 预留：AI 当时情绪
        usage_json TEXT,            -- token 用量 JSON
        metadata_json TEXT          -- 其他元数据
    );
    """,
    # 长期记忆表（T2-05 启用向量检索时扩展）
    """
    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,        -- 基本信息/兴趣/日程/人际/经历/情绪/偏好
        content TEXT NOT NULL,
        importance INTEGER DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
        vector_id TEXT,            -- ChromaDB 中的向量 ID（T2-05 启用）
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_recalled_at DATETIME,
        recall_count INTEGER DEFAULT 0,
        source_turn_id INTEGER     -- 来源对话 ID
    );
    """,
    # 亲密度状态快照（T2-03 用，T1-07 仅建表）
    """
    CREATE TABLE IF NOT EXISTS intimacy_state (
        id INTEGER PRIMARY KEY,
        score REAL NOT NULL DEFAULT 5.0,
        stage TEXT NOT NULL DEFAULT '陌生',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        metadata_json TEXT
    );
    """,
    # schema 版本表
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # 索引：按时间查对话
    "CREATE INDEX IF NOT EXISTS idx_conversations_ts ON conversations(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_conversations_role ON conversations(role);",
    # 索引：按类型/重要度查记忆
    "CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);",
    "CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance);",
]


# ============================================================
# 数据类
# ============================================================
class ConversationRecord:
    """对话记录。简单容器，避免引 ORM 重型依赖。"""

    __slots__ = (
        "id", "timestamp", "role", "content",
        "emotion", "emotion_intensity", "intimacy_score",
        "ai_emotion", "usage", "metadata",
    )

    def __init__(
        self,
        role: str,
        content: str,
        timestamp: str | None = None,
        id: int | None = None,
        emotion: str | None = None,
        emotion_intensity: float | None = None,
        intimacy_score: float | None = None,
        ai_emotion: str | None = None,
        usage: dict | None = None,
        metadata: dict | None = None,
    ):
        self.id = id
        self.timestamp = timestamp or datetime.now().isoformat()
        self.role = role
        self.content = content
        self.emotion = emotion
        self.emotion_intensity = emotion_intensity
        self.intimacy_score = intimacy_score
        self.ai_emotion = ai_emotion
        self.usage = usage or {}
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "role": self.role,
            "content": self.content,
            "emotion": self.emotion,
            "emotion_intensity": self.emotion_intensity,
            "intimacy_score": self.intimacy_score,
            "ai_emotion": self.ai_emotion,
            "usage": self.usage,
            "metadata": self.metadata,
        }


# ============================================================
# 数据库管理器
# ============================================================
class Database:
    """SQLite 数据库管理器。

    使用：
        from core.memory.db import db
        db.init()
        with db.transaction() as cur:
            cur.execute("INSERT INTO ...")
        records = db.get_recent_conversations(limit=20)
    """

    _instance: "Database | None" = None

    def __init__(self, db_path: str | Path | None = None):
        cfg_path = config.get("memory.sqlite_path", "data/companion.db")
        self.db_path = Path(db_path) if db_path else config.path(cfg_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        logger.debug(f"Database 初始化 path={self.db_path}")

    @classmethod
    def get_instance(cls) -> "Database":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reload(cls, db_path: str | Path | None = None) -> "Database":
        cls._instance = cls(db_path)
        return cls._instance

    # -------- 连接管理 --------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit，事务由我们手动管
            )
            self._conn.row_factory = sqlite3.Row
            # WAL 模式提升并发读
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            logger.info(f"SQLite 已连接: {self.db_path} (WAL)")
        return self._conn

    def init(self) -> None:
        """初始化 schema（幂等）。"""
        with self.transaction() as cur:
            for sql in _SCHEMA_SQL:
                cur.execute(sql)
            # 记录 schema 版本
            cur.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) "
                "VALUES('version', ?)",
                (str(SCHEMA_VERSION),),
            )
        logger.info(f"数据库 schema 已初始化 v{SCHEMA_VERSION}")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("SQLite 连接已关闭")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """事务上下文：成功 commit，异常 rollback。"""
        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN;")
            yield cur
            cur.execute("COMMIT;")
        except Exception:
            cur.execute("ROLLBACK;")
            logger.exception("数据库事务回滚")
            raise
        finally:
            cur.close()

    # ============================================================
    # 对话历史 CRUD
    # ============================================================
    def add_conversation(
        self,
        role: str,
        content: str,
        emotion: str | None = None,
        emotion_intensity: float | None = None,
        intimacy_score: float | None = None,
        ai_emotion: str | None = None,
        usage: dict | None = None,
        metadata: dict | None = None,
    ) -> int:
        """插入一条对话记录，返回 id。"""
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO conversations
                   (role, content, emotion, emotion_intensity,
                    intimacy_score, ai_emotion, usage_json, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    role, content, emotion, emotion_intensity,
                    intimacy_score, ai_emotion,
                    json.dumps(usage, ensure_ascii=False) if usage else None,
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                ),
            )
            return cur.lastrowid or 0

    def get_recent_conversations(self, limit: int = 20) -> list[ConversationRecord]:
        """按时间正序返回最近 N 条对话（id 升序）。"""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM conversations ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        cur.close()
        # 反转让时间正序
        rows = list(reversed(rows))
        return [self._row_to_record(r) for r in rows]

    def get_conversations_since(self, since_id: int, limit: int = 100) -> list[ConversationRecord]:
        """获取 id > since_id 的对话（增量同步用）。"""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM conversations WHERE id > ? ORDER BY id ASC LIMIT ?",
            (since_id, limit),
        )
        rows = cur.fetchall()
        cur.close()
        return [self._row_to_record(r) for r in rows]

    def count_conversations(self) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM conversations")
        n = cur.fetchone()[0]
        cur.close()
        return n

    def _row_to_record(self, row: sqlite3.Row) -> ConversationRecord:
        usage = None
        if row["usage_json"]:
            try:
                usage = json.loads(row["usage_json"])
            except json.JSONDecodeError:
                usage = None
        metadata = None
        if row["metadata_json"]:
            try:
                metadata = json.loads(row["metadata_json"])
            except json.JSONDecodeError:
                metadata = None
        return ConversationRecord(
            id=row["id"],
            timestamp=row["timestamp"],
            role=row["role"],
            content=row["content"],
            emotion=row["emotion"],
            emotion_intensity=row["emotion_intensity"],
            intimacy_score=row["intimacy_score"],
            ai_emotion=row["ai_emotion"],
            usage=usage or {},
            metadata=metadata or {},
        )

    # ============================================================
    # 长期记忆 CRUD（T2-05 扩展向量检索）
    # ============================================================
    def add_memory(
        self,
        type: str,
        content: str,
        importance: int = 3,
        vector_id: str | None = None,
        source_turn_id: int | None = None,
    ) -> int:
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO memories
                   (type, content, importance, vector_id, source_turn_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (type, content, importance, vector_id, source_turn_id),
            )
            return cur.lastrowid or 0

    def get_memory(self, memory_id: int) -> dict | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None

    def search_memories(
        self,
        keyword: str | None = None,
        type: str | None = None,
        min_importance: int = 1,
        limit: int = 10,
    ) -> list[dict]:
        """按关键词/类型检索记忆（简单 LIKE，向量检索 T2-05 实现）。"""
        sql = "SELECT * FROM memories WHERE importance >= ?"
        params: list[Any] = [min_importance]
        if keyword:
            sql += " AND content LIKE ?"
            params.append(f"%{keyword}%")
        if type:
            sql += " AND type = ?"
            params.append(type)
        sql += " ORDER BY importance DESC, last_recalled_at DESC NULLS LAST LIMIT ?"
        params.append(limit)
        cur = self.conn.cursor()
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    def mark_memory_recalled(self, memory_id: int) -> None:
        """记录记忆被召回（T2-05 用）。"""
        with self.transaction() as cur:
            cur.execute(
                """UPDATE memories
                   SET last_recalled_at = CURRENT_TIMESTAMP,
                       recall_count = recall_count + 1
                   WHERE id = ?""",
                (memory_id,),
            )

    # ============================================================
    # 亲密度状态（T2-03 用，T1-07 仅提供接口骨架）
    # ============================================================
    def get_intimacy_state(self) -> dict:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM intimacy_state WHERE id = 1")
        row = cur.fetchone()
        cur.close()
        if row is None:
            return {"score": 5.0, "stage": "陌生", "updated_at": None}
        return dict(row)

    def save_intimacy_state(self, score: float, stage: str, metadata: dict | None = None) -> None:
        with self.transaction() as cur:
            cur.execute(
                """INSERT OR REPLACE INTO intimacy_state
                   (id, score, stage, updated_at, metadata_json)
                   VALUES (1, ?, ?, CURRENT_TIMESTAMP, ?)""",
                (score, stage,
                 json.dumps(metadata, ensure_ascii=False) if metadata else None),
            )


# 全局单例
db = Database.get_instance()
