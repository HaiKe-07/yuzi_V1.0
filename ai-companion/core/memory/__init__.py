"""core.memory 包：对话历史 + 长期记忆 + 亲密度状态存储。"""
from .db import ConversationRecord, Database, db

__all__ = ["Database", "ConversationRecord", "db"]
