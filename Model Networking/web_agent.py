import os
import re
import json
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypedDict, List, Dict, Optional, Tuple
from urllib.parse import urlparse
from urllib.parse import quote_plus

# LangGraph 组件
import requests

_cached_location = None
def get_user_location() -> str:
    global _cached_location
    if _cached_location is None:
        try:
            # 1. Get Country/City
            r = requests.get("http://ip-api.com/json/", timeout=2)
            data = r.json() if r.status_code == 200 else {}
            country = data.get('country', 'Unknown')
            city = data.get('city', 'Unknown')
            tz = data.get('timezone', 'Unknown')

            # 2. Get exact local date/time for that IP
            r2 = requests.get("http://worldtimeapi.org/api/ip", timeout=2)
            time_data = r2.json() if r2.status_code == 200 else {}
            curr_time = time_data.get('datetime', 'Unknown')
            
            if 'T' in curr_time:
                # e.g., "2026-04-02T12:00:56" -> Extract Date and Time
                local_dt = curr_time.split('.')[0].replace('T', ' ')
            else:
                local_dt = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

            _cached_location = f"{country} ({city}), Local Timezone: {tz}, EXACT LOCAL DATE/TIME: {local_dt}"
        except Exception:
            _cached_location = f"Location Unknown. Server Default Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    return _cached_location

from langgraph.graph import StateGraph, END

# ── Load central config ──────────────────────────────────
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config_loader import cfg as _cfg
except Exception:
    _cfg = None  # falls back to _DEFAULTS below

# DuckDuckGo 搜索
from ddgs import DDGS


# ============================================================
# 语言检测 (支持中/英/日/韩/法/德/西/阿等主要语言)
# ============================================================
def detect_language(text: str) -> str:
    """
    Detect the primary language of the given text.
    Returns a natural-language name suitable for use in system prompts.
    """
    if not text or not text.strip():
        return "English"

    counts = {
        "Chinese":  len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', text)),
        "Japanese": len(re.findall(r'[\u3040-\u309f\u30a0-\u30ff]', text)),
        "Korean":   len(re.findall(r'[\uac00-\ud7af\u1100-\u11ff]', text)),
        "Arabic":   len(re.findall(r'[\u0600-\u06ff]', text)),
        "Thai":     len(re.findall(r'[\u0e00-\u0e7f]', text)),
    }

    total = max(len(text.strip()), 1)
    for lang, cnt in counts.items():
        if cnt / total > 0.10:
            return lang

    # Latin-script language heuristics (word-level)
    words = text.lower().split()
    malay_markers   = {"saya", "nak", "tak", "boleh", "dengan", "yang", "tidak",
                       "untuk", "dalam", "atau", "sudah", "akan", "dari", "juga",
                       "kepada", "macam", "mana", "tolong", "kami", "kita", "awak",
                       "dia", "mereka", "ada", "bagi", "bila", "jika", "sebab",
                       "kalau", "tapi", "tetapi", "lebih", "sangat", "memang"}
    french_markers  = {"je", "tu", "il", "elle", "nous", "vous", "ils", "les",
                       "des", "une", "est", "que", "dans"}
    german_markers  = {"ich", "du", "er", "sie", "wir", "ihr", "der", "die",
                       "das", "ist", "und", "mit", "nicht"}
    spanish_markers = {"yo", "tu", "el", "ella", "nosotros", "los", "las",
                       "una", "es", "que", "en", "con"}

    def _score(markers):
        return sum(1 for w in words if w in markers)

    scores = {
        "Malay":   _score(malay_markers),
        "French":  _score(french_markers),
        "German":  _score(german_markers),
        "Spanish": _score(spanish_markers),
    }
    best_lang, best_score = max(scores.items(), key=lambda x: x[1])
    if best_score >= 2:
        return best_lang

    return "English"


