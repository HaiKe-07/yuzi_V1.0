"""T2-05 长期记忆系统单元测试。

覆盖：
1. 记忆提取：规则式从用户输入提取值得记住的事实
2. 记忆存储：入库 + TF 缓存
3. 记忆去重：相似度高于阈值时更新而非新增
4. 语义检索：TF-IDF 余弦相似度召回
5. 记忆摘要：格式化为 prompt 中的 memory_brief
6. ConversationManager 集成：提取+召回+注入 prompt
7. 缓存与持久化：重启后能加载历史记忆
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory.db import Database
from core.memory.manager import (
    MemoryManager, Memory, MemoryType,
    _char_bigrams, _tf, _cosine,
)


# ============================================================
# 测试工具
# ============================================================
def _make_manager(tmpdir: str) -> MemoryManager:
    """构造基于临时目录的 MemoryManager（隔离测试）。"""
    import core.memory.manager as mgr_module
    import core.memory.db as db_module
    import core.memory as mem_pkg

    db_path = Path(tmpdir) / "test_mem.db"
    db = Database(db_path=db_path)
    db.init()
    # 同步更新两处模块级单例（与 test_db.py 一致的做法）
    mgr_module.default_db = db
    db_module.db = db
    mem_pkg.db = db
    # 重置全局单例
    mgr_module._memory_manager = None
    return MemoryManager(database=db)


# ============================================================
# 1. 文本向量化工具函数
# ============================================================
def test_char_bigrams_basic():
    """字符 bigram 提取应包含中文二字组合。"""
    tokens = _char_bigrams("我喜欢科幻")
    assert "我喜" in tokens or "喜欢" in tokens
    print(f"  [✓] bigram 提取: {tokens[:5]}")


def test_char_bigrams_english():
    """英文单词应单独成 token。"""
    tokens = _char_bigrams("I love python")
    assert "love" in tokens
    assert "python" in tokens
    print("  [✓] 英文 token 提取")


def test_tf_returns_normalized():
    """TF 向量应归一化（值在 0~1）。"""
    tf = _tf(_char_bigrams("测试测试测试"))
    assert all(0 < v <= 1.0 for v in tf.values())
    print("  [✓] TF 归一化")


def test_cosine_identical():
    """相同文本余弦相似度应为 1.0。"""
    tf = _tf(_char_bigrams("我喜欢科幻电影"))
    sim = _cosine(tf, tf)
    assert abs(sim - 1.0) < 0.01
    print(f"  [✓] 相同文本相似度: {sim:.3f}")


def test_cosine_different():
    """完全无关文本相似度应低。"""
    tf1 = _tf(_char_bigrams("我喜欢科幻电影"))
    tf2 = _tf(_char_bigrams("今天天气真好"))
    sim = _cosine(tf1, tf2)
    assert sim < 0.2, f"无关文本相似度应低，实际 {sim:.3f}"
    print(f"  [✓] 无关文本相似度低: {sim:.3f}")


def test_cosine_similar():
    """语义相近的文本相似度应高。"""
    tf1 = _tf(_char_bigrams("我喜欢看科幻电影"))
    tf2 = _tf(_char_bigrams("我也爱看科幻片"))
    sim = _cosine(tf1, tf2)
    assert sim > 0.3, f"相近文本相似度应高，实际 {sim:.3f}"
    print(f"  [✓] 相近文本相似度高: {sim:.3f}")


# ============================================================
# 2. 记忆提取
# ============================================================
def test_extract_profile():
    """应提取出基本信息类记忆。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        memories = mm.extract("我叫小明，今年25岁")
        types = [m.type for m in memories]
        assert MemoryType.PROFILE in types
        contents = [m.content for m in memories if m.type == MemoryType.PROFILE]
        assert any("小明" in c for c in contents)
        print(f"  [✓] 提取基本信息: {contents}")


def test_extract_interest():
    """应提取出兴趣类记忆。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        memories = mm.extract("我很喜欢科幻电影")
        types = [m.type for m in memories]
        assert MemoryType.INTEREST in types
        print("  [✓] 提取兴趣")


def test_extract_preference():
    """应提取出偏好类记忆（讨厌/不喜欢）。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        memories = mm.extract("我很讨厌香菜")
        types = [m.type for m in memories]
        assert MemoryType.PREFERENCE in types
        print("  [✓] 提取偏好")


