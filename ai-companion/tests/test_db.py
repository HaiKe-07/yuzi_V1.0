"""T1-07 数据库存储单元测试。

重点验证"重启后能读取历史"：
1. 用临时 db 文件，写入若干对话
2. 关闭连接（模拟重启）
3. 重新打开，能读到之前写入的记录
4. 长期记忆 CRUD
5. 亲密度状态读写
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory.db import ConversationRecord, Database


def make_db(tmpdir: str) -> Database:
    """用一个临时路径的 Database 实例。"""
    db_path = Path(tmpdir) / "test.db"
    return Database(db_path=db_path)


def test_init_creates_tables():
    with tempfile.TemporaryDirectory() as tmp:
        d = make_db(tmp)
        d.init()
        cur = d.conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        cur.close()
        assert {"conversations", "memories", "intimacy_state", "schema_meta"} <= tables
        print("  [✓] init() 创建全部表")


def test_restart_reads_history():
    """核心验收：重启后能读取历史。"""
    with tempfile.TemporaryDirectory() as tmp:
        # 第一次启动：写入对话
        d1 = make_db(tmp)
        d1.init()
        d1.add_conversation("user", "你好")
        d1.add_conversation("assistant", "在的，我在听呢")
        d1.add_conversation("user", "今天累不累？")
        assert d1.count_conversations() == 3

        # 关闭连接（模拟重启）
        d1.close()

        # 重新启动：应当能读到全部 3 条
        d2 = make_db(tmp)  # 同一路径
        d2.init()  # 幂等
        records = d2.get_recent_conversations(limit=20)

        assert len(records) == 3, f"重启后应读到 3 条，实际 {len(records)}"
        assert records[0].role == "user"
        assert records[0].content == "你好"
        assert records[1].role == "assistant"
        assert records[1].content == "在的，我在听呢"
        assert records[2].role == "user"
        assert records[2].content == "今天累不累？"
        print("  [✓] 重启后能读取历史（核心验收点）")


def test_persist_with_metadata():
    """带元数据的对话记录能正确序列化/反序列化。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = make_db(tmp)
        d.init()
        d.add_conversation(
            "assistant", "嗯，我懂你的感受",
            emotion="共情",
            emotion_intensity=4.0,
            intimacy_score=35.5,
            ai_emotion="温柔",
            usage={"total_tokens": 88},
            metadata={"finish_reason": "stop"},
        )
        records = d.get_recent_conversations()
        r = records[0]
        assert r.emotion == "共情"
        assert r.emotion_intensity == 4.0
        assert r.intimacy_score == 35.5
        assert r.ai_emotion == "温柔"
        assert r.usage == {"total_tokens": 88}
        assert r.metadata == {"finish_reason": "stop"}
        print("  [✓] 元数据 JSON 序列化/反序列化正确")


def test_get_recent_limit():
    with tempfile.TemporaryDirectory() as tmp:
        d = make_db(tmp)
        d.init()
        for i in range(5):
            d.add_conversation("user", f"msg{i}")
        records = d.get_recent_conversations(limit=3)
        # 返回最近 3 条，时间正序
        assert len(records) == 3
        assert records[0].content == "msg2"
        assert records[-1].content == "msg4"
        print("  [✓] get_recent_conversations limit 截断 + 时间正序")


def test_get_since_incremental():
    """增量同步：id > since_id 的记录。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = make_db(tmp)
        d.init()
        id1 = d.add_conversation("user", "a")
        id2 = d.add_conversation("assistant", "b")
        id3 = d.add_conversation("user", "c")

        new_records = d.get_conversations_since(since_id=id1, limit=100)
        assert len(new_records) == 2
        assert new_records[0].id == id2
        assert new_records[1].id == id3
        print("  [✓] get_conversations_since 增量查询")


def test_role_check_constraint():
    """role 字段 CHECK 约束应拒绝非法值。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = make_db(tmp)
        d.init()
        try:
            d.add_conversation("invalid_role", "x")
        except Exception:
            print("  [✓] CHECK 约束拒绝非法 role")
            return
        raise AssertionError("应拒绝非法 role")


def test_memory_crud():
    with tempfile.TemporaryDirectory() as tmp:
        d = make_db(tmp)
        d.init()
        mid = d.add_memory(
            type="兴趣",
            content="用户喜欢科幻电影",
            importance=4,
        )
        m = d.get_memory(mid)
        assert m["type"] == "兴趣"
        assert m["content"] == "用户喜欢科幻电影"
        assert m["importance"] == 4
        assert m["recall_count"] == 0

        d.mark_memory_recalled(mid)
        m2 = d.get_memory(mid)
        assert m2["recall_count"] == 1
        assert m2["last_recalled_at"] is not None
        print("  [✓] 长期记忆 CRUD + recall_count 计数")


def test_memory_search():
    with tempfile.TemporaryDirectory() as tmp:
        d = make_db(tmp)
        d.init()
        d.add_memory("兴趣", "喜欢科幻电影", importance=4)
        d.add_memory("兴趣", "喜欢听后摇音乐", importance=3)
        d.add_memory("日程", "下周三面试", importance=5)
        d.add_memory("偏好", "讨厌香菜", importance=2)

        # 关键词检索
        results = d.search_memories(keyword="喜欢")
        assert len(results) == 2
        # 类型过滤
        sched = d.search_memories(type="日程")
        assert len(sched) == 1
        assert sched[0]["content"] == "下周三面试"
        # 重要度过滤
        hi = d.search_memories(min_importance=4)
        assert len(hi) == 2
        print("  [✓] 长期记忆关键词/类型/重要度检索")


def test_intimacy_state():
    with tempfile.TemporaryDirectory() as tmp:
        d = make_db(tmp)
        d.init()

        # 初始默认
        s = d.get_intimacy_state()
        assert s["score"] == 5.0
        assert s["stage"] == "陌生"

        # 写入
        d.save_intimacy_state(score=45.5, stage="亲近",
                              metadata={"address_hint": "用昵称"})
        s2 = d.get_intimacy_state()
        assert s2["score"] == 45.5
        assert s2["stage"] == "亲近"
        assert s2["updated_at"] is not None
        print("  [✓] 亲密度状态读写（T2-03 接口骨架）")


def test_init_idempotent():
    """多次 init() 不应报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = make_db(tmp)
        d.init()
        d.init()
        d.init()
        assert d.count_conversations() == 0
        print("  [✓] init() 幂等")


def main() -> int:
    print("T1-07 数据库存储单元测试")
    test_init_creates_tables()
    test_restart_reads_history()
    test_persist_with_metadata()
    test_get_recent_limit()
    test_get_since_incremental()
    test_role_check_constraint()
    test_memory_crud()
    test_memory_search()
    test_intimacy_state()
    test_init_idempotent()
    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
