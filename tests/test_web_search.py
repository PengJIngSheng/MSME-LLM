"""
Basic unit tests for Model Networking/web_search.py.
Run with:  python -m pytest tests/test_web_search.py -v
"""

import sys
import os
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Model Networking"))

from web_search import (
    detect_language,
    needs_web_search,
    chunk_text,
    _clean_md as _clean_markdown,
    _html_to_text,
    _score,
    _cache_row_is_relevant,
    _search_result_is_relevant,
    SearchResult,
    WebResearcher,
    ResearchResult,
)


# ── detect_language ──────────────────────────────────────────────────────────

class TestDetectLanguage(unittest.TestCase):
    def test_chinese(self):
        self.assertEqual(detect_language("马来西亚最新AI政策有什么更新？"), "Chinese")

    def test_malay(self):
        self.assertEqual(detect_language("apa dasar terbaru untuk syarikat di Malaysia?"), "Malay")

    def test_english_default(self):
        self.assertEqual(detect_language("What is the latest AI policy in Malaysia?"), "English")

    def test_short_english(self):
        self.assertEqual(detect_language("hello"), "English")


# ── needs_web_search ─────────────────────────────────────────────────────────

class TestNeedsWebSearch(unittest.TestCase):
    def test_latest_news(self):
        self.assertTrue(needs_web_search("马来西亚最新 AI 政策有什么更新？"))

    def test_price_query(self):
        self.assertTrue(needs_web_search("What is the price of petrol in Malaysia today?"))

    def test_policy_malay(self):
        self.assertTrue(needs_web_search("dasar terbaru SSM untuk syarikat baru 2025?"))

    def test_greeting_no_search(self):
        self.assertFalse(needs_web_search("hello"))

    def test_coding_no_search(self):
        self.assertFalse(needs_web_search("write a Python function to sort a list"))

    def test_short_no_search(self):
        self.assertFalse(needs_web_search("hi"))

    def test_year_triggers(self):
        self.assertTrue(needs_web_search("budget 2025 Malaysia announcement"))


# ── chunk_text ───────────────────────────────────────────────────────────────

class TestChunkText(unittest.TestCase):
    def test_short_returns_single(self):
        text = "This is a short paragraph."
        chunks = chunk_text(text, chunk_size=600)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], text)

    def test_empty_returns_empty(self):
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   "), [])

    def test_long_text_splits(self):
        text = ("This is a sentence about AI policy in Malaysia. " * 20).strip()
        chunks = chunk_text(text, chunk_size=200)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertGreater(len(c), 0)

    def test_no_trivial_chunks(self):
        text = "\n\n".join(["Short."] * 50)
        chunks = chunk_text(text, chunk_size=600)
        for c in chunks:
            self.assertGreater(len(c), 60)

    def test_multiple_paragraphs(self):
        text = "Para one is quite long with many words.\n\nPara two has different content.\n\nPara three wraps things up nicely."
        chunks = chunk_text(text, chunk_size=100)
        self.assertGreaterEqual(len(chunks), 1)


# ── _clean_markdown ───────────────────────────────────────────────────────────

class TestCleanMarkdown(unittest.TestCase):
    def test_removes_image_syntax(self):
        text = "Hello ![alt](http://img.png) world"
        result = _clean_markdown(text)
        self.assertNotIn("![", result)
        self.assertIn("world", result)

    def test_converts_links_to_text(self):
        text = "Visit [Google](https://google.com) for more."
        result = _clean_markdown(text)
        self.assertIn("Google", result)
        self.assertNotIn("https://", result)

    def test_removes_html_tags(self):
        result = _clean_markdown("<b>Bold</b> text <script>js()</script>")
        self.assertNotIn("<b>", result)
        self.assertIn("Bold", result)


# ── _html_to_text ─────────────────────────────────────────────────────────────

class TestHtmlToText(unittest.TestCase):
    def test_strips_tags(self):
        html = "<p>Hello <b>world</b></p>"
        result = _html_to_text(html)
        self.assertIn("Hello", result)
        self.assertIn("world", result)
        self.assertNotIn("<p>", result)

    def test_removes_script(self):
        html = "<p>Content</p><script>alert('x')</script>"
        result = _html_to_text(html)
        self.assertNotIn("alert", result)
        self.assertIn("Content", result)

    def test_removes_nav(self):
        html = "<nav>Menu</nav><p>Article body text here.</p>"
        result = _html_to_text(html)
        self.assertNotIn("Menu", result)
        self.assertIn("Article body text here", result)


# ── _score ─────────────────────────────────────────────────────────────────────

