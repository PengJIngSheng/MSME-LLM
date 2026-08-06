"""Script-aware text helpers shared by the chat pipeline.

bisnes.ai serves English, Chinese, and Malay. Chinese is written without
spaces and packs far more meaning per character than Latin text, so any
"how big is this message" decision has to be script-aware. Measuring with
`len(text)` or `text.count(" ")` silently classifies substantial Chinese
questions as trivial.

Kept as a standalone module (rather than living inside server.py) so it can
be imported and tested without booting MongoDB, Ollama, and pgvector.
"""

import re

# CJK ideographs plus Japanese kana. These scripts have no word delimiters.
_CJK_CHAR_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ヿ]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")

# Pure pleasantries in the three supported UI languages. Matching these
# explicitly is far safer than a length cutoff: "什么是 SST?" is only 8
# characters but is a real question that deserves a real answer.
_GREETING_RE = re.compile(
    r"^\s*(?:"
    r"hi|hello|hey|yo|hiya|thanks|thank you|thx|ty|ok|okay|k|kk|"
    r"got it|noted|nice|cool|great|bye|goodbye|good (?:morning|afternoon|evening|night)|"
    r"你好|您好|嗨|哈啰|哈喽|谢谢|多谢|感谢|好的|好|行|嗯|收到|明白|再见|拜拜|早上好|晚上好|"
    r"hai|helo|apa khabar|terima kasih|baik|selamat (?:pagi|petang|malam|tengah hari)"
    r")\s*[!?.,~，。！？、]*\s*$",
    re.IGNORECASE,
)

# A message at or below this many units is treated as a short question:
# light history, tighter context window, smaller output budget.
SIMPLE_QUERY_MAX_UNITS = 12
SIMPLE_QUERY_MAX_CHARS = 90


def message_units(text: str) -> int:
    """Approximate the 'word count' of a message across Latin and CJK scripts.

    Each CJK character counts as one unit (roughly one word); Latin and Malay
    text is counted by whitespace-delimited tokens. A plain `text.count(" ")`
    is always 0 for Chinese, which previously made every Chinese question --
    however substantial -- look trivial and silently skip web search and RAG.
    """
    t = (text or "").strip()
    if not t:
        return 0
    return len(_CJK_CHAR_RE.findall(t)) + len(_LATIN_WORD_RE.findall(t))


def is_greeting(text: str) -> bool:
    """True only for pure pleasantries carrying no informational request."""
    return bool(_GREETING_RE.match((text or "").strip()))


# ── Supported reply languages ────────────────────────────────────────────────
#
# The product ships English and Bahasa Melayu only. Chinese was retired from
# the UI, the locale files, and the prompt templates.
#
# Detection still has to cope with Chinese *input* -- a user can type anything
# into the box, and the sizing helpers above must keep measuring CJK correctly
# so a Chinese question is not mistaken for a trivial one. What changed is the
# reply language: anything outside the supported set resolves to English.
#
# Resolving here, at the single point where a detected language becomes a reply
# language, is what makes the removal complete. The alternative was editing ~49
# `== "Chinese"` / `== "zh"` branches spread over four modules; those branches
# are now simply unreachable.
SUPPORTED_REPLY_LANGUAGES = ("English", "Malay")
DEFAULT_REPLY_LANGUAGE = "English"


def resolve_reply_language(detected: str) -> str:
    """Map a detected input language onto a supported reply language."""
    return detected if detected in SUPPORTED_REPLY_LANGUAGES else DEFAULT_REPLY_LANGUAGE


def resolve_reply_lang_code(detected_code: str) -> str:
    """Same mapping for modules that use two-letter codes ('en' / 'ms')."""
    code = (detected_code or "").strip().lower()
    return code if code in ("en", "ms") else "en"


# Questions whose correct answer is a specific date, rate, amount, or threshold
# set by a Malaysian authority. These must never be answered from model memory:
# the figures change yearly, and a confidently wrong filing deadline costs the
# user a penalty.
#
# The trigger is deliberately narrow -- an authority or an official instrument
# must be named. "What is a tax?" is a concept question and does not qualify;
# "When is the LHDN deadline?" does.
_REGULATORY_AUTHORITY = re.compile(
    r"\b(ssm|lhdn|hasil|kastam|customs|mof|bnm|socso|perkeso|epf|kwsp|"
    r"sst|gst|cukai|e-?invoice|myinvois|cp\d{3}|borang|"
    r"form\s+(?:e|b|be|p|c)\b|annual return|penyata tahunan|"
    r"suruhanjaya syarikat|inland revenue)\b",
    re.IGNORECASE,
)

# A specific figure or date is being asked for, as opposed to an explanation.
# Plurals are explicit: \b after "deadline" does not match inside "deadlines",
# so a bare \bdeadline\b silently missed "What are the LHDN filing deadlines?"
_REGULATORY_SPECIFIC = re.compile(
    r"\b(deadlines?|due dates?|due by|cut ?offs?|when is|when are|when do|"
    r"when does|when must|how much|how long|rates?|thresholds?|penalt(?:y|ies)|"
    r"amounts?|fees?|dates?|qualify|eligible|eligibility|exempt(?:ion)?|"
    r"tarikh akhir|tarikh|bila|berapa|kadar|denda)\b",
    re.IGNORECASE,
)


def is_regulatory_query(text: str) -> bool:
    """True when the question asks for a specific regulatory figure or date.

    Used to keep retrieval switched on for questions the length heuristic would
    otherwise dismiss as "simple". "When is the SST deadline?" is eight words
    long but is exactly the kind of question that must be grounded in the
    knowledge base rather than recalled.
    """
    t = (text or "").strip()
    if not t:
        return False
    return bool(_REGULATORY_AUTHORITY.search(t) and _REGULATORY_SPECIFIC.search(t))


def is_simple_query(text: str) -> bool:
    """True for greetings and genuinely short questions.

    Callers use this to trim history and generation budget. It must never be
    used to skip an explicitly requested mode (web search, agent) -- those are
    deliberate user actions, not something a heuristic should override.
    """
    t = (text or "").strip()
    if is_greeting(t):
        return True
    return message_units(t) <= SIMPLE_QUERY_MAX_UNITS and len(t) < SIMPLE_QUERY_MAX_CHARS
