"""
Web Search + Web RAG module.

Search  : Brave Search API (primary) → Tavily (fallback)
Fetch   : Crawl4AI AsyncWebCrawler with PruningContentFilter + keyword relevance extraction
Cache   : PostgreSQL + pgvector  (TTL-based, optional)
Context : Top-k chunks injected as system-message prefix before Gemma generation

Required env (at least one search key):
    BRAVE_SEARCH_API_KEY
    TAVILY_API_KEY

Optional:
    DATABASE_URL          override pgvector URI from config
    WEB_CACHE_TTL_HOURS   default 24
"""

from __future__ import annotations

import os
import re
import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# ── config bootstrap ─────────────────────────────────────────────────────────

try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config_loader import cfg as _cfg
except Exception:
    _cfg = None


# ── language detector (kept for server.py compatibility) ─────────────────────

_LANG_PATTERNS: Dict[str, str] = {
    "Chinese":  r"[一-鿿㐀-䶿]",
    "Japanese": r"[぀-ゟ゠-ヿ]",
    "Korean":   r"[가-힯]",
    "Arabic":   r"[؀-ۿ]",
    "Thai":     r"[฀-๿]",
    "Malay":    r"\b(yang|dan|di|untuk|dengan|tidak|ini|itu|boleh|kami|saya|anda|ada|atau)\b",
    "French":   r"\b(le|la|les|un|une|des|et|en|de|du|pour|avec|sur|est|je|vous|nous)\b",
    "German":   r"\b(der|die|das|ein|und|ist|ich|Sie|wir|nicht|für|mit|auf|von)\b",
    "Spanish":  r"\b(el|la|los|las|un|una|y|en|de|que|es|por|con|para|no|se|yo)\b",
}

def detect_language(text: str) -> str:
    for lang, pat in _LANG_PATTERNS.items():
        if re.search(pat, text, re.IGNORECASE):
            return lang
    return "English"


# ── data models ───────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    score: float = 0.0
    domain: str = ""
    published_date: str = ""

    def __post_init__(self) -> None:
        if not self.domain:
            self.domain = urlparse(self.url).netloc.lower().lstrip("www.")


@dataclass
class WebPage:
    url: str
    title: str
    content: str          # clean Markdown text
    fit_content: str = "" # BM25-filtered relevant content (subset of content)
    domain: str = ""

    def __post_init__(self) -> None:
        if not self.domain:
            self.domain = urlparse(self.url).netloc.lower().lstrip("www.")

    @property
    def best_content(self) -> str:
        """Return BM25-filtered content if available, else full content."""
        return self.fit_content if len(self.fit_content) > 200 else self.content


@dataclass
class ResearchResult:
    question: str
    context: str
    sources: List[Dict]
    chunks_used: int = 0
    search_queries: List[str] = field(default_factory=list)
    attempted: bool = False


# ── domain filter ─────────────────────────────────────────────────────────────

_SKIP_DOMAINS = frozenset({
    "youtube.com", "youtu.be", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "tiktok.com", "pinterest.com",
    "reddit.com", "quora.com",
    "douyin.com", "kuaishou.com",           # short-video: no extractable text
    "kimi.ai", "openai.com", "chat.openai.com",
    "gemini.google.com", "copilot.microsoft.com",
})

_COUNTRY_CODES = {
    "malaysia": "MY",
    "singapore": "SG",
    "indonesia": "ID",
    "thailand": "TH",
    "philippines": "PH",
    "vietnam": "VN",
    "china": "CN",
    "hong kong": "HK",
    "taiwan": "TW",
    "japan": "JP",
    "south korea": "KR",
    "united states": "US",
    "america": "US",
    "australia": "AU",
    "united kingdom": "GB",
    "uk": "GB",
}

_LANG_SEARCH_CODES = {
    "Chinese": "zh-hans",
    "Malay": "ms",
    "French": "fr",
    "German": "de",
    "Spanish": "es",
}


# ── needs-search heuristic ────────────────────────────────────────────────────

# ASCII/Malay: word-boundary safe
_NEEDS_SEARCH_ASCII = re.compile(
    r"\b("
    r"latest|terbaru|recently|current|now|today|tonight|"
    r"2024|2025|2026|this year|this month|this week|"
    r"price|harga|cost|rate|kadar|berapa|how much|"
    r"policy|dasar|regulation|peraturan|guideline|garis panduan|"
    r"company|syarikat|registration|pendaftaran|ssm|lhdn|hasil|"
    r"news|berita|update|kemaskini|announce|pengumuman|"
    r"who is|siapa|ceo|minister|menteri|chairman|director|pengarah|"
    r"version|versi|release|launched|released|available|"
    r"where|mana|location|address|alamat|"
    r"weather|cuaca|forecast|"
    r"event|acara|schedule|jadual"
    r")\b",
    re.IGNORECASE,
)

# CJK: no \b (Chinese chars have no ASCII word boundary)
_NEEDS_SEARCH_ZH = re.compile(
    r"(最新|最近|今天|今日|今晚|"
    r"费用|价格|多少钱|利率|"
    r"政策|法规|规定|"
    r"公司|注册|登记|"
    r"新闻|更新|消息|公告|"
    r"谁是|现任|主席|部长|总裁|"
    r"版本|发布|发行|"
    r"哪里|地点|地址|"
    r"天气|预报|"
    r"活动|日程|会议)"
)


def needs_web_search(question: str) -> bool:
    q = question.strip()
    if len(q) < 8:
        return False
    return bool(_NEEDS_SEARCH_ASCII.search(q) or _NEEDS_SEARCH_ZH.search(q))