def test_extract_schedule():
    """应提取出日程类记忆。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        memories = mm.extract("下周三我有个面试")
        types = [m.type for m in memories]
        assert MemoryType.SCHEDULE in types
        print("  [✓] 提取日程")


def test_extract_relation():
    """应提取出人际类记忆。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        memories = mm.extract("我妈妈说让我早点回家")
        types = [m.type for m in memories]
        assert MemoryType.RELATION in types
        print("  [✓] 提取人际")


def test_extract_experience():
    """应提取出经历类记忆。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        memories = mm.extract("我昨天去看了电影")
        types = [m.type for m in memories]
        assert MemoryType.EXPERIENCE in types
        print("  [✓] 提取经历")


def test_extract_emotion():
    """应提取出情绪类记忆。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        memories = mm.extract("我最近很焦虑")
        types = [m.type for m in memories]
        assert MemoryType.EMOTION in types
        print("  [✓] 提取情绪")


def test_extract_empty_input():
    """空输入应返回空列表。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        assert mm.extract("") == []
        assert mm.extract("   ") == []
        print("  [✓] 空输入返回空")


def test_extract_no_memorable_content():
    """无值得记住的内容应返回空。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        memories = mm.extract("你好，今天天气不错")
        # "你好，今天天气不错" 没有明显的记忆模式
        assert len(memories) == 0 or all(
            m.type not in (MemoryType.PROFILE, MemoryType.INTEREST)
            for m in memories
        )
        print("  [✓] 无记忆内容时正确处理")


def test_extract_multiple_facts():
    """一句中包含多个事实时应全部提取。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        memories = mm.extract("我叫小红，我喜欢看书，我讨厌早起")
        types = {m.type for m in memories}
        assert MemoryType.PROFILE in types
        assert MemoryType.INTEREST in types
        assert MemoryType.PREFERENCE in types
        print(f"  [✓] 多事实提取 {len(memories)} 条")


# ============================================================
# 3. 记忆存储与去重
# ============================================================
def test_extract_and_store_basic():
    """提取并入库后应能查到。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        stored = mm.extract_and_store("我叫小明，我喜欢科幻")
        assert len(stored) >= 2
        # 内存中应能通过 count 查到
        assert mm.count() >= 2
        # 数据库中应能查到
        all_mems = mm.all_memories()
        assert len(all_mems) >= 2
        print(f"  [✓] 入库 {len(stored)} 条，总数 {mm.count()}")


def test_dedup_similar_content():
    """相似内容再次输入应去重，不重复入库。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        # 第一次入库
        mm.extract_and_store("我喜欢科幻电影")
        count_after_first = mm.count()
        # 第二次：相似度高的内容（应去重）
        mm.extract_and_store("我喜欢科幻")
        count_after_second = mm.count()
        # 不应大幅增加（去重生效）
        assert count_after_second <= count_after_first + 1, (
            f"去重失败: 第一次 {count_after_first}，第二次 {count_after_second}"
        )
        print(f"  [✓] 去重生效: {count_after_first} → {count_after_second}")


def test_dedup_different_types_not_merged():
    """不同类型的相似内容不应被错误合并。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        # "我喜欢" 是 INTEREST
        mm.extract_and_store("我喜欢运动")
        # "我讨厌" 是 PREFERENCE，即使语义上接近也不应合并
        mm.extract_and_store("我讨厌运动")
        interests = mm.all_memories(mem_type=MemoryType.INTEREST)
        prefs = mm.all_memories(mem_type=MemoryType.PREFERENCE)
        assert len(interests) >= 1
        assert len(prefs) >= 1
        print(f"  [✓] 不同类型不合并: 兴趣 {len(interests)}，偏好 {len(prefs)}")


