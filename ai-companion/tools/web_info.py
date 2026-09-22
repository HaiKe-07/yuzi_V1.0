"""工具：网络资讯查询（T4-04）。

聊天中用户问到实时信息（新闻、百科等），AI 能查并回应。
验收：用户问“今天有什么新闻”，AI 能给出简要资讯。

实现要点（纯标准库，免第三方依赖）：
1. RSS 聚合：内置多个新闻资讯源，解析标题/链接/时间，给最近条目
2. 资讯查询：get_news() 拉取各源并返回整理后的头条
3. 百科/词条查询：按关键词抓取简体中文词条简介（可注入 fetcher，离线可测）
4. handle()：根据问题分类（新闻类 → 头条；词条类 → 简介）
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from xml.etree import ElementTree

# 内置新闻 RSS 源（name → url）。联网抓取复用 urllib；抓取函数可注入以便离线测试。
DEFAULT_SOURCES: dict[str, str] = {
    "新浪新闻": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num=10",
    "网易新闻": "https://news.163.com/special/00011K6L/rss_newstop.xml",
    "BBC中文": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
    "联合早报": "https://www.zaobao.com.sg/xml/rss_zh_cn_newsall.xml",
}
# 词条查询（百科）候选源
BAIKE_URL = "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103&format=json&appid=379020&bk_key={kw}"


@dataclass
class NewsItem:
    title: str
    link: str = ""
    desc: str = ""
    pub: str = ""
    source: str = ""

    def brief(self, limit: int = 60) -> str:
        t = self.title.strip()
        if len(t) > limit:
            t = t[: limit - 1] + "…"
        return t


class WebInfo:
    """网络资讯查询服务。

    Args:
        fetch: 抓取函数 fetch(url) -> bytes，注入便于离线测试。
        sources: RSS 源映射，None 用内置默认源。
    """

    def __init__(
        self,
        fetch: Optional[Callable[[str], bytes]] = None,
        sources: Optional[dict[str, str]] = None,
    ):
        self._fetch = fetch or self._default_fetch
        self.sources = sources or DEFAULT_SOURCES

    # ---------- 抓取 ----------
    def _default_fetch(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.read()

    # ---------- RSS 解析 ----------
    @staticmethod
    def _parse_rss(data: bytes, source: str) -> List[NewsItem]:
        """解析 RSS/Atom XML，返回条目列表。失败返回空。"""
        items: List[NewsItem] = []
        try:
            root = ElementTree.fromstring(data)
        except Exception:
            return items

        def _text(el) -> str:
            return (el.text or "").strip() if el is not None else ""

        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1].lower()
            if tag not in ("item", "entry"):
                continue
            def _child(name):
                for c in node:
                    if c.tag.rsplit("}", 1)[-1].lower() == name:
                        return _text(c) or getattr(c, "text", "") or ""
                return ""
            title = _child("title")
            link = _child("link")
            desc = _child("description") or _child("summary") or _child("content")
            pub = _child("pubdate") or _child("published") or _child("updated")
            items.append(NewsItem(title, link, desc, pub, source))
            if len(items) >= 20:  # 单源最多 20 条
                break
        return items

    # ---------- 资讯 ----------
    def get_source_headlines(self, name: str) -> List[NewsItem]:
        url = self.sources.get(name)
        if not url:
            return []
        try:
            data = self._fetch(url)
        except Exception:
            return []
        return self._parse_rss(data, name)

    def get_news(self, top: int = 12) -> List[NewsItem]:
        """聚合全部可用源，取最近的 top 条（去重）。"""
        seen = set()
        merged: List[NewsItem] = []
        for name in self.sources:
            for it in self.get_source_headlines(name):
                key = it.title.strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(it)
        return merged[:top]

    def news_brief(self, top: int = 8) -> str:
        items = self.get_news(top)
        if not items:
            return "暂时没抓到今日新闻（可能网络不可用）。"
        lines = [f"{i + 1}. {it.brief()}" for i, it in enumerate(items)]
        return "今日资讯：\n" + "\n".join(lines)

    # ---------- 词条/百科 ----------
    def wiki(self, keyword: str) -> str:
        """抓取词条简介。失败返回空串。"""
        kw = urllib.parse.quote(keyword.strip())
        try:
            data = self._fetch(BAIKE_URL.format(kw=kw))
        except Exception:
            return ""
        try:
            import json
            j = json.loads(data.decode("utf-8", "ignore"))
        except Exception:
            return ""
        # 简介字段（"abstract"）可能为列表或字符串，防御性提取
        for k in ("abstract", "summary", "intro", "lemmaIntroduction"):
            if isinstance(j.get(k), str) and j[k]:
                return j[k].strip()
        if isinstance(j.get("abstract"), list) and j["abstract"]:
            return "\n".join(str(x) for x in j["abstract"][:3])
        return ""

    # ---------- 统一入口 ----------
    def handle(self, question: str) -> str:
        """根据问题类型分类处理：新闻 → 头条；词条 → 简介百科。"""
        q = question.strip()
        news_kw = ("新闻", "资讯", "头条", "大事", "热点", "最新消息", "有什么新闻", "今天怎么了")
        if any(kw in q for kw in news_kw):
            return self.news_brief()
        # 否则按词条处理：提取“什么是X / X是什么 / 介绍下X”
        m = re.search(r"(?:什么是|是什么|介绍一下?|介绍|了解(?:一下)?)\s*([^\s？?。]+)", q)
        if m:
            kw = m.group(1)
            info = self.wiki(kw)
            if info:
                return f"{kw}：{info}"
            return f"关于「{kw}」暂时查不到简介。"
        # 兜底：当作新闻聚合
        return self.news_brief()


def describe() -> dict:
    return {
        "name": "web_info",
        "description": "查询实时资讯/新闻或词条简介。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用户问题，如 今天有什么新闻 / 什么是人工智能"},
            },
            "required": ["query"],
        },
    }