def _is_trivial_question(question: str) -> bool:
    q = re.sub(r"\s+", " ", (question or "").strip().lower())
    if not q:
        return True
    greetings = {
        "hi", "hello", "hey", "yo", "thanks", "thank you", "ok", "okay",
        "你好", "您好", "谢谢", "嗨", "哈喽", "terima kasih", "hai",
    }
    return q in greetings or len(q) < 4


def _country_code_from_location(location: str) -> str:
    raw = (location or "").lower()
    for name, code in _COUNTRY_CODES.items():
        if name in raw:
            return code
    return "MY"


def _search_language_for_question(question: str) -> str:
    return _LANG_SEARCH_CODES.get(detect_language(question), "en")


def _source_kind(domain: str, title: str = "") -> str:
    blob = f"{domain} {title}".lower()
    if any(marker in domain for marker in (".gov", "ssm.", "hasil.", "mof.", "pmo.", "mdec.", "mampu.")):
        return "official"
    if any(marker in blob for marker in ("reuters", "bloomberg", "apnews", "bbc", "cnbc", "theedgemalaysia")):
        return "news"
    if any(marker in blob for marker in ("pricing", "price", "shop", "store", "marketplace")):
        return "market"
    return "web"


def _dedupe_results(results: List[SearchResult]) -> List[SearchResult]:
    seen: set[str] = set()
    deduped: List[SearchResult] = []
    for result in results:
        if not result.url or result.url in seen:
            continue
        seen.add(result.url)
        deduped.append(result)
    return deduped


_LATIN_QUERY_STOP = {
    "the", "and", "for", "are", "was", "that", "this", "with", "from", "have",
    "has", "what", "when", "where", "which", "please", "give", "source",
    "sources", "cite", "cites", "current", "latest", "today", "now", "malaysia",
    "yang", "dan", "untuk", "dengan", "tidak", "ini", "itu", "sila", "beri",
}

_CJK_QUERY_STOP = {
    "马来", "来西", "西亚", "马来西亚", "现在", "今天", "今日", "请给", "给来",
    "来源", "官方", "优先", "引用", "需要", "什么", "多少", "最新", "目前",
}


def _meaningful_query_terms(question: str) -> set[str]:
    """Terms used only for relevance gating, not for semantic ranking."""
    q = (question or "").lower()
    terms = {
        w for w in re.findall(r"\b[a-zA-Z][a-zA-Z0-9]{2,}\b", q)
        if w not in _LATIN_QUERY_STOP
    }

    # Chinese/Japanese-style queries need phrase-level anchors. Character-level
    # matching is too loose: "马来西亚" alone caused petrol pages to leak into
    # company-registration searches.
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", question or "")
    for run in cjk_runs:
        for n in (2, 3, 4):
            for i in range(0, max(len(run) - n + 1, 0)):
                gram = run[i:i + n]
                if not any(gram in stop or stop in gram for stop in _CJK_QUERY_STOP):
                    terms.add(gram)

    # Keep important mixed-language business anchors as-is.
    if re.search(r"\bsdn\s*bhd\b", q):
        terms.update({"sdn", "bhd", "sdn bhd"})
    if "ssm" in q:
        terms.add("ssm")
    if "ron95" in q:
        terms.add("ron95")
    return terms


def _relevance_score_for_text(question: str, text: str) -> int:
    haystack = (text or "").lower()
    if not haystack:
        return 0
    score = 0
    for term in _meaningful_query_terms(question):
        if term.lower() in haystack:
            score += 2 if len(term) >= 4 or " " in term else 1
    return score


def _should_gate_relevance(question: str) -> bool:
    return len(_meaningful_query_terms(question)) >= 2


def _search_result_is_relevant(result: SearchResult, question: str) -> bool:
    blob = f"{result.title} {result.snippet} {result.domain}"
    return _relevance_score_for_text(question, blob) > 0


def _cache_row_is_relevant(row: Dict, question: str) -> bool:
    blob = f"{row.get('title', '')} {row.get('domain', '')} {row.get('chunk_text', '')}"
    return _relevance_score_for_text(question, blob) > 0


# ── Brave Search ──────────────────────────────────────────────────────────────

class BraveSearchClient:
    _BASE = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        }

    async def search(
        self,
        query: str,
        count: int = 8,
        freshness: Optional[str] = None,
        country: str = "MY",
        search_lang: str = "en",
    ) -> List[SearchResult]:
        """freshness: 'pd' past-day, 'pw' past-week, None = any time"""
        params: Dict[str, Any] = {
            "q": query,
            "count": min(count, 20),
            "country": (country or "MY").upper(),
            "search_lang": search_lang or "en",
            "safesearch": "moderate",
            "text_decorations": "false",
        }
        if freshness:
            params["freshness"] = freshness

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self._BASE, headers=self._headers, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("Brave search error %r: %s", query, exc)
            return []

        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
                published_date=item.get("page_age", ""),
            )
            for item in data.get("web", {}).get("results", [])
        ]


# ── Tavily Search ─────────────────────────────────────────────────────────────

class TavilySearchClient:
    _BASE = "https://api.tavily.com/search"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    async def search(
        self,
        query: str,
        max_results: int = 8,
        days: Optional[int] = None,
        country: str = "",
        search_lang: str = "",
    ) -> List[SearchResult]:
        payload: Dict[str, Any] = {
            "api_key": self._key,
            "query": query,
            "max_results": min(max_results, 10),
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        if days:
            payload["days"] = days

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(self._BASE, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.error("Tavily search error %r: %s", query, exc)
            return []

        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                score=float(item.get("score", 0.0)),
                published_date=item.get("published_date", "") or "",
            )
            for item in data.get("results", [])
        ]