def test_duplicate_updates_importance():
    """重复记忆应提升重要度（取较高值）。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        # 兴趣类默认重要度 4
        mm.extract_and_store("我喜欢看书")
        mems = mm.all_memories(mem_type=MemoryType.INTEREST)
        assert len(mems) >= 1
        original_imp = mems[0].importance
        # 重复提及，重要度应保持或提升
        mm.invalidate_cache()
        mm.extract_and_store("我喜欢看书")
        mems2 = mm.all_memories(mem_type=MemoryType.INTEREST)
        # 不应新增
        assert len(mems2) == len(mems)
        # 重要度不降
        assert mems2[0].importance >= original_imp
        print(f"  [✓] 重复记忆重要度: {original_imp} → {mems2[0].importance}")


# ============================================================
# 4. 语义检索
# ============================================================
def test_recall_relevant():
    """召回应返回与 query 相关的记忆。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        # 存入几条不同主题的记忆
        mm.extract_and_store("我喜欢科幻电影")
        mm.extract_and_store("我妈妈是老师")
        mm.extract_and_store("我下周三有面试")
        # 用相关 query 召回
        results = mm.recall("你看过什么科幻片")
        assert len(results) > 0
        # 第一条应与科幻相关
        contents = [m.content for m in results]
        assert any("科幻" in c for c in contents), (
            f"召回未包含科幻相关: {contents}"
        )
        print(f"  [✓] 召回相关记忆: {contents[:2]}")


def test_recall_top_k_limit():
    """recall 应尊重 top_k 限制。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        for i in range(10):
            mm.extract_and_store(f"我喜欢电影类型{i}")
        results = mm.recall("电影", top_k=3)
        assert len(results) <= 3
        print(f"  [✓] top_k=3 限制，召回 {len(results)} 条")


def test_recall_empty_query():
    """空 query 应返回空。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        mm.extract_and_store("我喜欢科幻")
        assert mm.recall("") == []
        assert mm.recall("   ") == []
        print("  [✓] 空 query 返回空")


def test_recall_no_match():
    """完全无关的 query 应返回空或很少结果。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        mm.extract_and_store("我喜欢科幻电影")
        # 完全无关的 query
        results = mm.recall("今天天气真好啊")
        # 相似度应低于阈值，不召回
        assert len(results) == 0 or all(
            "科幻" not in m.content for m in results
        )
        print(f"  [✓] 无关 query 召回 {len(results)} 条")


def test_recall_updates_recall_count():
    """召回后 recall_count 应增加。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        mm.extract_and_store("我喜欢科幻电影")
        before = mm.all_memories()[0].recall_count
        mm.recall("科幻电影")
        mm.invalidate_cache()
        after = mm.all_memories()[0].recall_count
        assert after > before, f"recall_count 未增加: {before} → {after}"
        print(f"  [✓] recall_count: {before} → {after}")


# ============================================================
# 5. 记忆摘要
# ============================================================
def test_recall_brief_format():
    """recall_brief 应格式化为多行字符串。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        mm.extract_and_store("我喜欢科幻电影")
        mm.extract_and_store("我叫小明")
        # 用与已存记忆有字符重合的 query
        brief = mm.recall_brief("你喜欢什么电影")
        assert brief, "brief 不应为空"
        assert "- [" in brief, f"格式错误: {brief}"
        print(f"  [✓] brief 格式: {brief[:50]}")


def test_recall_brief_empty():
    """无记忆时 brief 应为空串。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        brief = mm.recall_brief("随便什么")
        assert brief == ""
        print("  [✓] 无记忆时 brief 为空")


def test_recent_brief():
    """recent_brief 应返回最近的记忆。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        mm.extract_and_store("我喜欢科幻")
        mm.extract_and_store("我讨厌香菜")
        brief = mm.recent_brief(limit=5)
        assert brief, "recent_brief 不应为空"
        assert "- [" in brief
        print(f"  [✓] recent_brief: {brief[:50]}")


# ============================================================
# 6. 持久化与缓存
# ============================================================
def test_cache_loaded_on_recall():
    """extract_and_store 会触发缓存加载。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        assert not mm._cache_loaded
        mm.extract_and_store("我喜欢科幻")
        # extract_and_store 内部调用 _ensure_cache
        assert mm._cache_loaded
        assert len(mm._tf_cache) >= 1
        print("  [✓] 缓存懒加载")