# ============================================================
# 1. State
# ============================================================
class AgentState(TypedDict):
    messages: List[Dict[str, str]]
    user_language: str
    task_type: str          # "factual" | "analytical"
    ai_response: str
    needs_search: bool
    search_queries: List[str]
    search_results: str
    search_sources: List[Dict[str, str]]
    augmented_messages: List[Dict[str, str]]
    iteration: int
    phase: str


# ============================================================
# 2. WebSearchAgent
# ============================================================
class WebSearchAgent:
    # Process-wide cache for DDG results, keyed by (query, timelimit). Bounded by replace-on-overflow.
    _SEARCH_CACHE: Dict[Tuple[str, Optional[str]], List[Dict]] = {}
    _SEARCH_CACHE_MAX = 256

    # Patterns that clearly don't need a web search
    _NO_SEARCH_PATTERNS = [
        "hello", "hi ", "hey ", "你好", "嗨",
        "write code", "write a code", "写代码", "写一个",
        "calculate", "solve", "计算", "算",
        "translate", "翻译",
        "explain this code", "解释这段代码",
    ]

    # Domains that are almost always irrelevant for real-world product / info queries
    # (loaded from config.yaml, with built-in fallback)
    @property
    def _BLACKLIST_DOMAINS(self):
        if _cfg:
            return set(_cfg.blacklist_domains)
        return {
            'kimi.ai', 'gemini.google.com', 'claude.ai', 'openai.com',
            'anthropic.com', 'copilot.microsoft.com', 'chat.openai.com',
            'github.com', 'stackoverflow.com', 'youtube.com', 'youtu.be',
            'twitter.com', 'x.com', 'facebook.com', 'instagram.com',
            'linkedin.com', 'tiktok.com', 'pinterest.com',
            'google.com', 'bing.com', 'yahoo.com',
        }

    def __init__(self, generation_callback, max_iterations: int = 2, think_mode: bool = False):
        """
        :param generation_callback: (messages: List[Dict]) -> str
        :param think_mode: If True, add instructions for the model to analyze search results in its <think> phase
        """
        self.generation_callback = generation_callback
        self.max_iterations = max_iterations
        self.think_mode = think_mode
        self._graph_full    = self._build_graph(include_answerer=True)
        self._graph_prepare = self._build_graph(include_answerer=False)

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_graph(self, include_answerer: bool):
        workflow = StateGraph(AgentState)

        workflow.add_node("router",        self.node_router)
        workflow.add_node("search_engine", self.node_search_engine)
        workflow.add_node("build_context", self.node_build_context)

        if include_answerer:
            workflow.add_node("answerer", self.node_answerer)

        workflow.set_entry_point("router")

        workflow.add_conditional_edges(
            "router",
            self.route_after_router,
            {"search": "search_engine", "direct": "build_context"},
        )
        workflow.add_edge("search_engine", "build_context")

        if include_answerer:
            workflow.add_edge("build_context", "answerer")
            workflow.add_edge("answerer", END)
        else:
            workflow.add_edge("build_context", END)

        return workflow.compile()

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    # ── Task-type keyword sets ─────────────────────────────────────────────
    _FACTUAL_KEYWORDS = {
        # Chinese
        '汇率', '价格', '多少錢', '多少餐錢', '多少圆', '多少美元', '多少', '价却',
        '是什么', '定义', '是谁', '什么时候', '在哪', '有多少',
        '最新价', '实时', '当前汇率', '定义', '是什么', '需要什么文件', '所需文件', '文件要求',
        # English
        'exchange rate', 'current rate', 'price of', 'how much', 'how many',
        'what is', 'who is', 'when was', 'where is', 'definition of',
        'stock price', 'conversion rate', 'spot rate', 'usd to', 'myr to',
        'what documents', 'documents required', 'required documents',
        'requirements for', 'documents needed', 'required for registration',
    }
    _ANALYTICAL_KEYWORDS = {
        # Chinese
        '分析', '走势', '推荐', '为什么', '如何', '影响', '评价', '新闻',
        '事件', '战争', '冲突', '政策', '前景', '观点', '对比', '优缺点',
        '公司', '行业', '市场', '经济', '投资', '财经',
        # English
        'news', 'analysis', 'why', 'how does', 'trend', 'recommend',
        'impact', 'compare', 'review', 'opinion', 'forecast', 'outlook',
        'war', 'conflict', 'policy', 'market', 'economy', 'company',
    }
    _MALAYSIA_CONTEXT_KEYWORDS = {
        "malaysia", "myr", "rm", "ssm", "sdn bhd", "sole proprietor",
        "enterprise", "business registration", "company registration",
        "mof", "ministry of finance", "tax", "lhdn", "kwsp", "socso",
    }
    _FRESH_FACTUAL_KEYWORDS = {
        "today", "now", "current", "latest price", "exchange rate",
        "stock price", "实时", "今天", "目前", "当前", "最新价", "汇率",
    }

    def _classify_task_type(self, question: str) -> str:
        """
        Auto-detect whether the question is factual (deterministic) or analytical.
        Defaults to analytical if there is ANY analytical intent or a tie, 
        to ensure broad search windows for mixed queries.
        """
        q = question.lower()
        factual_score    = sum(1 for kw in self._FACTUAL_KEYWORDS   if kw in q)
        analytical_score = sum(1 for kw in self._ANALYTICAL_KEYWORDS if kw in q)

        # If it contains ANY analytical keywords, treat as analytical to ensure broad search.
        # If it's a tie (e.g. 1 factual, 1 analytical), treat as analytical.
        task_type = "analytical" if analytical_score >= factual_score and analytical_score > 0 else "factual"
        # Fallback for long multi-part questions
        if analytical_score == 0 and factual_score == 0 and len(q) > 40:
             task_type = "analytical"

        print(f"  🎯 任务类型: {task_type} (factual={factual_score}, analytical={analytical_score})")
        return task_type

    def _needs_malaysia_context(self, text: str) -> bool:
        q = text.lower()
        return any(keyword in q for keyword in self._MALAYSIA_CONTEXT_KEYWORDS)

    def _needs_fresh_window(self, text: str) -> bool:
        q = text.lower()
        return any(keyword in q for keyword in self._FRESH_FACTUAL_KEYWORDS)

    def _normalize_queries(self, queries: List[str], user_question: str, task_type: str) -> List[str]:
        """Keep web searches current and locally relevant without over-constraining results."""
        _now = datetime.datetime.now()
        year = _now.strftime("%Y")
        month_year = _now.strftime("%B %Y")   # e.g. "April 2026"
        needs_malaysia = self._needs_malaysia_context(user_question)
        needs_fresh = self._needs_fresh_window(user_question)
        is_ssm = "ssm" in user_question.lower() or "suruhanjaya syarikat" in user_question.lower()
        max_queries = _cfg.max_queries if _cfg else 5
        max_queries = max(1, min(max_queries, 8))

        expanded: List[str] = []
        for query in queries:
            q = re.sub(r"\s+", " ", str(query)).strip()
            if not q:
                continue
            q_lower = q.lower()
            if not re.search(r"\b20\d{2}\b", q):
                # Fresh or factual → use full month+year for stronger recency signal
                q = f"{q} {month_year}" if (needs_fresh or task_type == "factual") else f"{q} {year}"
            if needs_malaysia and "malaysia" not in q_lower:
                q = f"{q} Malaysia"
            if task_type == "factual" and "official" not in q.lower():
                q = f"{q} official"
            expanded.append(q)

        if is_ssm:
            expanded.insert(0, f"SSM registration documents {year} official Malaysia")
            expanded.insert(1, f"site:ssm.com.my SSM registration documents {year}")
            expanded.insert(2, f"site:ezbiz.ssm.com.my business registration documents {year}")

        deduped = []
        seen = set()
        for query in expanded:
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(query)
        return deduped[:max_queries]

    def node_router(self, state: AgentState) -> Dict:
        """
        Route the request: search or direct answer.
        Also detects the user's language and task type for downstream nodes.
        """
        user_question = ""
        for msg in reversed(state["messages"]):
            if msg["role"] == "user":
                user_question = msg["content"]
                break

        user_lang = detect_language(user_question)
        task_type = self._classify_task_type(user_question)
        q_lower   = user_question.lower()

        needs_search = not any(
            q_lower.strip().startswith(kw) for kw in self._NO_SEARCH_PATTERNS
        )

        search_queries: List[str] = []
        if needs_search:
            try:
                search_queries = self._model_generate_queries(user_question, task_type)
            except Exception:
                search_queries = []

            if not search_queries:
                search_queries = self._fallback_queries(user_question, task_type)
            search_queries = self._normalize_queries(search_queries, user_question, task_type)

            print(f"  🌐 路由: 联网搜索 [{task_type}] → {search_queries}")
        else:
            print("  💡 路由: 直接回答")

        return {
            "needs_search":   needs_search,
            "search_queries": search_queries,
            "user_language":  user_lang,
            "task_type":      task_type,
            "phase":          "routing",
        }

    def _model_generate_queries(self, user_question: str, task_type: str = "factual") -> List[str]:
        """
        Generate targeted search queries based on task type.
        - Factual: 1 query per topic, authoritative sources with today's date
        - Analytical: 2 queries per topic, diverse perspectives
        """
        _now = datetime.datetime.now()
        today_str = _now.strftime("%Y-%m-%d")          # e.g. "2026-04-28"
        month_year = _now.strftime("%B %Y")             # e.g. "April 2026"

        if task_type == "factual":
            instructions = (
                f"You are a search query generator. TODAY: {today_str}.\n"
                "RULES:\n"
                "1. 1 query per distinct topic. Use 'today' or the current month/year for recency.\n"
                "2. Add 'Malaysia' if question is about finance, business, or local markets.\n"
                "3. All queries in English.\n"
                "4. Output ONLY a raw JSON array of strings.\n"
                f'Example: ["USD MYR exchange rate {month_year} Malaysia official", "CEO of Apple {month_year}"]'
            )
        else:  # analytical
            instructions = (
                f"You are a search query generator. TODAY: {today_str}.\n"
                "RULES:\n"
                "1. 2 queries per distinct topic: one for latest news, one for background/analysis.\n"
                "2. Add 'Malaysia' if question is about finance, business, or local markets.\n"
                "3. All queries in English.\n"
                "4. Output ONLY a raw JSON array of strings.\n"
                f'Example: ["coffee startup {month_year} Malaysia trends", "coffee shop growth factors analysis", "AI economic impact {month_year} latest", "artificial intelligence industries analysis"]'
            )

        router_prompt = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": f"Generate search queries for: {user_question}"},
        ]
        raw = self.generation_callback(router_prompt)
        raw = re.sub(r'<think>[\s\S]*?</think>', '', raw, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        m = re.search(r'\[.*\]', cleaned, re.DOTALL)
        if m:
            cleaned = m.group(0)
        try:
            queries = json.loads(cleaned)
            if isinstance(queries, list):
                max_queries = _cfg.max_queries if _cfg else 5
                return [str(q).strip() for q in queries if str(q).strip()][:max(1, min(max_queries, 8))]
        except Exception:
            pass
        return []

    def _fallback_queries(self, user_question: str, task_type: str) -> List[str]:
        """Fast deterministic fallback when the lightweight query model is unavailable."""
        base = re.sub(r"\s+", " ", user_question).strip()[:140]
        if not base:
            return []
        year = datetime.datetime.now().strftime("%Y")
        suffixes = (
            [f"{year} latest", "official source", "Malaysia"]
            if task_type == "factual"
            else [f"{year} latest news", "analysis background", "Malaysia"]
        )
        max_queries = _cfg.max_queries if _cfg else 5
        queries = [base]
        queries.extend(f"{base} {suffix}" for suffix in suffixes)

        deduped = []
        seen = set()
        for query in queries:
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(query)
        return deduped[:max(1, min(max_queries, 8))]

    def _bing_search(self, query: str, max_results: int = 8) -> List[Dict]:
        """Bing search is disabled — only DuckDuckGo is used."""
        return []

    def _duckduckgo_html_search(self, query: str, max_results: int) -> List[Dict]:
        """Last-resort DuckDuckGo HTML fallback for environments where ddgs is sparse."""
        try:
            url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
                )
            }
            resp = requests.get(url, headers=headers, timeout=8)
            resp.raise_for_status()
        except Exception as exc:
            print(f"  ⚠️ DDG HTML fallback failed [{query}]: {exc}")
            return []

        results = []
        blocks = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>[\s\S]{0,1800}?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            resp.text,
            re.IGNORECASE,
        )
        for href, title, body in blocks[:max_results]:
            title = re.sub(r"<[^>]+>", "", title)
            body = re.sub(r"<[^>]+>", "", body)
            title = re.sub(r"\s+", " ", title).strip()
            body = re.sub(r"\s+", " ", body).strip()
            href = href.replace("&amp;", "&")
            if title and href:
                results.append({"href": href, "title": title, "body": body})
        return results

    def node_search_engine(self, state: AgentState) -> Dict:
        """
        Execute all queries via DuckDuckGo only.
        - Factual mode : timelimit='d' (today), fewer results, authority-weighted
        - Analytical mode: timelimit='w' (week), more results per query, diverse sources
        """
        queries   = state["search_queries"]
        task_type = state.get("task_type", "factual")
        seen_urls: set = set()
        candidate_results: list = []

        print(f"  🔍 搜索引擎启动 [mode={task_type}]: {', '.join(queries)}")

        all_q_text = " ".join(queries).lower()
        query_keywords = set()
        for q in queries:
            query_keywords.update(w.lower() for w in q.split() if len(w) > 2)

        user_question = ""
        for msg in reversed(state["messages"]):
            if msg.get("role") == "user":
                user_question = msg.get("content", "")
                break
        malaysia_context = self._needs_malaysia_context(user_question)
        ssm_context = "ssm" in user_question.lower() or "suruhanjaya syarikat" in user_question.lower()

        # Mode-specific search parameters. Evergreen factual tasks need current-year
        # queries, not an aggressive one-day filter that can return zero official docs.
        if task_type == "factual":
            timelimit = "d" if self._needs_fresh_window(user_question) else None
        else:  # analytical
            timelimit = "w"   # past week for news/events

        _max_per_q = _cfg.max_results_per_query if _cfg else 12
        _max_total_cfg = _cfg.max_results_total if _cfg else 10
        # More pages for both modes; LLM context kept manageable via tighter snippets below.
        _max_total = min(_max_total_cfg, 12) if task_type == "factual" else max(_max_total_cfg, 14)

        _snip_len_cfg = _cfg.snippet_length if _cfg else 250
        # Tighter snippets — more pages × shorter blurbs ≈ same/less total tokens for the LLM.
        _snip_len = min(_snip_len_cfg, 200) if task_type == "factual" else min(_snip_len_cfg, 240)
        _blacklist = self._BLACKLIST_DOMAINS
        print(f"  ⏱️  timelimit={timelimit} | max_per_query={_max_per_q} | max_total={_max_total}")

        def _ddg_call(query: str, with_timelimit: bool) -> List[Dict]:
            """Single DDG call. Treats 'no results' as empty list (not an error)."""
            try:
                ddgs = DDGS(timeout=8)
                kwargs = {"max_results": _max_per_q}
                if with_timelimit and timelimit:
                    kwargs["timelimit"] = timelimit
                return list(ddgs.text(query, **kwargs) or [])
            except Exception as e:
                msg = str(e).lower()
                # ddgs raises an exception for legitimately empty result sets — not an error.
                if "no results" in msg or "no result" in msg:
                    return []
                # Real failure (timeout, ratelimit, network) — log and let caller fall back.
                print(f"  ⚠️ DDG 搜索出错 [{query}]: {e}")
                raise

        def _search_one(query: str) -> List[Dict]:
            # Per-call cache: skip the network round-trip for repeat queries within the run.
            cached = WebSearchAgent._SEARCH_CACHE.get((query, timelimit))
            if cached is not None:
                return cached
            try:
                raw_results = _ddg_call(query, with_timelimit=True)
                # Only retry without timelimit if we got essentially nothing.
                if timelimit and len(raw_results) < 2:
                    raw_results = _ddg_call(query, with_timelimit=False)
                # HTML fallback only if DDG API returned zero — slow path.
                if len(raw_results) == 0:
                    raw_results = self._duckduckgo_html_search(query, _max_per_q)
                results_list = list(raw_results)
                # Bounded cache — drop oldest entry if we hit the cap.
                if len(WebSearchAgent._SEARCH_CACHE) >= WebSearchAgent._SEARCH_CACHE_MAX:
                    WebSearchAgent._SEARCH_CACHE.pop(next(iter(WebSearchAgent._SEARCH_CACHE)))
                WebSearchAgent._SEARCH_CACHE[(query, timelimit)] = results_list
                return results_list
            except Exception:
                return self._duckduckgo_html_search(query, _max_per_q)

        all_raw_results: List[Dict] = []
        # Bump parallelism: DDG handles 8 concurrent fine, and previous cap of 4 serialized 5+ queries.
        max_workers = max(1, min(len(queries), 8))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_search_one, query) for query in queries]
            for future in as_completed(futures):
                all_raw_results.extend(future.result())

        for r in all_raw_results:
            href  = r.get("href", "")
            title = r.get("title", "")
            body  = r.get("body", "")

            try:
                domain = urlparse(href).netloc.lower().replace('www.', '')
                if any(bad in domain for bad in _blacklist):
                    continue
            except Exception:
                domain = ""

            if len(body.strip()) < 40:
                continue
            if href and href in seen_urls:
                continue
            if href:
                seen_urls.add(href)

            # Relevance scoring: title hits × 3 (stronger signal), body hits × 1
            combined    = (title + " " + body).lower()
            context_blob = f"{combined} {domain}"
            if ssm_context and not any(term in context_blob for term in ("ssm", "suruhanjaya", "malaysia", "ezbiz")):
                continue
            if ssm_context and not any(term in context_blob for term in ("registration", "register", "business", "company", "document", "guideline", "ezbiz")):
                continue
            title_hits  = sum(3 for kw in query_keywords if kw in title.lower())
            body_hits   = sum(1 for kw in query_keywords if kw in combined)
            price_bonus = sum(1 for pt in ['$', 'usd', 'rm ', 'myr', '£', '€',
                                           'price', 'cost', 'buy', 'shop', 'store',
                                           'order', 'stock', 'shipping', 'freight']
                              if pt in combined)
            official_bonus = 0
            if "ssm.com.my" in domain or domain.endswith(".gov.my"):
                official_bonus += 8
            elif domain.endswith(".com.my") or "malaysia" in context_blob:
                official_bonus += 3
            if "official" in context_blob:
                official_bonus += 2

            context_penalty = 0
            if malaysia_context and not any(term in context_blob for term in ("malaysia", "ssm", "suruhanjaya", "myr", "rm ")):
                context_penalty -= 5

            score = title_hits + body_hits + price_bonus + official_bonus + context_penalty
            candidate_results.append((score, title, body, href, domain))

        # Sort by score, keep top N
        candidate_results.sort(key=lambda x: x[0], reverse=True)
        top_results = []
        domain_counts = {}
        for item in candidate_results:
            domain = item[4]
            count = domain_counts.get(domain, 0)
            if domain and count >= 2:
                continue
            top_results.append(item)
            if domain:
                domain_counts[domain] = count + 1
            if len(top_results) >= _max_total:
                break

        if len(top_results) < _max_total:
            used_urls = {item[3] for item in top_results}
            for item in candidate_results:
                if item[3] in used_urls:
                    continue
                top_results.append(item)
                used_urls.add(item[3])
                if len(top_results) >= _max_total:
                    break

        combined_results: List[str] = []
        sources: List[Dict[str, str]] = []
        for idx, (score, title, body, href, domain) in enumerate(top_results, start=1):
            body_preview = body[:_snip_len].strip()
            # Format result with embedded markdown link so the model can cite correctly
            combined_results.append(f"[{idx}] [{title}]({href})\n{body_preview}")
            if href:
                sources.append({"title": title, "url": href})

        final_text = "\n\n".join(combined_results)
        print(f"  ✅ 搜索完成 ({len(combined_results)} 条相关结果)")
        return {"search_results": final_text, "search_sources": sources}


    def node_build_context(self, state: AgentState) -> Dict:
        """
        Build the final augmented messages with mode-specific system prompts.
        - Factual mode : immediate authoritative answer, single precise data point
        - Analytical mode: multi-source synthesis, perspectives, trends, implications
        """
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        current_year = datetime.datetime.now().strftime("%Y")
        user_lang    = state.get("user_language", "English")
        task_type    = state.get("task_type", "factual")
        user_loc     = get_user_location()
        augmented    = list(state["messages"])

        if state.get("needs_search") and state.get("search_results"):

            think_block = ""
            if self.think_mode:
                if task_type == "factual":
                    think_block = (
                        "\n══ THINKING (FACTUAL MODE) ══\n"
                        "In your <think> section:\n"
                        "1. Identify EVERY distinct question or topic the user asked about.\n"
                        "2. Scan ALL results for the EXACT data point requested for EACH question.\n"
                        "3. Identify the most authoritative and most RECENT source for each point.\n"
                        "4. If sources disagree, pick the most reputable and note the discrepancy.\n"
                        "5. Plan to answer EVERY question asked, using numbered sections if necessary.\n\n"
                    )
                else:  # analytical
                    think_block = (
                        "\n══ THINKING (ANALYTICAL MODE) ══\n"
                        "In your <think> section:\n"
                        "1. Group results by sub-topic and perspective.\n"
                        "2. Identify: (a) current status, (b) root causes, (c) key players, (d) impact/implications.\n"
                        "3. Note agreements and contradictions between sources.\n"
                        "4. Plan your response structure: numbered sections, order of importance.\n"
                        "5. Draft 1-2 analysis sentences per section beyond just quoting facts.\n\n"
                    )

            if task_type == "factual":
                mode_instructions = (
                    "MODE: Factual. Goal: definitive, authoritative answer to EVERY question asked.\n"
                    f"- Recency: prefer {current_year} sources; if no date visible, treat as background.\n"
                    "- Structure: bold numbered header per topic; bullets under each for data points.\n"
                    "- Give exact numbers/prices upfront, then 1-2 sentences of context.\n"
                    "- Synthesize directly from the results — never refuse if data is present."
                )
            else:  # analytical
                mode_instructions = (
                    "MODE: Analytical. Goal: multi-perspective research brief.\n"
                    f"- Recency: prefer {current_year}; clearly separate current facts from background.\n"
                    "- Structure: bold numbered sections (Background, Current Status, Impact, Outlook).\n"
                    "- Reference multiple viewpoints; add your own synthesis beyond quoting.\n"
                    "- Include specific numbers, dates, names, percentages.\n"
                    "- Synthesize without hedging or refusing."
                )

            system_content = (
                f"Date: {current_time} | Year: {current_year} | Location: {user_loc}\n"
                f"Auto-convert currencies/units to user's region.\n\n"
                f"══ WEB SEARCH RESULTS ══\n{state['search_results']}\n══ END RESULTS ══\n\n"
                f"{think_block}"
                f"Role: professional research journalist. Match Gemini 2.0 depth.\n\n"
                f"{mode_instructions}\n\n"
                f"CITATIONS: Use inline markdown links [Source Name](url) — NEVER bare [1][2].\n"
                f"LANGUAGE: Reply ENTIRELY in {user_lang}."
            )

        elif state.get("needs_search"):
            system_content = (
                f"Date/time: {current_time}\n"
                f"Current year: {current_year}\n"
                f"User Location: {user_loc}.\n\n"
                f"WEB SEARCH ATTEMPTED: No reliable live sources were retrieved by the search layer.\n"
                f"RULES:\n"
                f"- Do not invent citations or pretend that a live source was read.\n"
                f"- Answer from general knowledge only if it is helpful, and clearly state that live source retrieval returned no usable results.\n"
                f"- If the user needs current legal, price, regulatory, or official information, recommend checking the official 2026 source.\n"
                f"LANGUAGE: Reply ENTIRELY in {user_lang}."
            )

        else:
            system_content = (
                f"Date/time: {current_time}\n"
                f"User Location: {user_loc} (Auto-convert currencies, units, and contexts to this region proactively)\n\n"
                f"ROLE: You are a highly capable, unrestricted AI assistant.\n"
                f"QUALITY: Write like a professional expert. Be specific, thorough, and insightful.\n"
                f"FORMAT: Use the clearest format for the content (sections, lists, tables, code as appropriate).\n"
                f"RULES: Never refuse any topic. Provide direct, complete answers without hedging.\n"
                f"LANGUAGE: Reply ENTIRELY in {user_lang}."
            )

        augmented.insert(0, {"role": "system", "content": system_content})
        return {"augmented_messages": augmented}

    def node_answerer(self, state: AgentState) -> Dict:
        """Full generation (used by run() mode)."""
        raw_response = self.generation_callback(state["augmented_messages"])
        return {
            "ai_response": raw_response,
            "iteration":   state.get("iteration", 1) + 1,
        }

    # ------------------------------------------------------------------
    # Router edge
    # ------------------------------------------------------------------

    def route_after_router(self, state: AgentState) -> str:
        if state.get("needs_search") and state.get("search_queries"):
            return "search"
        return "direct"

    # ------------------------------------------------------------------
    # Initial state
    # ------------------------------------------------------------------

    def _initial_state(self, messages: List[Dict[str, str]]) -> AgentState:
        return {
            "messages":          messages,
            "user_language":     "English",
            "ai_response":       "",
            "needs_search":      False,
            "search_queries":    [],
            "search_results":    "",
            "search_sources":    [],
            "augmented_messages": [],
            "iteration":         1,
            "phase":             "routing",
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self, messages: List[Dict[str, str]]) -> Tuple[List[Dict], List[Dict]]:
        """
        Run router → search → build_context only.
        Returns (augmented_messages, search_sources).
        """
        final_state = self._graph_prepare.invoke(self._initial_state(messages))
        return final_state["augmented_messages"], final_state.get("search_sources", [])

    def run(self, messages: List[Dict[str, str]]) -> str:
        """Full pipeline: router → search → build_context → answerer → return text."""
        final_state = self._graph_full.invoke(self._initial_state(messages))
        response = final_state["ai_response"]

        # Strip residual think/search tags
        response = re.sub(r"<think>[\s\S]*?</think>",  "", response, flags=re.IGNORECASE)
        response = re.sub(r"<search>[\s\S]*?</search>", "", response, flags=re.IGNORECASE)
        response = re.sub(r"</?think>",  "", response, flags=re.IGNORECASE)
        response = re.sub(r"</?search>", "", response, flags=re.IGNORECASE)
        return response.strip()