# ── Crawl4AI fetcher ──────────────────────────────────────────────────────────

class Crawl4AIFetcher:
    """
    Extracts clean Markdown from URLs using Crawl4AI.

    Uses BM25ContentFilter to return only the chunks most relevant to the
    search query — dramatically reduces noise sent to the LLM.

    Falls back to a simple httpx+regex extraction if Crawl4AI fails.
    """

    # One shared crawler instance to avoid repeated browser launches
    _crawler = None
    _crawler_lock = asyncio.Lock()

    @classmethod
    async def _get_crawler(cls):
        async with cls._crawler_lock:
            if cls._crawler is None:
                from crawl4ai import AsyncWebCrawler, BrowserConfig
                cls._crawler = AsyncWebCrawler(
                    config=BrowserConfig(
                        headless=True,
                        verbose=False,
                    )
                )
                await cls._crawler.__aenter__()
        return cls._crawler

    async def fetch(self, url: str, query: str = "") -> Optional[WebPage]:
        """
        Fetch and extract clean content from a URL.
        If query is provided, applies BM25 filtering to return the most
        relevant portions of the page.
        """
        page = await self._fetch_crawl4ai(url, query)
        if page:
            return page
        # Graceful fallback: plain httpx + regex
        return await self._fetch_simple(url)

    async def _fetch_crawl4ai(self, url: str, query: str) -> Optional[WebPage]:
        try:
            from crawl4ai import CrawlerRunConfig, CacheMode
            from crawl4ai.content_filter_strategy import PruningContentFilter
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

            md_generator = DefaultMarkdownGenerator(
                content_filter=PruningContentFilter(threshold=0.40)
            )

            config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                word_count_threshold=60,
                excluded_tags=["nav", "footer", "header", "aside",
                               "script", "style", "form", "figure"],
                remove_overlay_elements=True,
                remove_consent_popups=True,
                process_iframes=False,
                markdown_generator=md_generator,
                page_timeout=15000,
                verbose=False,
            )

            crawler = await self.__class__._get_crawler()
            result = await crawler.arun(url=url, config=config)

            if not result.success:
                logger.debug("Crawl4AI failed for %s: %s", url, getattr(result, "error_message", ""))
                return None

            # raw_markdown is the PruningContentFilter-cleaned content
            md_obj = result.markdown
            if hasattr(md_obj, "raw_markdown"):
                raw = md_obj.raw_markdown or ""
            else:
                raw = str(md_obj) if md_obj else ""

            title = ""
            if result.metadata:
                title = result.metadata.get("title", "")

            content = _clean_md(raw)
            if not content or len(content.strip()) < 100:
                return None

            # Keyword-based relevance extraction (works for CJK + Latin, no BM25 needed)
            fit = _extract_relevant_content(content, query, max_chars=2500)

            return WebPage(
                url=url,
                title=title,
                content=content,
                fit_content=fit,
            )

        except Exception as exc:
            logger.warning("Crawl4AI error for %s: %s", url, exc)
            return None

    async def _fetch_simple(self, url: str) -> Optional[WebPage]:
        """httpx + regex fallback for when Crawl4AI is unavailable."""
        headers = {"User-Agent": "Mozilla/5.0 (compatible; PepperBot/1.0)"}
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    return None
                ctype = resp.headers.get("content-type", "")
                if "html" not in ctype and "text" not in ctype:
                    return None
                text = _html_to_text(resp.text)
                if len(text) < 150:
                    return None
                return WebPage(url=url, title="", content=text)
        except Exception as exc:
            logger.debug("Simple fetch failed for %s: %s", url, exc)
            return None


def _clean_md(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)           # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links → text only
    text = re.sub(r"<[^>]+>", " ", text)                  # stray HTML
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_relevant_content(text: str, query: str, max_chars: int = 2500) -> str:
    """
    Return the most query-relevant paragraphs from clean text.
    Works for any language: CJK character-level + Latin word-level matching.
    """
    if not text:
        return ""
    if not query or len(text) <= max_chars:
        return text[:max_chars]

    # Build query term sets
    cjk_terms = set(re.findall(
        r'[一-鿿㐀-䶿฀-๿가-힯]', query
    ))
    _STOP = {'the', 'and', 'for', 'are', 'was', 'that', 'this',
             'with', 'from', 'have', 'has', 'yang', 'dan', 'untuk'}
    latin_terms = set(
        w.lower() for w in re.findall(r'\b[a-zA-Z][a-zA-Z0-9]{2,}\b', query)
        if w.lower() not in _STOP
    )

    if not cjk_terms and not latin_terms:
        return text[:max_chars]

    paras = [p.strip() for p in re.split(r'\n{2,}', text) if len(p.strip()) > 50]
    if not paras:
        return text[:max_chars]

    scored = []
    for p in paras:
        pl = p.lower()
        score = (
            sum(2 for c in cjk_terms if c in p)
            + sum(1 for t in latin_terms if t in pl)
            + (3 if re.match(r'^#{1,3} ', p) else 0)
        )
        scored.append((score, p))

    scored.sort(key=lambda x: -x[0])
    result, total = [], 0
    for _, para in scored:
        if total + len(para) + 2 > max_chars:
            break
        result.append(para)
        total += len(para) + 2

    return "\n\n".join(result) if result else text[:max_chars]