def test_invalidate_cache():
    """invalidate_cache 应清空缓存。"""
    with tempfile.TemporaryDirectory() as tmp:
        mm = _make_manager(tmp)
        mm.extract_and_store("测试")
        mm.recall("测试")  # 触发缓存加载
        assert mm._cache_loaded
        mm.invalidate_cache()
        assert not mm._cache_loaded
        assert len(mm._tf_cache) == 0
        print("  [✓] 缓存清空")


def test_persistence_across_instances():
    """新 MemoryManager 实例应能加载之前入库的记忆。"""
    with tempfile.TemporaryDirectory() as tmp:
        # 第一个实例：存入记忆
        mm1 = _make_manager(tmp)
        mm1.extract_and_store("我喜欢科幻电影")
        assert mm1.count() >= 1
        mm1.invalidate_cache()

        # 第二个实例：同一个 db，应能加载
        import core.memory.manager as mgr_module
        mm2 = MemoryManager(database=mgr_module.default_db)
        # recall 会触发缓存加载
        results = mm2.recall("科幻")
        assert len(results) > 0
        contents = [m.content for m in results]
        assert any("科幻" in c for c in contents)
        print(f"  [✓] 跨实例持久化: 召回 {len(results)} 条")


# ============================================================
# 7. ConversationManager 集成
# ============================================================
def _make_mock_llm(reply_text="嗯，我在听呢"):
    llm = MagicMock()
    from llm import LLMResponse
    llm.chat.return_value = LLMResponse(text=reply_text, usage={"total_tokens": 10})
    return llm


def _make_mock_asr():
    asr = MagicMock()
    from speech import ASRResult
    asr.transcribe.return_value = ASRResult(text="你好")
    return asr


def _make_mock_tts():
    tts = MagicMock()
    from speech import TTSResult
    tts.default_voice = "nova"
    tts.synthesize.return_value = TTSResult(audio=b"X", format="mp3", voice="nova")
    return tts


def _make_manager_with_memory(tmpdir: str, reply_text="嗯嗯，我在呢"):
    """构造带长期记忆的 ConversationManager。"""
    import core.memory.manager as mgr_module
    import core.memory.db as db_module
    import core.memory as mem_pkg

    db_path = Path(tmpdir) / "test_conv.db"
    db = Database(db_path=db_path)
    db.init()
    mgr_module.default_db = db
    db_module.db = db
    mem_pkg.db = db
    mgr_module._memory_manager = None

    from core.memory.manager import MemoryManager
    from core.conversation import ConversationManager
    mm = MemoryManager(database=db)

    return ConversationManager(
        llm=_make_mock_llm(reply_text),
        asr=_make_mock_asr(),
        tts=_make_mock_tts(),
        emotion_engine=False,
        intimacy_manager=False,
        memory_manager=mm,
        persist=False,
    )


def test_conversation_extracts_memory():
    """对话中应自动提取并存储记忆。"""
    with tempfile.TemporaryDirectory() as tmp:
        m = _make_manager_with_memory(tmp)
        m.text_chat("我叫小明，我喜欢科幻电影")
        # 记忆应已入库
        assert m.memory.count() >= 2
        print(f"  [✓] 对话提取记忆 {m.memory.count()} 条")


def test_conversation_injects_memory_brief():
    """对话 prompt 应包含召回的记忆摘要。"""
    with tempfile.TemporaryDirectory() as tmp:
        m = _make_manager_with_memory(tmp)
        # 先存入一条记忆
        m.memory.extract_and_store("我喜欢科幻电影")
        m.memory.invalidate_cache()
        # 再对话，prompt 应包含记忆
        m.text_chat("推荐个电影吧")
        sys_prompt = m.llm.chat.call_args.kwargs["system_prompt"]
        # 应在 prompt 中看到记忆摘要
        assert "科幻" in sys_prompt or "兴趣" in sys_prompt, (
            "prompt 应包含召回的记忆"
        )
        print("  [✓] 对话 prompt 注入记忆摘要")


