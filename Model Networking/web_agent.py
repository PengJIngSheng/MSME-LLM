import os
import re
import json
import datetime
from typing import TypedDict, List, Dict, Optional, Tuple

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
        '最新价', '实时', '当前汇率', '定义', '是什么',
        # English
        'exchange rate', 'current rate', 'price of', 'how much', 'how many',
        'what is', 'who is', 'when was', 'where is', 'definition of',
        'stock price', 'conversion rate', 'spot rate', 'usd to', 'myr to',
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
                primary   = user_question[:120]
                secondary = user_question[:80] + " detailed explanation"
                search_queries = [primary, secondary]

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
        - Factual: 2-3 queries, focused on authoritative/official sources with recency
        - Analytical: 4-5 queries, diverse perspectives (news, expert, market, background)
        """
        if task_type == "factual":
            instructions = (
                "You are an expert search query generator.\n"
                "CRITICAL: If the user asks MULTIPLE unrelated questions, you MUST generate queries for EVERY SINGLE QUESTION.\n\n"
                "RULES:\n"
                "1. Generate 2 queries **FOR EACH DISTINCT TOPIC** asked by the user.\n"
                "2. FACTUAL FOCUS: Target official/authoritative sources (e.g., official sites, reuters) with recency signals ('2026', 'today', 'latest').\n"
                "3. ENGLISH: Translate product names, currencies, and entities to English.\n"
                "4. Output ONLY a raw JSON array of strings. No markdown, no explanations.\n\n"
                "Example for 'USD to MYR today and who is the president of US':\n"
                '["USD MYR exchange rate today latest", "USD to MYR real-time xe.com", "current president of United States 2026", "US president full name latest"]'
            )
        else:  # analytical
            instructions = (
                "You are an expert search query generator.\n"
                "CRITICAL: If the user asks MULTIPLE unrelated questions, you MUST generate queries for EVERY SINGLE QUESTION.\n\n"
                "RULES:\n"
                "1. Generate 3 queries **FOR EACH DISTINCT TOPIC** asked by the user.\n"
                "2. DIVERSITY: For each topic, target different angles (e.g., 'latest news', 'background causes', 'expert analysis').\n"
                "3. ENGLISH: Translate product names, currencies, and entities to English.\n"
                "4. Output ONLY a raw JSON array of strings. No markdown, no explanations.\n\n"
                "Example for 'US-Iran conflict and also Apple stock':\n"
                '["US Iran war latest news 2026", "US Iran conflict impact causes", "Iran US retaliation expert analysis", "Apple stock AAPL price today", "Apple recent market analysis 2026"]'
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
                return [str(q).strip() for q in queries if str(q).strip()][:8]
        except Exception:
            pass
        return []

    def _bing_search(self, query: str, max_results: int = 8) -> List[Dict]:
        """Bing search is disabled — only DuckDuckGo is used."""
        return []

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

        # Mode-specific search parameters
        if task_type == "factual":
            timelimit  = "d"   # today's data for maximum recency
        else:  # analytical
            timelimit  = "w"   # past week for news/events

        _max_per_q = _cfg.max_results_per_query if _cfg else 12
        _max_total = _cfg.max_results_total      if _cfg else 10

        _snip_len  = _cfg.snippet_length if _cfg else 250
        _blacklist = self._BLACKLIST_DOMAINS
        print(f"  ⏱️  timelimit={timelimit} | max_per_query={_max_per_q} | max_total={_max_total}")

        for query in queries:
            try:
                ddgs = DDGS()
                raw_results = ddgs.text(query, timelimit=timelimit, max_results=_max_per_q) or []
                # For factual mode: also try without timelimit if results are sparse
                if task_type == "factual" and len(raw_results) < 3:
                    raw_results = ddgs.text(query, max_results=_max_per_q) or []
            except Exception as e:
                print(f"  ⚠️ DDG 搜索出错: {e}")
                raw_results = []

            for r in raw_results:
                href  = r.get("href", "")
                title = r.get("title", "")
                body  = r.get("body", "")

                try:
                    from urllib.parse import urlparse
                    domain = urlparse(href).netloc.lower().replace('www.', '')
                    if any(bad in domain for bad in _blacklist):
                        continue
                except Exception:
                    pass

                if len(body.strip()) < 40:
                    continue
                if href and href in seen_urls:
                    continue
                if href:
                    seen_urls.add(href)

                # Relevance scoring: title hits × 3 (stronger signal), body hits × 1
                combined    = (title + " " + body).lower()
                title_hits  = sum(3 for kw in query_keywords if kw in title.lower())
                body_hits   = sum(1 for kw in query_keywords if kw in combined)
                price_bonus = sum(1 for pt in ['$', 'usd', 'rm ', 'myr', '£', '€',
                                               'price', 'cost', 'buy', 'shop', 'store',
                                               'order', 'stock', 'shipping', 'freight']
                                  if pt in combined)
                score = title_hits + body_hits + price_bonus
                candidate_results.append((score, title, body, href))

        # Sort by score, keep top N
        candidate_results.sort(key=lambda x: x[0], reverse=True)
        top_results = candidate_results[:_max_total]

        combined_results: List[str] = []
        sources: List[Dict[str, str]] = []
        for idx, (score, title, body, href) in enumerate(top_results, start=1):
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
                    f"MODE: 事实检索模式 (Factual / Deterministic)\n"
                    f"GOAL: Provide definitive, authoritative, and structured answers to EVERY question the user asked.\n\n"
                    f"OUTPUT RULES:\n"
                    f"1. STRUCTURE: Use bold numbered headers for each distinct topic the user asked about.\n"
                    f"2. VISUALS: Use bullet points under each header to organize data (e.g. '• Current Rate:', '• Fluctuation Range:', '• Trend:').\n"
                    f"3. EXACT DATA: Give the exact numbers/prices immediately.\n"
                    f"4. CITATIONS: Cite the source inline using markdown links: [Source Name](url).\n"
                    f"5. CONTEXT: Add 1-2 sentences of context (e.g., changes from yesterday, why it changed).\n"
                    f"6. NO REFUSALS: If data exists in the search results, synthesize and state it directly."
                )
            else:  # analytical
                mode_instructions = (
                    f"MODE: 深度挖掘模式 (Analytical / Multi-Dimensional)\n"
                    f"GOAL: Produce a comprehensive, multi-perspective analysis — matching a professional research brief.\n\n"
                    f"OUTPUT RULES:\n"
                    f"1. STRUCTURE: Use bold numbered sections for each major aspect (Background, Current Status, Impact, Outlook).\n"
                    f"2. VISUALS: Liberally use bullet points and **bold text** to highlight key names, dates, and concepts.\n"
                    f"3. MULTI-SOURCE: Reference multiple different viewpoints (e.g., US side vs Iran side).\n"
                    f"4. DEPTH: Add YOUR OWN synthesis and analysis sentences beyond just quoting the facts.\n"
                    f"5. DATA: Include specific numbers, dates, names, and percentages from the sources.\n"
                    f"6. CITATIONS: Always use inline links [Source Name](url).\n"
                    f"7. NO REFUSALS: Synthesize all available information without hesitation."
                )

            system_content = (
                f"Date/time: {current_time}\n"
                f"User Location: {user_loc} (Auto-convert currencies, units, and contexts to this region proactively)\n\n"
                f"══ WEB SEARCH RESULTS ══\n"
                f"{state['search_results']}\n"
                f"══ END RESULTS ══\n\n"
                f"{think_block}"
                f"ROLE: You are a professional research journalist and analyst.\n"
                f"QUALITY BENCHMARK: Match or exceed Google Gemini 2.0 depth and clarity.\n\n"
                f"{mode_instructions}\n\n"
                f"CITATION FORMAT (CRITICAL WARNING):\n"
                f"You MUST use inline markdown links with the actual title and URL. NO EXCEPTIONS.\n"
                f"✅ CORRECT (EN): 'According to [Reuters News](https://reuters.com/...), the rate is 4.225'\n"
                f"✅ CORRECT (ZH): '根据 [Reuters News](https://reuters.com/...), 汇率为 4.225'\n"
                f"❌ FORBIDDEN: 'According to [1]'\n"
                f"❌ FORBIDDEN: '... rate is 4.225 [1][2]'\n"
                f"NEVER use standalone bracketed numbers like [1] or [2] for citations. ALWAYS use the full markdown link.\n\n"
                f"LANGUAGE: Reply ENTIRELY in {user_lang}. Do not mix languages."
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