def _html_to_text(html: str) -> str:
    html = re.sub(
        r"<(script|style|nav|footer|header|aside|form)[^>]*>.*?</\1>",
        " ", html, flags=re.DOTALL | re.IGNORECASE,
    )
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&[a-z#0-9]+;", " ", html)
    html = re.sub(r"[ \t]{2,}", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


# ── text chunker ──────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 700, overlap: int = 80) -> List[str]:
    if not text or not text.strip():
        return []
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]

    # Split on Markdown ## headings first (natural article sections)
    sections = re.split(r"\n(?=#{1,3} )", text)
    chunks: List[str] = []
    buf = ""

    for section in sections:
        # Section itself is large → sentence-split
        if len(section) > chunk_size:
            sentences = re.split(r"(?<=[.!?。！？])\s+", section)
            for sent in sentences:
                if len(buf) + len(sent) + 1 > chunk_size:
                    if buf.strip():
                        chunks.append(buf.strip())
                    words = buf.split()
                    buf = " ".join(words[-15:]) + " " + sent if words else sent
                else:
                    buf = (buf + " " + sent).strip()
        else:
            if len(buf) + len(section) + 2 > chunk_size:
                if buf.strip():
                    chunks.append(buf.strip())
                buf = section
            else:
                buf = (buf + "\n\n" + section).strip() if buf else section

    if buf.strip():
        chunks.append(buf.strip())

    return [c for c in chunks if len(c) > 80]


# ── pgvector web-content cache ────────────────────────────────────────────────

_EMBED_DIM = 768  # nomic-embed-text output dimension

_DDL = f"""\
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS web_content_cache (
    id          BIGSERIAL PRIMARY KEY,
    url         TEXT      NOT NULL,
    url_hash    CHAR(32)  NOT NULL,
    title       TEXT      DEFAULT '',
    chunk_text  TEXT      NOT NULL,
    chunk_index INTEGER   DEFAULT 0,
    embedding   vector({_EMBED_DIM}),
    domain      TEXT      DEFAULT '',
    fetched_at  TIMESTAMPTZ DEFAULT NOW(),
    expires_at  TIMESTAMPTZ,
    UNIQUE (url_hash, chunk_index)
);
CREATE INDEX IF NOT EXISTS wcc_url_hash_idx ON web_content_cache (url_hash);
CREATE INDEX IF NOT EXISTS wcc_expires_idx  ON web_content_cache (expires_at);
"""


def _pg_dsn(uri: str) -> str:
    """Strip SQLAlchemy dialect prefix so psycopg3 accepts the DSN."""
    return re.sub(r"^postgresql\+psycopg", "postgresql", uri)


class WebCache:
    def __init__(self, connection_uri: str, ttl_hours: int = 24) -> None:
        self._dsn = _pg_dsn(connection_uri)
        self._ttl = ttl_hours
        self._pool = None
        self._ready = False

    async def _pool_(self):
        if self._pool is None:
            from psycopg_pool import AsyncConnectionPool
            self._pool = AsyncConnectionPool(self._dsn, min_size=1, max_size=4, open=False)
            await self._pool.open()
        return self._pool

    async def init_schema(self) -> bool:
        try:
            pool = await self._pool_()
            async with pool.connection() as conn:
                for stmt in _DDL.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        await conn.execute(stmt)
            self._ready = True
            return True
        except Exception as exc:
            logger.warning("WebCache schema init failed (cache disabled): %s", exc)
            return False

    async def is_fresh(self, url: str) -> bool:
        if not self._ready:
            return False
        url_hash = hashlib.md5(url.encode()).hexdigest()
        try:
            pool = await self._pool_()
            async with pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT 1 FROM web_content_cache WHERE url_hash=%s AND expires_at > NOW() LIMIT 1",
                    (url_hash,),
                )
                row = await cur.fetchone()
                return row is not None
        except Exception:
            return False

    @staticmethod
    def _vec_str(emb: List[float]) -> str:
        """Format embedding list as pgvector literal '[f1,f2,...]'."""
        return "[" + ",".join(f"{v:.8f}" for v in emb) + "]"

    async def store(
        self,
        url: str,
        title: str,
        chunks: List[str],
        embeddings: List[List[float]],
    ) -> None:
        if not self._ready or not chunks:
            return
        url_hash = hashlib.md5(url.encode()).hexdigest()
        domain = urlparse(url).netloc.lower().lstrip("www.")
        expires_at = datetime.now(timezone.utc) + timedelta(hours=self._ttl)
        try:
            pool = await self._pool_()
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM web_content_cache WHERE url_hash=%s", (url_hash,)
                )
                for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                    if not emb:
                        continue
                    await conn.execute(
                        """INSERT INTO web_content_cache
                               (url, url_hash, title, chunk_text, chunk_index,
                                embedding, domain, expires_at)
                           VALUES (%s,%s,%s,%s,%s,%s::vector,%s,%s)
                           ON CONFLICT (url_hash, chunk_index) DO UPDATE
                               SET chunk_text  = EXCLUDED.chunk_text,
                                   embedding   = EXCLUDED.embedding,
                                   expires_at  = EXCLUDED.expires_at""",
                        (url, url_hash, title, chunk, i,
                         self._vec_str(emb), domain, expires_at),
                    )
        except Exception as exc:
            logger.warning("WebCache.store failed for %s: %s", url, exc)

    async def retrieve_similar(
        self,
        query_embedding: List[float],
        top_k: int = 8,
        threshold: float = 0.72,
    ) -> List[Dict]:
        if not self._ready or not query_embedding:
            return []
        try:
            pool = await self._pool_()
            async with pool.connection() as conn:
                cur = await conn.execute(
                    """SELECT url, title, chunk_text, domain,
                              1 - (embedding <=> %s::vector) AS similarity
                       FROM web_content_cache
                       WHERE expires_at > NOW()
                         AND 1 - (embedding <=> %s::vector) >= %s
                       ORDER BY similarity DESC
                       LIMIT %s""",
                    (self._vec_str(query_embedding),
                     self._vec_str(query_embedding),
                     threshold, top_k),
                )
                rows = await cur.fetchall()
                cols = [d.name for d in cur.description]
                return [dict(zip(cols, row)) for row in rows]
        except Exception as exc:
            logger.warning("WebCache.retrieve_similar failed: %s", exc)
            return []

    async def cleanup_expired(self) -> None:
        if not self._ready:
            return
        try:
            pool = await self._pool_()
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM web_content_cache WHERE expires_at < NOW()")
        except Exception as exc:
            logger.debug("WebCache.cleanup_expired: %s", exc)