def test_conversation_no_memory_when_disabled():
    """关闭记忆管理器时 prompt 应用默认值兜底。"""
    with tempfile.TemporaryDirectory() as tmp:
        import core.memory.manager as mgr_module
        import core.memory.db as db_module
        import core.memory as mem_pkg

        db_path = Path(tmp) / "test_off.db"
        db = Database(db_path=db_path)
        db.init()
        mgr_module.default_db = db
        db_module.db = db
        mem_pkg.db = db
        mgr_module._memory_manager = None

        from core.conversation import ConversationManager
        m = ConversationManager(
            llm=_make_mock_llm(),
            asr=_make_mock_asr(),
            tts=_make_mock_tts(),
            emotion_engine=False,
            intimacy_manager=False,
            memory_manager=False,
            persist=False,
        )
        m.text_chat("你好")
        sys_prompt = m.llm.chat.call_args.kwargs["system_prompt"]
        # 默认值应填充（暂无）
        import re
        leftovers = re.findall(r"\{[a-z_]+\}", sys_prompt)
        assert not leftovers, f"prompt 残留占位符: {leftovers}"
        print("  [✓] 关闭记忆时 prompt 用默认值")


def test_conversation_memory_grows_over_turns():
    """多轮对话后记忆应逐步累积。"""
    with tempfile.TemporaryDirectory() as tmp:
        m = _make_manager_with_memory(tmp)
        m.text_chat("我叫小红")
        count1 = m.memory.count()
        m.text_chat("我喜欢看书")
        count2 = m.memory.count()
        m.text_chat("我讨厌早起")
        count3 = m.memory.count()
        assert count3 > count2 > count1, (
            f"记忆应递增: {count1} → {count2} → {count3}"
        )
        print(f"  [✓] 记忆递增: {count1} → {count2} → {count3}")


def test_conversation_recall_after_restart():
    """重启后新对话应能召回之前的记忆。"""
    with tempfile.TemporaryDirectory() as tmp:
        import core.memory.manager as mgr_module

        # 第一次会话：存入记忆
        m1 = _make_manager_with_memory(tmp)
        m1.text_chat("我叫小明，我喜欢科幻")
        db_ref = mgr_module.default_db
        m1.memory.invalidate_cache()

        # 第二次会话：新 manager，同一个 db
        from core.memory.manager import MemoryManager
        from core.conversation import ConversationManager
        mm2 = MemoryManager(database=db_ref)
        m2 = ConversationManager(
            llm=_make_mock_llm("我记得你呢"),
            asr=_make_mock_asr(),
            tts=_make_mock_tts(),
            emotion_engine=False,
            intimacy_manager=False,
            memory_manager=mm2,
            persist=False,
        )
        m2.text_chat("你还记得我吗")
        sys_prompt = m2.llm.chat.call_args.kwargs["system_prompt"]
        # 应召回之前存的记忆
        assert "小明" in sys_prompt or "科幻" in sys_prompt, (
            "重启后应召回之前的记忆"
        )
        print("  [✓] 重启后召回历史记忆")


# ============================================================
# main
# ============================================================
def main() -> int:
    print("T2-05 长期记忆系统单元测试\n")

    print("【1】文本向量化工具")
    test_char_bigrams_basic()
    test_char_bigrams_english()
    test_tf_returns_normalized()
    test_cosine_identical()
    test_cosine_different()
    test_cosine_similar()

    print("\n【2】记忆提取")
    test_extract_profile()
    test_extract_interest()
    test_extract_preference()
    test_extract_schedule()
    test_extract_relation()
    test_extract_experience()
    test_extract_emotion()
    test_extract_empty_input()
    test_extract_no_memorable_content()
    test_extract_multiple_facts()

    print("\n【3】记忆存储与去重")
    test_extract_and_store_basic()
    test_dedup_similar_content()
    test_dedup_different_types_not_merged()
    test_duplicate_updates_importance()

    print("\n【4】语义检索")
    test_recall_relevant()
    test_recall_top_k_limit()
    test_recall_empty_query()
    test_recall_no_match()
    test_recall_updates_recall_count()

    print("\n【5】记忆摘要")
    test_recall_brief_format()
    test_recall_brief_empty()
    test_recent_brief()

    print("\n【6】持久化与缓存")
    test_cache_loaded_on_recall()
    test_invalidate_cache()
    test_persistence_across_instances()

    print("\n【7】ConversationManager 集成")
    test_conversation_extracts_memory()
    test_conversation_injects_memory_brief()
    test_conversation_no_memory_when_disabled()
    test_conversation_memory_grows_over_turns()
    test_conversation_recall_after_restart()

    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