class TestScoring(unittest.TestCase):
    def test_gov_my_bonus(self):
        r1 = SearchResult(title="SSM Registration", url="https://ssm.com.my/page", snippet="info")
        r2 = SearchResult(title="Blog Post", url="https://myblog.com/page", snippet="info")
        q = "SSM registration Malaysia"
        self.assertGreater(_score(r1, q), _score(r2, q))

    def test_relevance_gate_rejects_previous_petrol_result(self):
        q = "马来西亚现在注册 Sdn Bhd 需要什么条件和费用？请优先引用 SSM 官方来源。"
        petrol = SearchResult(
            title="最新一周马来西亚汽油（RON95）价格",
            url="https://carlist.my/news/ron95",
            snippet="RON95 petrol price in Malaysia this week",
        )
        ssm = SearchResult(
            title="How to register a Sdn Bhd with SSM",
            url="https://www.ssm.com.my/Pages/Register_Company.aspx",
            snippet="Company registration requirements and incorporation fee",
        )

        self.assertFalse(_search_result_is_relevant(petrol, q))
        self.assertTrue(_search_result_is_relevant(ssm, q))

    def test_cache_relevance_gate_rejects_stale_petrol_chunk(self):
        q = "马来西亚现在注册 Sdn Bhd 需要什么条件和费用？请优先引用 SSM 官方来源。"
        row = {
            "title": "RON95 petrol price",
            "domain": "carlist.my",
            "chunk_text": "Malaysia weekly petrol price for RON95 and diesel.",
        }
        self.assertFalse(_cache_row_is_relevant(row, q))

    def test_title_overlap_bonus(self):
        r1 = SearchResult(title="AI Policy Malaysia 2025", url="https://a.com", snippet="")
        r2 = SearchResult(title="Unrelated Title", url="https://b.com", snippet="")
        q = "AI policy Malaysia 2025"
        self.assertGreater(_score(r1, q), _score(r2, q))


# ── WebResearcher.prepare (mocked) ────────────────────────────────────────────

class TestWebResearcherPrepare(unittest.IsolatedAsyncioTestCase):
    async def test_no_search_needed_returns_empty_sources(self):
        researcher = WebResearcher.__new__(WebResearcher)
        researcher._brave = None
        researcher._tavily = None
        researcher._fetcher = MagicMock()
        researcher._cache = None
        researcher._cache_ready = False
        researcher._embed_fn = None
        researcher._max_results = 8
        researcher._max_pages = 3
        researcher._top_k_chunks = 6
        researcher._cfg = None

        messages = [{"role": "user", "content": "hello"}]
        aug, sources = await researcher.prepare(messages)
        self.assertEqual(sources, [])
        self.assertEqual(aug[0]["role"], "system")

    async def test_no_api_key_returns_gracefully(self):
        researcher = WebResearcher.__new__(WebResearcher)
        researcher._brave = None
        researcher._tavily = None
        researcher._fetcher = MagicMock()
        researcher._cache = None
        researcher._cache_ready = False
        researcher._embed_fn = None
        researcher._max_results = 8
        researcher._max_pages = 3
        researcher._top_k_chunks = 6
        researcher._cfg = None

        messages = [{"role": "user", "content": "latest AI policy Malaysia 2025"}]
        aug, sources = await researcher.prepare(messages)
        self.assertEqual(sources, [])
        self.assertIn("WEB SEARCH ATTEMPTED", aug[0]["content"])

    async def test_force_search_uses_research_even_without_keyword(self):
        researcher = WebResearcher.__new__(WebResearcher)
        researcher._brave = MagicMock()
        researcher._tavily = None
        researcher._fetcher = MagicMock()
        researcher._cache = None
        researcher._cache_ready = True
        researcher._embed_fn = None
        researcher._max_results = 8
        researcher._max_pages = 3
        researcher._top_k_chunks = 6
        researcher._cfg = None
        researcher._run_research = AsyncMock(return_value=ResearchResult(
            question="tell me about ssm",
            context="=== WEB SEARCH RESULTS ===\nSome source context.",
            sources=[{"title": "SSM", "url": "https://www.ssm.com.my"}],
        ))

        messages = [{"role": "user", "content": "tell me about ssm"}]
        aug, sources = await researcher.prepare(messages, force_search=True)

        self.assertIn("WEB SEARCH", aug[0]["content"])
        self.assertEqual(sources[0]["url"], "https://www.ssm.com.my")

    async def test_prepare_injects_system_message(self):
        researcher = WebResearcher.__new__(WebResearcher)
        researcher._brave = MagicMock()
        researcher._tavily = None
        researcher._fetcher = MagicMock()
        researcher._cache = None
        researcher._cache_ready = True
        researcher._embed_fn = None
        researcher._max_results = 8
        researcher._max_pages = 3
        researcher._top_k_chunks = 6
        researcher._cfg = None

        mock_result = ResearchResult(
            question="latest AI policy Malaysia 2025",
            context="[WEB SEARCH RESULTS]\n\nSome context here.",
            sources=[{"title": "Gov Page", "url": "https://gov.my"}],
        )
        researcher._run_research = AsyncMock(return_value=mock_result)

        messages = [{"role": "user", "content": "latest AI policy Malaysia 2025"}]
        aug, sources = await researcher.prepare(messages)

        self.assertEqual(aug[0]["role"], "system")
        self.assertIn("WEB SEARCH", aug[0]["content"])
        self.assertEqual(sources[0]["url"], "https://gov.my")
        self.assertEqual(len(aug), 2)  # system + user


if __name__ == "__main__":
    unittest.main()