# ── Ollama embedding ──────────────────────────────────────────────────────────

async def _ollama_embed(text: str, base_url: str, model: str) -> List[float]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/api/embed",
                json={"model": model, "input": text},
            )
            resp.raise_for_status()
            return (resp.json().get("embeddings") or [[]])[0]
    except Exception as exc:
        logger.debug("Ollama embed failed: %s", exc)
        return []


# ── result scoring ────────────────────────────────────────────────────────────

def _score(result: SearchResult, question: str) -> float:
    q_terms = set(re.findall(r"\w+", question.lower()))
    t_terms = set(re.findall(r"\w+", result.title.lower()))
    s_terms = set(re.findall(r"\w+", result.snippet.lower()))

    score = result.score
    if q_terms:
        score += len(q_terms & t_terms) / len(q_terms) * 2.5
        score += len(q_terms & s_terms) / len(q_terms)

    domain = result.domain
    if ".gov.my" in domain or ".gov" in domain:
        score += 0.6
    if any(k in domain for k in ("ssm.", "hasil.", "mampu.", "mdec.", "mof.", "pmo.")):
        score += 0.5
    if "official" in result.title.lower():
        score += 0.3

    return score


# ── secondary query builder ───────────────────────────────────────────────────

def _secondary_query(question: str) -> str:
    """
    Generate a complementary search query for the same question from a different angle.
    Returns empty string if no useful secondary query can be formed.
    """
    q = question.strip()
    if len(q) < 10:
        return ""

    q_lower = q.lower()

    # News/latest → add "official" / "government" angle
    if re.search(r"\b(latest|terbaru|最新|news|berita|新闻|update|kemaskini)\b", q_lower):
        return q + " official announcement"

    # Policy/regulation → add "guide" angle
    if re.search(r"\b(policy|dasar|regulation|peraturan|法规|政策|guideline|garis panduan)\b",
                 q_lower):
        return q + " implementation guide"

    # Price/rate → add "comparison" angle
    if re.search(r"\b(price|harga|rate|kadar|exchange|berapa|多少|费用|汇率)\b", q_lower):
        return q + " comparison 2025"

    # Company/registration → add "requirements" angle
    if re.search(r"\b(company|syarikat|register|daftar|注册|ssm|sdn bhd)\b", q_lower):
        return q + " requirements documents"

    # Grant/loan → add "eligibility" angle
    if re.search(r"\b(grant|geran|loan|pinjaman|funding|助|资助|贷款)\b", q_lower):
        return q + " eligibility criteria apply"

    # Game/entertainment → patch notes / season update angle
    if re.search(
        r"(游戏|赛季|英雄|皮肤|版本|patch|season|hero|game|moba|hok|王者|"
        r"lol|dota|valorant|mobile legend|mlbb|pubg|genshin|原神)",
        q_lower,
    ):
        return q + " patch notes season update"

    # For general queries: add "Malaysia" if not already there
    if "malaysia" not in q_lower and "马来西亚" not in q_lower:
        return q + " Malaysia"

    return ""


def _query_plan(question: str, user_location: str = "") -> List[str]:
    """Small deterministic planner for multi-angle web search."""
    q = re.sub(r"\s+", " ", question.strip())
    if not q:
        return []

    q_lower = q.lower()
    location = user_location.strip()
    location_lower = location.lower()
    queries = [q]

    secondary = _secondary_query(q)
    if secondary and secondary not in queries:
        queries.append(secondary)

    if location and location_lower not in q_lower:
        queries.append(f"{q} {location}")

    if re.search(r"\b(policy|regulation|guideline|registration|ssm|lhdn|tax|grant|loan|permit|license|licence|requirement)\b", q_lower) or re.search(r"(政策|法规|规定|注册|登记|税|补助|贷款|执照|许可证)", q):
        if "malaysia" in q_lower or "马来西亚" in q or "my" in location_lower:
            queries.append(f"{q} site:gov.my OR site:ssm.com.my OR site:hasil.gov.my")
        else:
            queries.append(f"{q} official government source")

    if re.search(r"\b(price|harga|cost|rate|kadar|how much|fee|fees)\b", q_lower) or re.search(r"(价格|费用|多少钱|利率|收费)", q):
        queries.append(f"{q} official price rate")

    deduped = []
    seen = set()
    for query in queries:
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(query)
    return deduped[:4]


# ── main orchestrator ─────────────────────────────────────────────────────────

