"""core.memory 包：对话历史 + 长期记忆 + 亲密度状态存储。"""
from .db import ConversationRecord, Database, db
from .manager import MemoryManager, Memory, MemoryType, get_memory_manager

__all__ = [
    "Database", "ConversationRecord", "db",
    "MemoryManager", "Memory", "MemoryType", "get_memory_manager",
]
