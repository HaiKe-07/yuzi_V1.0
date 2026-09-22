"""T4-04 网络资讯查询测试。

覆盖：
1. RSS 解析：item/entry、多源聚合、去重、限数
2. 新闻查询：无网络依赖（注入 mock fetcher）
3. 百科词条：mock JSON 提取简介；无简介兜底
4. handle()：新闻类/词条类/兜底分类
5. LLM schema / 数据类 brief 截断
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.web_info import BAIKE_URL, NewsItem, WebInfo, describe  # noqa: E402

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>今日科技新闻：AI 取得新进展</title><link>https://a/1</link>
<description>描述一</description><pubDate>Mon, 22 Sep 2026 08:00:00 GMT</pubDate></item>
<item><title>经济稳定复苏</title><link>https://a/2</link></item>
</channel></rss>""".encode("utf-8")

ATOM_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>体坛快讯：决赛落幕</title><link href="https://b/1"/>
<summary>摘要</summary><updated>2026-09-22T08:00:00Z</updated></entry>
</feed>""".encode("utf-8")


def make_client(payload: dict) -> WebInfo:
    """将各源/词条 URL 映射到 payload，构造离线 WebInfo。"""
    def fetcher(url: str) -> bytes:
        if "baike" in url:
            return payload.get("baike", b"")
        # 新浪(JSON) / 网易 / BBC 用 RSS_XML，联合早报用 ATOM_XML
        if "zaobao" in url:
            return payload.get("atom", ATOM_XML)
        return payload.get("rss", RSS_XML)
    return WebInfo(
        fetch=fetcher,
        sources={
            "新浪新闻": "http://x/sina",
            "网易新闻": "http://x/163",
            "BBC中文": "http://x/bbc",
            "联合早报": "http://x/zaobao",
        },
    )


# ---------- RSS 解析 ----------
def test_parse_rss_item():
    c = make_client({})
    headlines = c.get_source_headlines("新浪新闻")
    assert len(headlines) == 2  # (新浪 news/网易 news 相同源都返回 RSS_XML，但 get_source_headlines 只取单源)
    assert headlines[0].title == "今日科技新闻：AI 取得新进展"
    assert headlines[0].source == "新浪新闻"
    print("  [✓] RSS item 解析")


def test_parse_atom_entry():
    c = make_client({})
    items = c.get_source_headlines("联合早报")
    assert len(items) == 1
    assert items[0].title == "体坛快讯：决赛落幕"
    print("  [✓] Atom entry 解析")


def test_news_dedup_and_limit():
    c = make_client({})
    news = c.get_news(top=4)
    # 多源，但标题去重后应只保留唯一标题
    titles = [n.title for n in news]
    assert len(titles) == len(set(titles))
    assert len(news) <= 4
    print("  [✓] 多源聚合 + 去重 + 限数")


def test_news_brief_format():
    c = make_client({})
    brief = c.news_brief(top=3)
    assert "今日资讯" in brief
    assert "1." in brief
    print("  [✓] 新闻简要输出格式")


def test_news_fetch_failure_empty():
    def fetcher(url):
        raise RuntimeError("网络不可用")
    c = WebInfo(fetch=fetcher, sources={"华尔街日报": "http://x/wsj"})
    brief = c.news_brief()
    assert "暂时没抓到" in brief
    print("  [✓] 抓取失败 → 友好提示")


# ---------- 百科 ----------
def test_wiki_extract_abstract():
    c = make_client({"baike": '{"abstract":"人工智能是研究智能体的学科。"}'.encode()})
    info = c.wiki("人工智能")
    assert "人工智能是研究智能体" in info
    print("  [✓] 百科简介提取")


def test_wiki_no_result():
    c = make_client({"baike": b"{}"})
    assert c.wiki("不存在词") == ""
    print("  [✓] 百科空结果 → 空串")


# ---------- handle 分类 ----------
def test_handle_news_query():
    c = make_client({})
    out = c.handle("今天有什么新闻")
    assert "今日资讯" in out
    print("  [✓] handle 新闻类")


def test_handle_baike_query():
    c = make_client({"baike": '{"abstract":"人工智能是研究智能体。"}'.encode()})
    out = c.handle("什么是人工智能")
    assert "人工智能：" in out
    assert "研究智能体" in out
    print("  [✓] handle 词条类")


def test_handle_baike_not_found():
    c = make_client({"baike": b"{}"})
    out = c.handle("介绍一下量子比特XYZ")
    assert "查不到" in out
    print("  [✓] handle 词条未命中兜底")


# ---------- schema / 数据类 ----------
def test_newsitem_brief_truncate():
    it = NewsItem(title="A" * 100)
    assert len(it.brief(60)) <= 60
    assert it.brief(60).endswith("…")
    print("  [✓] NewsItem.brief 截断")


def test_describe_schema():
    d = describe()
    assert d["name"] == "web_info"
    assert d["parameters"]["required"] == ["query"]
    print("  [✓] LLM 工具 schema")


def test_baike_url_template():
    assert "{kw}" in BAIKE_URL  # 抓取模板含关键词占位
    print("  [✓] 百科 URL 模板")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    import traceback

    tests = [
        test_parse_rss_item,
        test_parse_atom_entry,
        test_news_dedup_and_limit,
        test_news_brief_format,
        test_news_fetch_failure_empty,
        test_wiki_extract_abstract,
        test_wiki_no_result,
        test_handle_news_query,
        test_handle_baike_query,
        test_handle_baike_not_found,
        test_newsitem_brief_truncate,
        test_describe_schema,
        test_baike_url_template,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  [✗] {t.__name__} 失败: {e}")
            traceback.print_exc()

    print(f"\n结果: {passed} 通过, {failed} 失败 / 共 {len(tests)}")