class WebResearcher:
    """
    Orchestrates: Brave/Tavily → Crawl4AI → pgvector cache → Gemma context.

    Main web-search researcher used by the app:
        researcher = WebResearcher(cfg, user_location="Malaysia")
        augmented_msgs, sources = await researcher.prepare(inference_messages)
    """

    def __init__(self, config=None, embed_fn=None, user_location: str = "") -> None:
        cfg = config or _cfg
        self._cfg = cfg
        self._user_location = user_location  # e.g. "Malaysia", "Singapore"

        # Search clients
        brave_key = os.environ.get("BRAVE_SEARCH_API_KEY") or (
            getattr(cfg, "brave_search_api_key", "") if cfg else ""
        )
        tavily_key = os.environ.get("TAVILY_API_KEY") or (
            getattr(cfg, "tavily_api_key", "") if cfg else ""
        )
        self._brave = BraveSearchClient(brave_key) if brave_key else None
        self._tavily = TavilySearchClient(tavily_key) if tavily_key else None

        if not self._brave and not self._tavily:
            logger.warning("WebResearcher: no search API key — set BRAVE_SEARCH_API_KEY or TAVILY_API_KEY")

        # Crawl4AI fetcher (one shared instance)
        self._fetcher = Crawl4AIFetcher()

        # pgvector cache
        db_uri = os.environ.get("DATABASE_URL") or (
            getattr(cfg, "pgvector_connection_uri", "") if cfg else ""
        )
        ttl = int(os.environ.get("WEB_CACHE_TTL_HOURS", "24"))
        if cfg:
            ttl = getattr(cfg, "web_cache_ttl_hours", ttl)
        self._cache: Optional[WebCache] = WebCache(db_uri, ttl) if db_uri else None
        self._cache_ready = False

        # Embedding function (Ollama nomic-embed-text)
        if embed_fn is not None:
            self._embed_fn = embed_fn
        elif cfg:
            ollama_url = getattr(cfg, "ollama_base_url", "http://localhost:11434")
            emb_model = getattr(cfg, "ollama_embedding_model", "nomic-embed-text")
            async def _auto_embed(text: str, _u=ollama_url, _m=emb_model) -> List[float]:
                return await _ollama_embed(text, _u, _m)
            self._embed_fn = _auto_embed
        else:
            self._embed_fn = None

        # Tunables — config_loader flattens nested keys: web_search.max_pages → web_search_max_pages
        self._max_results = (
            getattr(cfg, "web_search_max_results", None) or
            getattr(cfg, "max_results_total", 15)
        ) if cfg else 15
        self._max_pages = getattr(cfg, "web_search_max_pages", 6) if cfg else 6
        self._top_k_chunks = 10

    # ── public tool functions ─────────────────────────────────────────────────

    async def web_search(self, query: str, max_results: int = 8) -> List[SearchResult]:
        results = await self._search_raw(query, max_results=max_results)
        return sorted(results, key=lambda r: _score(r, query), reverse=True)

    async def web_fetch(self, url: str, query: str = "") -> Optional[WebPage]:
        return await self._fetcher.fetch(url, query=query)

    async def web_extract(self, url: str, query: str = "") -> Optional[str]:
        page = await self._fetcher.fetch(url, query=query)
        return page.best_content if page else None

    async def web_research(
        self,
        question: str,
        max_results: int = 8,
        max_pages: int = 4,
    ) -> ResearchResult:
        return await self._run_research(question, max_results=max_results, max_pages=max_pages)

    # ── server.py-compatible interface ────────────────────────────────────────

    async def prepare(
        self,
        messages: List[Dict],
        user_location: str = "",
        force_search: bool = False,
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Build web-grounded messages for server.py.
        Returns (augmented_messages, search_sources).
        server.py will prepend identity/date/language to the system message.

        user_location: e.g. "Malaysia", "Singapore" — used for currency/unit localization.
        """
        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = (m.get("content") or "").strip()
                break

        empty = [{"role": "system", "content": ""}] + list(messages)

        should_search = force_search or needs_web_search(user_msg)
        if not user_msg or not should_search or _is_trivial_question(user_msg):
            return empty, []
        if not self._brave and not self._tavily:
            no_sources = self._build_no_sources_context(
                user_msg,
                reason="No search provider is configured. Set BRAVE_SEARCH_API_KEY or TAVILY_API_KEY.",
                user_location=user_location or getattr(self, "_user_location", ""),
            )
            return [{"role": "system", "content": no_sources}] + list(messages), []

        location = user_location or getattr(self, "_user_location", "")

        try:
            result = await self._run_research(
                user_msg,
                max_results=self._max_results,
                max_pages=self._max_pages,
                user_location=location,
            )
        except Exception as exc:
            logger.error("WebResearcher.prepare failed: %s", exc)
            return empty, []

        if not result.context:
            no_sources = self._build_no_sources_context(
                user_msg,
                reason="Search was attempted, but no reliable live sources were retrieved.",
                user_location=location,
            )
            return [{"role": "system", "content": no_sources}] + list(messages), []

        augmented = [{"role": "system", "content": result.context}] + list(messages)
        sources = [{"title": s["title"], "url": s["url"]} for s in result.sources]
        return augmented, sources

    # ── internal pipeline ─────────────────────────────────────────────────────

    async def _ensure_cache_ready(self) -> None:
        if self._cache and not self._cache_ready:
            ok = await self._cache.init_schema()
            self._cache_ready = ok

    async def _embed(self, text: str) -> List[float]:
        if not self._embed_fn:
            return []
        try:
            return await self._embed_fn(text) or []
        except Exception as exc:
            logger.debug("Embed failed: %s", exc)
            return []

    async def _search_raw(
        self,
        query: str,
        max_results: int = 10,
        freshness: Optional[str] = None,
        country: str = "MY",
        search_lang: str = "en",
    ) -> List[SearchResult]:
        results: List[SearchResult] = []

        if self._brave:
            results = await self._brave.search(
                query,
                count=max_results,
                freshness=freshness,
                country=country,
                search_lang=search_lang,
            )

        if not results and self._tavily:
            logger.debug("Brave returned 0 results → Tavily fallback for %r", query)
            days = {"pd": 1, "pw": 7, "pm": 30}.get(freshness or "", None)
            results = await self._tavily.search(
                query,
                max_results=max_results,
                days=days,
                country=country,
                search_lang=search_lang,
            )

        return [r for r in results if not any(s in r.domain for s in _SKIP_DOMAINS)]

    async def _fetch_embed_cache(self, result: SearchResult, query: str) -> Optional[str]:
        """
        Fetch page with Crawl4AI (BM25 filtered), chunk, embed, store in pgvector.
        Returns the best content string for immediate use, or None if cache hit.
        """
        await self._ensure_cache_ready()

        if self._cache and await self._cache.is_fresh(result.url):
            return None  # already cached; will be retrieved via similarity search

        page = await self._fetcher.fetch(result.url, query=query)

        if not page:
            return result.snippet or None

        # Store full page chunks in cache (background task)
        if self._cache and self._embed_fn and page.content:
            chunks = chunk_text(page.content)
            if chunks:
                asyncio.create_task(
                    self._store_page(result.url, page.title or result.title, chunks)
                )

        # Return BM25-filtered content for immediate context injection
        content = page.best_content
        return content[:2500] if content else result.snippet

    async def _store_page(self, url: str, title: str, chunks: List[str]) -> None:
        embeddings = await asyncio.gather(*[self._embed(c) for c in chunks])
        if self._cache:
            await self._cache.store(url, title, chunks, list(embeddings))

    async def _run_research(
        self,
        question: str,
        max_results: int = 8,
        max_pages: int = 4,
        user_location: str = "",
    ) -> ResearchResult:
        location = user_location or getattr(self, "_user_location", "")
        await self._ensure_cache_ready()

        # freshness hint based on question keywords
        current_year = datetime.now(timezone.utc).year
        fresh_pat = re.compile(
            rf"\b(latest|terbaru|最新|today|tonight|news|berita|新闻|{current_year}|"
            r"this week|this month|recently)\b",
            re.IGNORECASE,
        )
        very_fresh_pat = re.compile(r"\b(today|tonight|this week|breaking|live|weather|forecast)\b|今天|今日|今晚|天气|预报", re.IGNORECASE)
        freshness = "pd" if very_fresh_pat.search(question) else ("pw" if fresh_pat.search(question) else None)

        country = _country_code_from_location(location)
        search_lang = _search_language_for_question(question)
        queries = _query_plan(question, location)

        raw_batches = await asyncio.gather(
            *[
                self._search_raw(
                    query,
                    max_results=max(4, max_results // max(1, len(queries))),
                    freshness=freshness,
                    country=country,
                    search_lang=search_lang,
                )
                for query in queries
            ],
            return_exceptions=True,
        )
        raw: List[SearchResult] = []
        for batch in raw_batches:
            if isinstance(batch, Exception):
                logger.debug("Search batch failed: %s", batch)
                continue
            raw.extend(batch)
        raw = _dedupe_results(raw)
        if _should_gate_relevance(question):
            relevant_raw = [r for r in raw if _search_result_is_relevant(r, question)]
            if relevant_raw:
                raw = relevant_raw

        if not raw:
            return ResearchResult(question=question, context="", sources=[], search_queries=queries, attempted=True)

        scored = sorted(raw, key=lambda r: _score(r, question), reverse=True)
        top = scored[:max_results]

        # Fetch top pages concurrently
        fetched = await asyncio.gather(
            *[self._fetch_embed_cache(r, question) for r in top[:max_pages]],
            return_exceptions=True,
        )

        context_parts: List[str] = []
        sources_seen: set = set()
        sources: List[Dict] = []

        def _add_source(r: SearchResult) -> None:
            if r.url not in sources_seen:
                sources_seen.add(r.url)
                sources.append({"title": r.title or r.domain, "url": r.url})

        # 1. Freshly fetched content first. Fresh/live evidence should lead the prompt.
        for result, content in zip(top[:max_pages], fetched):
            if isinstance(content, Exception) or not content:
                content = result.snippet
            relevance_blob = f"{result.title} {result.snippet} {result.domain} {content or ''}"
            if (
                _should_gate_relevance(question)
                and _relevance_score_for_text(question, relevance_blob) <= 0
            ):
                continue
            if content and result.url not in sources_seen:
                _add_source(result)
                label = f"{result.title or result.domain} | {_source_kind(result.domain, result.title)}"
                context_parts.append(
                    f"**[{label}]({result.url})**\n{content[:2200]}"
                )

        # 2. Vector-similarity retrieval from cache supplements fresh crawls.
        if self._cache and self._embed_fn:
            q_emb = await self._embed(question)
            if q_emb:
                cached = await self._cache.retrieve_similar(
                    q_emb, top_k=self._top_k_chunks // 2
                )
                for row in cached:
                    if _should_gate_relevance(question) and not _cache_row_is_relevant(row, question):
                        logger.debug(
                            "Skipping cached web chunk as off-topic for %r: %s",
                            question,
                            row.get("url", ""),
                        )
                        continue
                    if row["url"] not in sources_seen:
                        sources_seen.add(row["url"])
                        sources.append({"title": row["title"] or row["domain"], "url": row["url"]})
                    context_parts.append(
                        f"**[{row['title'] or row['domain']} | cached]({row['url']})**\n"
                        f"{row['chunk_text']}"
                    )

        # 3. Remaining snippets to fill remaining slots
        for result in top[max_pages:]:
            if len(context_parts) >= self._top_k_chunks:
                break
            if (
                _should_gate_relevance(question)
                and not _search_result_is_relevant(result, question)
            ):
                continue
            if result.snippet and result.url not in sources_seen:
                _add_source(result)
                label = f"{result.title or result.domain} | snippet"
                context_parts.append(
                    f"**[{label}]({result.url})**\n{result.snippet}"
                )

        if not context_parts:
            return ResearchResult(question=question, context="", sources=[], search_queries=queries, attempted=True)

        return ResearchResult(
            question=question,
            context=self._build_context(context_parts, user_location=location, queries=queries),
            sources=sources[:12],
            chunks_used=len(context_parts),
            search_queries=queries,
            attempted=True,
        )

    def _build_context(self, parts: List[str], user_location: str = "", queries: List[str] | None = None) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        location_rule = ""
        if user_location:
            _CURRENCY_MAP = {
                "malaysia": "MYR (Ringgit)",
                "singapore": "SGD",
                "indonesia": "IDR (Rupiah)",
                "thailand": "THB (Baht)",
                "philippines": "PHP (Peso)",
                "vietnam": "VND (Dong)",
                "china": "CNY (Yuan)",
                "hong kong": "HKD",
                "taiwan": "TWD",
                "japan": "JPY (Yen)",
                "south korea": "KRW (Won)",
                "united states": "USD",
                "australia": "AUD",
                "united kingdom": "GBP",
            }
            loc_lower = user_location.lower()
            currency = next(
                (v for k, v in _CURRENCY_MAP.items() if k in loc_lower), None
            )
            currency_hint = f" Use {currency} as the primary currency." if currency else ""
            location_rule = (
                f"USER LOCATION: {user_location}.{currency_hint} "
                "Localize all prices, rates, and business context to the user's region.\n"
            )

        # Strong directive header — tells Gemma exactly what to do before any web content
        query_line = ""
        if queries:
            query_line = "SEARCH QUERIES USED: " + " | ".join(queries[:4]) + "\n"
        header = (
            f"=== WEB SEARCH RESULTS ({today}) ===\n"
            f"MODE: 事实检索模式 / live-source grounded answer.\n"
            f"{query_line}"
            f"INSTRUCTION: You are a research assistant. "
            f"The search results below were retrieved to answer the user's question. "
            f"Your job is to read them and write a direct, complete answer in the SAME LANGUAGE as the user's message.\n"
            f"{location_rule}"
            f"DO NOT ask for clarification. DO NOT say you need more context. "
            f"DO NOT output phrases like 'Since you have not provided'. "
            f"Just answer the question using the sources.\n"
            f"=== SOURCES BEGIN ===\n\n"
        )

        body = "\n\n---\n\n".join(parts)

        footer = (
            "\n\n=== SOURCES END ===\n"
            "NOW answer the user's question above. Rules:\n"
            "- Cite every fact inline as [Source Title](URL) immediately after the claim.\n"
            "- Prefer fresh/live source content over cached snippets when they disagree.\n"
            "- For current prices, laws, schedules, policies, company facts, and recommendations, do not answer beyond what the sources support.\n"
            "- Use bullet points, bold headers, tables where it improves clarity.\n"
            "- If a fact is not found in any source, say so — never fabricate.\n"
        )
        return header + body + footer

    def _build_no_sources_context(self, question: str, reason: str = "", user_location: str = "") -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        location = f"USER LOCATION: {user_location}\n" if user_location else ""
        return (
            f"=== WEB SEARCH RESULTS ({today}) ===\n"
            "MODE: 事实检索模式 / live-source grounded answer.\n"
            "WEB SEARCH ATTEMPTED: No reliable live sources were retrieved by the search layer.\n"
            f"{location}"
            f"SEARCH FAILURE REASON: {reason or 'No usable source content was returned.'}\n\n"
            "INSTRUCTION: The user asked with web search enabled, but you do not have reliable live source content. "
            "Do not invent citations, prices, policies, dates, schedules, company facts, or current claims. "
            "Reply in the same language as the user. Briefly state that live search returned no usable sources, "
            "then give either a concise general-knowledge answer with uncertainty or a practical next step.\n"
            f"USER QUESTION: {question}\n"
        )


# ── module-level convenience wrappers ─────────────────────────────────────────

_default_researcher: Optional[WebResearcher] = None

def _get_researcher() -> WebResearcher:
    global _default_researcher
    if _default_researcher is None:
        _default_researcher = WebResearcher()
    return _default_researcher


async def web_search(query: str, max_results: int = 8) -> List[SearchResult]:
    return await _get_researcher().web_search(query, max_results=max_results)


async def web_fetch(url: str, query: str = "") -> Optional[WebPage]:
    return await _get_researcher().web_fetch(url, query=query)


async def web_extract(url: str, query: str = "") -> Optional[str]:
    return await _get_researcher().web_extract(url, query=query)


async def web_research(
    question: str,
    max_results: int = 8,
    max_pages: int = 4,
) -> ResearchResult:
    return await _get_researcher().web_research(
        question, max_results=max_results, max_pages=max_pages
    )
