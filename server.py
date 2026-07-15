import os
import sys
import warnings
warnings.filterwarnings("ignore")
import json
import asyncio
import re
import uuid
import time
import datetime as dt
import queue as queue_module
import ipaddress
import urllib.parse
import urllib.request
from datetime import datetime
from fastapi import FastAPI, Request, UploadFile, File
from copy import deepcopy
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
from threading import Thread
from pymongo import MongoClient
from typing import Optional, List
import importlib.util as _ilu
import ollama as _ol

# Load pdf_agent from subfolder with spaces in path
_pdf_agent_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AI agent", "PDF Agent", "pdf_agent.py")
_spec = _ilu.spec_from_file_location("pdf_agent", _pdf_agent_path)
pdf_agent = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(pdf_agent)

# Load pdf_generator from AI agent/PDF Agent
_pdf_gen_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AI agent", "PDF Agent", "pdf_generator.py")
_gen_spec = _ilu.spec_from_file_location("pdf_generator", _pdf_gen_path)
pdf_generator = _ilu.module_from_spec(_gen_spec)
_gen_spec.loader.exec_module(pdf_generator)

# Load the CSV/structured-data agent separately from the PDF Agent.
_fin_data_agent_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AI agent", "Financial Data Agent", "financial_data_agent.py")
_fin_data_spec = _ilu.spec_from_file_location("financial_data_agent", _fin_data_agent_path)
financial_data_agent = _ilu.module_from_spec(_fin_data_spec)
_fin_data_spec.loader.exec_module(financial_data_agent)

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "interface functions"))
from auth import auth_router, _auth_user

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)
sys.path.append(os.path.join(_BASE, "Model Networking"))

# ── Load central config ──────────────────────────────────
from config_loader import cfg
os.environ.setdefault("OLLAMA_HOST", cfg.ollama_base_url)
_ollama_client = _ol.Client(host=cfg.ollama_base_url)

import Model_StartUp as ms
from ImageGemma4 import comfyui_local, image_gemma4, sd35_medium_local

_skill_gen_path = os.path.join(_BASE, "AI agent", "skill_impl", "generator.py")
_skill_gen_spec = _ilu.spec_from_file_location("agent_skill_generator", _skill_gen_path)
skill_generator = _ilu.module_from_spec(_skill_gen_spec)
sys.modules[_skill_gen_spec.name] = skill_generator
_skill_gen_spec.loader.exec_module(skill_generator)

try:
    from web_search import WebResearcher, detect_language
except ImportError:
    WebResearcher = None
    def detect_language(text): return "English"

app = FastAPI()


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data: blob: https:; font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
        "script-src 'self' 'unsafe-inline' https://accounts.google.com https://cdn.jsdelivr.net; "
        "connect-src 'self' https://accounts.google.com https://www.googleapis.com;",
    )
    return response

app.include_router(auth_router)

class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", NoCacheStaticFiles(directory=static_dir), name="static")

import gridfs
import io

# Load google_workspace_tools via importlib (replaces deprecated SourceFileLoader)
_gwt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AI agent", "google_workspace_tools.py")
_gwt_spec = _ilu.spec_from_file_location("google_workspace_tools", _gwt_path)
google_connectors = _ilu.module_from_spec(_gwt_spec)
_gwt_spec.loader.exec_module(google_connectors)
app.include_router(google_connectors.connectors_router)

# Load google_agent via importlib (directory name "AI agent" has spaces → not a valid package)
_ga_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AI agent", "google_agent.py")
_ga_spec = _ilu.spec_from_file_location("google_agent", _ga_path)
google_agent = _ilu.module_from_spec(_ga_spec)
_ga_spec.loader.exec_module(google_agent)

# Load memory_agent via importlib
_mem_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AI agent", "memory_agent.py")
_mem_spec = _ilu.spec_from_file_location("memory_agent", _mem_path)
memory_agent = _ilu.module_from_spec(_mem_spec)
_mem_spec.loader.exec_module(memory_agent)

# Load knowledge_agent via importlib
_knowledge_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AI agent", "knowledge_agent.py")
_knowledge_spec = _ilu.spec_from_file_location("knowledge_agent", _knowledge_path)
knowledge_agent = _ilu.module_from_spec(_knowledge_spec)
_knowledge_spec.loader.exec_module(knowledge_agent)

mongo_client = MongoClient(cfg.mongo_uri)
db = mongo_client[cfg.mongo_database]
chats_col = db["chats"]
feedbacks_col = db["feedbacks"]
fs = gridfs.GridFS(db)

MAX_UPLOAD_FILES = 10
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
UPLOAD_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
INLINE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _optional_auth_user(request: Request) -> dict | None:
    """Return the signed-in user, but allow a deliberately stateless guest chat."""
    if request.headers.get("authorization") or request.cookies.get("msme_session"):
        return _auth_user(request)
    return None


def _set_file_owner(filename: str, user_id: str, kind: str = "generated") -> None:
    file_doc = fs.find_one({"filename": filename})
    if not file_doc:
        raise FileNotFoundError("Generated file was not found")
    db.fs.files.update_one(
        {"_id": file_doc._id},
        {"$set": {"metadata.owner_id": str(user_id), "metadata.kind": kind}},
    )


def _user_can_access_file(user_id: str, filename: str) -> bool:
    """Authorize GridFS files exclusively through server-written ownership metadata."""
    file_doc = fs.find_one({"filename": filename})
    if not file_doc:
        return False
    metadata = getattr(file_doc, "metadata", None) or {}
    if metadata.get("owner_id"):
        return str(metadata["owner_id"]) == str(user_id)
    # Older records have no cryptographically trustworthy ownership proof because
    # the old chat endpoint accepted a browser-supplied transcript.  Denying them is
    # safer than inferring ownership from a forgeable legacy reference.
    return False


def _validate_owned_attachments(attachments: list | None, user_id: str) -> None:
    for attachment in attachments or []:
        if not isinstance(attachment, dict):
            raise HTTPException(status_code=400, detail="Invalid attachment")
        filename = str(attachment.get("saved_path") or "").strip()
        if not filename or not _user_can_access_file(user_id, filename):
            raise HTTPException(status_code=403, detail="You do not have access to one or more attachments")

model = None
tokenizer = None
model_type = None
_think_mode_supported = False   # resolved during startup from actual model name

def _sse(d):
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

def _detect_language(text):
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    total = max(len(text.strip()), 1)
    return "Chinese" if cn / total > 0.15 else "English"

def _extract_client_ip(request: Request) -> str:
    """Read the original client IP behind nginx/Plesk proxies when available."""
    for header in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        value = request.headers.get(header, "")
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else ""

COUNTRY_CODE_TO_NAME = {
    "AE": "United Arab Emirates",
    "AU": "Australia",
    "CA": "Canada",
    "CN": "China",
    "GB": "United Kingdom",
    "HK": "Hong Kong",
    "ID": "Indonesia",
    "IN": "India",
    "JP": "Japan",
    "KR": "South Korea",
    "MY": "Malaysia",
    "NZ": "New Zealand",
    "PH": "Philippines",
    "SG": "Singapore",
    "TH": "Thailand",
    "TW": "Taiwan",
    "US": "United States",
    "VN": "Vietnam",
}
COUNTRY_NAME_TO_CODE = {name: code for code, name in COUNTRY_CODE_TO_NAME.items()}
_geo_country_cache = {}
_GEO_COUNTRY_CACHE_TTL = 60 * 60 * 6

def _extract_country_code_from_headers(request: Request) -> str:
    for header in (
        "cf-ipcountry",
        "x-vercel-ip-country",
        "cloudfront-viewer-country",
        "x-country-code",
        "x-appengine-country",
    ):
        value = request.headers.get(header, "").strip().upper()
        if value and value not in {"XX", "ZZ"} and len(value) == 2:
            return value
    return ""

def _is_public_ip(ip_value: str) -> bool:
    try:
        return ipaddress.ip_address(ip_value).is_global
    except ValueError:
        return False

def _lookup_country_code_from_ip(ip_value: str) -> str:
    now = time.time()
    cached = _geo_country_cache.get(ip_value)
    if cached and cached[1] > now:
        return cached[0]
    if not _is_public_ip(ip_value):
        return ""
    try:
        safe_ip = urllib.parse.quote(ip_value, safe="")
        req = urllib.request.Request(
            f"https://ipapi.co/{safe_ip}/country/",
            headers={"User-Agent": "MSME.AI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=1.5) as response:
            code = response.read(16).decode("utf-8", "ignore").strip().upper()
        if code in COUNTRY_CODE_TO_NAME:
            _geo_country_cache[ip_value] = (code, now + _GEO_COUNTRY_CACHE_TTL)
            return code
    except Exception:
        return ""
    return ""

def _infer_country_from_locale_timezone(browser_language: str = "", timezone: str = "") -> str:
    raw = f"{browser_language or ''} {timezone or ''}".lower()
    country_map = [
        (("my", "kuala_lumpur", "malaysia"), "Malaysia"),
        (("sg", "singapore"), "Singapore"),
        (("cn", "shanghai", "beijing", "china"), "China"),
        (("hk", "hong_kong"), "Hong Kong"),
        (("tw", "taipei"), "Taiwan"),
        (("id", "jakarta", "indonesia"), "Indonesia"),
        (("th", "bangkok", "thailand"), "Thailand"),
        (("vn", "ho_chi_minh", "vietnam"), "Vietnam"),
        (("ph", "manila", "philippines"), "Philippines"),
        (("us", "new_york", "los_angeles", "chicago", "america"), "United States"),
        (("gb", "london", "united kingdom"), "United Kingdom"),
        (("au", "sydney", "melbourne", "australia"), "Australia"),
        (("jp", "tokyo", "japan"), "Japan"),
        (("kr", "seoul", "korea"), "South Korea"),
    ]
    for needles, country in country_map:
        if any(n in raw for n in needles):
            return country
    return ""

def _response_profile(text: str, agent_mode: bool = False, web_mode: bool = False, has_pdf: bool = False) -> dict:
    """Choose answer depth and generation budget from the request shape."""
    t = (text or "").strip()
    low = t.lower()
    score = 0
    if len(t) > 120:
        score += 1
    if len(t) > 280:
        score += 1
    if re.search(r"\b(compare|analy[sz]e|explain|strategy|report|proposal|plan|calculate|evaluate|forecast|summari[sz]e)\b", low):
        score += 2
    if re.search(r"(详细|分析|比较|报告|方案|计划|策略|总结|计算|预测|评估|完整|深入)", t):
        score += 2
    if any(mark in t for mark in ("?", "？")) and len(t) < 80:
        score -= 1
    if has_pdf:
        score += 3
    if agent_mode:
        return {
            "depth": "agent",
            "max_predict": 6144 if has_pdf or score >= 3 else 4096,
            "ctx": cfg.ollama_num_ctx_cap if has_pdf else min(cfg.ollama_num_ctx_cap, 8192),
            "instruction": (
                "ANSWER DEPTH: Agent mode should be action-oriented and complete. "
                "Be concise while planning, but provide a substantial final result with clear sections, "
                "tables when useful, and no filler. Verify names, dates, and numbers against the available context."
            ),
        }
    if web_mode:
        return {
            "depth": "web_deep" if score >= 2 else "web_standard",
            "max_predict": 3072 if score >= 2 else 2048,
            "ctx": min(cfg.ollama_num_ctx_cap, 6144),
            "instruction": (
                "ANSWER DEPTH: Use the live sources to produce a grounded answer. "
                "For simple lookup questions, answer briefly with citations. "
                "For business, legal, financial, or comparison questions, synthesize the evidence thoroughly. "
                "Do not invent sources, dates, prices, or legal requirements."
            ),
        }
    if score <= 0:
        return {
            "depth": "short",
            "max_predict": 2048,
            "ctx": min(cfg.ollama_num_ctx_cap, 4096),
            "instruction": "ANSWER DEPTH: This appears simple. Answer directly, but include enough context to be genuinely useful.",
        }
    if score <= 2:
        return {
            "depth": "standard",
            "max_predict": 4096,
            "ctx": min(cfg.ollama_num_ctx_cap, 6144),
            "instruction": "ANSWER DEPTH: Give a balanced, high-quality answer with concrete examples or steps when useful.",
        }
    return {
        "depth": "deep",
        "max_predict": 6144,
        "ctx": min(cfg.ollama_num_ctx_cap, 8192),
        "instruction": "ANSWER DEPTH: This is complex. Provide a high-quality structured answer with reasoning, examples, and tables where useful.",
    }


def _model_supports_thinking(model_name: str) -> bool:
    name = (model_name or "").lower()
    _non_thinking = ("gemma",)
    if any(m in name for m in _non_thinking):
        return False
    return any(marker in name for marker in ("deepseek", "qwq", "qwen3", "qwen-3", "reasoning"))


_unavailable_ollama_models = set()

def _generate_search_query_response(messages) -> str:
    """Use a tiny utility model for web-search query planning when available."""
    if tokenizer == "ollama" or model_type in ("gguf", "ollama"):
        tried = set()
        for query_model in (cfg.search_query_model, cfg.fast_model):
            if not query_model or query_model in tried:
                continue
            if query_model in _unavailable_ollama_models:
                continue
            tried.add(query_model)
            try:
                resp = _ollama_client.chat(
                    model=query_model,
                    messages=messages,
                    stream=False,
                    think=False,
                    options={
                        "temperature": 0.1,
                        "top_p": 0.9,
                        "num_predict": 256,
                        "num_ctx": 2048,
                        "num_gpu": cfg.ollama_num_gpu,
                        "num_thread": cfg.ollama_num_thread,
                        "use_mmap": True,
                    },
                )
                msg = resp.get("message") if isinstance(resp, dict) else getattr(resp, "message", None)
                content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
                if content:
                    return content
            except Exception as exc:
                _unavailable_ollama_models.add(query_model)
                print(f"  ⚠️ Search query model '{query_model}' unavailable: {exc}")

    return ms.generate_response(
        cfg.fast_model, tokenizer, messages,
        think_mode=False, show_thinking=False, stream=False
    )


def _extract_json_object(text: str) -> dict:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"```(?:json)?", "", cleaned, flags=re.IGNORECASE).replace("```", "").strip()
    if not cleaned:
        return {}
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return {}
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _classify_previous_image_route(user_text: str, image_ref: dict) -> str:
    """Use the fast local model as an intent router when a generated image exists."""
    text = (user_text or "").strip()
    if not text or not image_ref:
        return "normal_chat"

    fallback = "edit_previous_image" if image_gemma4.is_image_edit_request(text) else "normal_chat"
    if os.getenv("IMAGE_EDIT_INTENT_MODEL", "1").strip().lower() in {"0", "false", "no", "off"}:
        return fallback

    prompt = {
        "latest_user_message": text,
        "previous_generated_image": {
            "filename": image_ref.get("generated_image_name", ""),
            "original_request": image_ref.get("source_prompt", ""),
        },
    }
    messages_for_router = [
        {
            "role": "system",
            "content": (
                "You are a strict intent router for an AI chat app with image generation.\n"
                "A previous AI-generated image exists in the conversation.\n"
                "Decide what the latest user message wants.\n\n"
                "Return ONLY valid compact JSON with exactly these keys and no markdown:\n"
                "{\"route\":\"edit_previous_image|new_image|normal_chat\",\"confidence\":0.0}\n\n"
                "Use route=edit_previous_image when the user wants to change, fix, continue, refine, rename, recolor, "
                "add, remove, replace, crop, upscale, restyle, or otherwise modify the previous generated image. "
                "This includes indirect wording like 'make it...', 'same but...', 'change the company name...', "
                "'remove that', 'more premium', 'use gold', 'try another version', and pronouns such as it/this/logo.\n"
                "Use route=new_image only when they clearly ask for a separate new image rather than editing the previous one.\n"
                "Use route=normal_chat for questions, critique, explanations, prompt advice, troubleshooting, downloads, "
                "or anything unrelated to changing the image."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(prompt, ensure_ascii=False),
        },
    ]
    try:
        resp = _ollama_client.chat(
            model=cfg.fast_model,
            messages=messages_for_router,
            stream=False,
            think=False,
            format="json",
            options={
                "temperature": 0.0,
                "top_p": 1.0,
                "num_predict": 192,
                "num_ctx": 2048,
                "num_gpu": cfg.ollama_num_gpu,
                "num_thread": cfg.ollama_num_thread,
                "use_mmap": True,
            },
            keep_alive="8m",
        )
        msg = resp.get("message") if isinstance(resp, dict) else getattr(resp, "message", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        obj = _extract_json_object(content)
        route = str(obj.get("route", "")).strip().lower()
        if route in {"edit_previous_image", "new_image", "normal_chat"}:
            print(f"[IMAGE ROUTER] route={route} fallback={fallback} text={text[:120]!r}")
            return route
        print(f"[IMAGE ROUTER] Invalid router output; fallback={fallback}; raw={content[:180]!r}")
    except Exception as exc:
        print(f"[IMAGE ROUTER] Router failed; fallback={fallback}; error={exc}")
    return fallback


# =========================================================================
#  PhaseStreamer: Based on Model_StartUp.ThinkingAwareStreamer's proven
#  pattern. Uses skip_special_tokens=False + accumulated text detection
#  to find <think>/<think> boundaries. Emits SSE events to a queue.
# =========================================================================
class PhaseStreamer:
    """
    Custom streamer for model.generate() that detects <think>/<think>
    boundaries in accumulated decoded text and emits SSE-ready events
    to a thread-safe queue.
    """

    def __init__(self, tokenizer, think_mode=True, initial_phase=None):
        self.tokenizer = tokenizer
        self.think_mode = think_mode
        self.output_queue = queue_module.Queue()

        # Decoding state
        self.token_cache = []
        self.print_len = 0
        self.is_first_chunk = True

        # Phase tracking
        self.all_text = ""
        self.emitted_len = 0
        self.phase = initial_phase if initial_phase else ("thinking" if think_mode else "answering")
        self.sent_think_start = getattr(self, "phase", "") == "answering"

        # Collect special token strings to strip (but keep <think>/<think>)
        self._special_strings = set()
        if hasattr(tokenizer, 'all_special_tokens'):
            for t in tokenizer.all_special_tokens:
                if t not in ('<think>', '</think>'):
                    self._special_strings.add(t)

    def _clean(self, text):
        for s in self._special_strings:
            text = text.replace(s, '')
        return text

    def put(self, value):
        """Called by model.generate() for each new token batch."""
        if self.is_first_chunk:
            self.is_first_chunk = False
            return  # skip prompt

        if len(value.shape) > 1:
            value = value[0]

        self.token_cache.extend(value.tolist())
        text = self.tokenizer.decode(self.token_cache, skip_special_tokens=False)

        if text.endswith('\ufffd'):
            return

        new_text = text[self.print_len:]
        self.print_len = len(text)

        if new_text:
            self._process_text(new_text)

    def end(self):
        """Called when generation is complete."""
        if self.token_cache:
            text = self.tokenizer.decode(self.token_cache, skip_special_tokens=False)
            remaining = text[self.print_len:]
            if remaining and not remaining.endswith('\ufffd'):
                self._process_text(remaining)

        # Flush any un-emitted content
        self._flush_remaining()
        self.output_queue.put(None)  # sentinel

    def _process_text(self, new_text):
        """Process new decoded text, detect phase transitions, emit events."""
        self.all_text += new_text

        if not self.think_mode:
            # No think mode: everything is answer
            clean = self._clean(new_text)
            if clean:
                self.output_queue.put({'text': clean})
            return

        # === Think mode logic ===
        if self.phase == "thinking":
            if not self.sent_think_start:
                self.output_queue.put({'think_start': True})
                self.sent_think_start = True

            if '</think>' in self.all_text:
                # Think phase is over
                think_end_idx = self.all_text.find('</think>')
                # Emit remaining think content
                unemitted = self.all_text[self.emitted_len:think_end_idx]
                if unemitted:
                    clean = self._clean(unemitted.replace('<think>', ''))
                    if clean:
                        self.output_queue.put({'text': clean, 'thinking': True})

                self.output_queue.put({'think_end': True})
                self.phase = "answering"

                # Emit answer content after </think>
                answer = self.all_text[think_end_idx + 8:]
                if answer:
                    clean = self._clean(answer)
                    if clean:
                        self.output_queue.put({'text': clean})
                self.emitted_len = len(self.all_text)
            else:
                # Still thinking, emit new content
                unemitted = self.all_text[self.emitted_len:]
                if unemitted:
                    clean = self._clean(unemitted.replace('<think>', ''))
                    if clean:
                        self.output_queue.put({'text': clean, 'thinking': True})
                self.emitted_len = len(self.all_text)

        elif self.phase == "answering":
            unemitted = self.all_text[self.emitted_len:]
            if unemitted:
                clean = self._clean(unemitted)
                if clean:
                    self.output_queue.put({'text': clean})
            self.emitted_len = len(self.all_text)

    def _flush_remaining(self):
        """Flush any remaining content when stream ends."""
        if self.phase == "thinking":
            # If we never saw </think>, emit what we have
            unemitted = self.all_text[self.emitted_len:]
            if unemitted:
                clean = self._clean(unemitted.replace('<think>', '').replace('</think>', ''))
                if clean:
                    self.output_queue.put({'text': clean, 'thinking': True})
            self.output_queue.put({'think_end': True})
        elif self.phase == "answering":
            unemitted = self.all_text[self.emitted_len:]
            if unemitted:
                clean = self._clean(unemitted)
                if clean:
                    self.output_queue.put({'text': clean})

    def __iter__(self):
        return self

    def __next__(self):
        val = self.output_queue.get(timeout=180)
        if val is None:
            raise StopIteration
        return val


# =========================================================================
#  Server routes
# =========================================================================

@app.on_event("startup")
async def startup_event():
    global model, tokenizer, model_type, _think_mode_supported
    print("⏳ Scanning for local models...")
    available = []
    base = os.path.dirname(os.path.abspath(__file__))
    configured_model_path = cfg.gguf_path
    if configured_model_path and os.path.exists(configured_model_path):
        available.append((configured_model_path, os.path.basename(configured_model_path), "gguf"))
    for item in os.listdir(base):
        p = os.path.join(base, item)
        if configured_model_path and os.path.abspath(p) == os.path.abspath(configured_model_path):
            continue
        if os.path.isfile(p) and item.lower().endswith('.gguf'):
            available.append((p, item, "gguf"))
        elif os.path.isdir(p) and os.path.exists(os.path.join(p, "config.json")):
            available.append((p, item, "hf"))

    if available:
        available.sort(key=lambda x: x[1], reverse=True)  # Q5 > Q4 > ...
        path, name, model_type = available[0]
        _think_mode_supported = _model_supports_thinking(name)
        print(f"✅ Auto-selected: {name} [{model_type}]")
        ms.apply_speed_optimizations()
        model, tokenizer = ms.load_model_and_tokenizer(path, model_type)
    else:
        model_type = "ollama"
        model = cfg.think_model
        tokenizer = "ollama"
        # Check which model is actually registered in Ollama and base think-mode on that
        _active_model = cfg.think_model
        try:
            models_resp = _ollama_client.list()
            registered = {getattr(m, "model", "") for m in getattr(models_resp, "models", [])}
            registered_short = {n.split(":")[0] for n in registered}
            if cfg.think_model in registered or cfg.think_model.split(":")[0] in registered_short:
                _active_model = cfg.think_model
            else:
                print(f"⚠️ Ollama is reachable, but '{cfg.think_model}' is not pulled yet.")
                print(f"   Run: ollama pull {cfg.think_model}")
        except Exception as e:
            print(f"⚠️ Could not verify Ollama at {cfg.ollama_base_url}: {e}")
            print("   The first chat request will fail until Ollama is reachable.")

        _think_mode_supported = _model_supports_thinking(_active_model)
        print(f"ℹ️ Active Ollama model : {_active_model}")

    _think_label = "ENABLED" if _think_mode_supported else "DISABLED (model does not emit <think> tags)"
    print(f"ℹ️ Think mode          : {_think_label}")
    print(f"✅ Ready on http://127.0.0.1:{cfg.port}")

class ChatRequest(BaseModel):
    chat_id: Optional[str] = None
    user_id: Optional[str] = None
    message: str
    messages: Optional[list] = None
    attachments: Optional[list] = []
    think_mode: bool = True
    web_mode: bool = True
    is_resume: bool = False
    agent_mode: bool = False
    max_tokens: Optional[int] = None   # override MAX_NEW_TOKENS per request
    user_timezone: Optional[str] = ""
    browser_language: Optional[str] = ""
    regenerate_pdf: bool = False
    skill_type: Optional[str] = ""

class SettingsRequest(BaseModel):
    max_new_tokens: Optional[int] = None

# ---- Conversation helpers ----

SLIDING_WINDOW_TURNS = 12  # keep this many messages (= 6 user+assistant pairs)

def _apply_sliding_window(messages: list, window: int = SLIDING_WINDOW_TURNS) -> list:
    """Return at most `window` messages from the tail of the list."""
    return messages[-window:] if len(messages) > window else messages

def _summarise_history(messages: list, threshold: int = SLIDING_WINDOW_TURNS) -> list:
    """
    If the history is longer than `threshold`, call the model to compress
    older messages into a single summary message, keeping the most recent
    `threshold` messages intact.
    Returns the compressed message list.
    """
    if model is None or len(messages) <= threshold:
        return messages

    older   = messages[:-threshold]
    recent  = messages[-threshold:]

    # Build a transcript of the older turns for the model to summarise
    transcript_lines = []
    for m in older:
        role_label = "User" if m.get("role") == "user" else "Assistant"
        transcript_lines.append(f"{role_label}: {m.get('content', '')[:400]}")
    transcript = "\n".join(transcript_lines)

    summary_prompt = [
        {
            "role": "system",
            "content": (
                "You are a conversation summariser. "
                "Summarise the following conversation history in 2-4 concise paragraphs. "
                "Preserve key facts, decisions, and context the user may build upon later. "
                "Output only the summary — no extra commentary."
            ),
        },
        {"role": "user", "content": transcript},
    ]

    summary_text = ms.generate_response(
        model, tokenizer, summary_prompt,
        think_mode=False, show_thinking=False, stream=False
    )

    summary_msg = {
        "role": "system",
        "content": f"[Earlier conversation summary]\n{summary_text}",
    }
    return [summary_msg] + recent

def _prune_agent_generation_history(messages: list, max_items: int = 10) -> list:
    """
    For PDF generation/regeneration, keep the latest user intent but drop older
    long assistant report bodies that can cause the model to keep extending the
    previous report instead of cleanly regenerating a fresh one.
    """
    kept = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "") or ""
        if role == "user":
            kept.append({"role": "user", "content": content})
            continue
        if role == "assistant":
            if (
                len(content) <= 600
                and "⚙️ **Google Workspace**" not in content
                and "[GMAIL_CONFIRM_PENDING]" not in content
                and "Your PDF Report is Ready!" not in content
            ):
                kept.append({"role": "assistant", "content": content})
    if not kept:
        return messages[-2:] if len(messages) >= 2 else list(messages)
    return kept[-max_items:]

def _is_plain_pdf_export_request(text: str) -> bool:
    """Detect normal-chat requests to turn the previous answer into a PDF."""
    low = (text or "").lower()
    if "pdf" not in low:
        return False
    compact = re.sub(r"\s+", " ", low).strip()
    patterns = [
        r"\b(generate|create|make|export|download|convert|save|produce|prepare)\b.{0,60}\bpdf\b",
        r"\b(turn|put)\b.{0,40}\b(into|to)\b.{0,20}\bpdf\b",
        r"\bpdf\b.{0,40}\b(file|version|copy|download|report)\b",
        r"\b(jana|buat|hasilkan|muat turun)\b.{0,60}\bpdf\b",
        r"(生成|制作|导出|下载|转换).{0,20}pdf",
    ]
    return any(re.search(pattern, compact) for pattern in patterns)

def _strip_internal_markers(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"\[GMAIL_PREVIEW:[A-Za-z0-9_-]+={0,2}\]", "", cleaned)
    cleaned = cleaned.replace("[GMAIL_CONFIRM_PENDING]", "")
    return cleaned.strip()

def _latest_exportable_assistant_text(messages: list) -> str:
    """Find the latest useful assistant answer to export, skipping PDF/refusal messages."""
    bad_markers = (
        "cannot directly generate or provide a downloadable .pdf",
        "do not have a file-hosting system",
        "print-ready master copy",
        "copy and paste it into microsoft word",
        "save as pdf",
        "pdf generation failed",
        "your pdf report is ready",
    )
    for msg in reversed(messages or []):
        if msg.get("role") != "assistant":
            continue
        if msg.get("pdf_url") or msg.get("pdf_name"):
            continue
        content = _strip_internal_markers(msg.get("content", ""))
        if len(content) < 80:
            continue
        low = content.lower()
        if any(marker in low for marker in bad_markers):
            continue
        return content
    return ""

def _latest_pdf_filename_from_messages(messages: list) -> str:
    """Find the newest generated or uploaded PDF filename in chat messages."""
    for msg in reversed(messages or []):
        if msg.get("pdf_name"):
            return msg["pdf_name"]
        for att in reversed(msg.get("attachments", []) or []):
            saved_path = att.get("saved_path", "")
            if saved_path.lower().endswith(".pdf"):
                return os.path.basename(saved_path.replace("\\", "/"))
    return ""

def _latest_generated_file_from_messages(messages: list) -> dict:
    """Find the newest non-image generated skill file in chat messages."""
    for msg in reversed(messages or []):
        file_name = msg.get("generated_file_name")
        file_type = (msg.get("generated_file_type") or "").lower()
        if file_name and file_type:
            return {"name": file_name, "type": file_type}
    return {}

def _is_generated_file_rename_request(text: str) -> bool:
    """Detect a follow-up request that only renames the latest generated file."""
    low = (text or "").lower().strip()
    if not low:
        return False
    if re.search(
        r"\b(create|generate|make|export|produce|prepare|build|download)\b|"
        r"(生成|制作|创建|导出|做一份|做一个|產生|hasilan|buat|jana)",
        low,
        flags=re.IGNORECASE,
    ):
        return False
    if not skill_generator.extract_requested_file_stem(text):
        return False
    return bool(re.search(
        r"\b(rename|name|call|title|change)\b.{0,60}\b(file|document|docx|pdf|xlsx|excel|pptx|ppt|slide|deck|presentation|report|it)\b|"
        r"\b(file\s*name|filename|save\s+it\s+as|save\s+as)\b|"
        r"(重命名|改名|命名|文件名|名字|保存为|另存为)",
        text or "",
        flags=re.IGNORECASE,
    ))

def _latest_renameable_file(messages: list, chat_doc: dict | None = None) -> dict:
    """Return metadata for the latest generated document-style file."""
    for msg in reversed(messages or []):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        file_name = msg.get("generated_file_name")
        file_type = (msg.get("generated_file_type") or "").lower()
        if file_name and file_type in {"docx", "pdf", "pptx", "xlsx"}:
            return {
                "name": file_name,
                "type": file_type,
                "content_type": msg.get("generated_file_content_type") or "",
            }
        if msg.get("pdf_name"):
            return {
                "name": msg["pdf_name"],
                "type": "pdf",
                "content_type": "application/pdf",
            }
    if chat_doc:
        file_name = chat_doc.get("last_generated_file_name")
        file_type = (chat_doc.get("last_generated_file_type") or "").lower()
        if file_name and file_type in {"docx", "pdf", "pptx", "xlsx"}:
            return {"name": file_name, "type": file_type, "content_type": ""}
        if chat_doc.get("last_generated_pdf"):
            return {
                "name": chat_doc["last_generated_pdf"],
                "type": "pdf",
                "content_type": "application/pdf",
            }
    return {}

def _copy_gridfs_file_with_name(old_name: str, new_stem: str, file_type: str, owner_id: str) -> dict:
    if not _user_can_access_file(owner_id, old_name):
        raise PermissionError("You do not have access to this file")
    old_doc = fs.find_one({"filename": old_name})
    if not old_doc:
        raise FileNotFoundError(old_name)
    ext = (file_type or os.path.splitext(old_name)[1].lstrip(".") or "file").lower()
    new_name = skill_generator._safe_filename(new_stem, ext, exact=True, fs=fs)
    if new_name == old_name:
        return {
            "name": old_name,
            "type": ext,
            "content_type": getattr(old_doc, "content_type", "") or "",
            "copied": False,
        }
    metadata = dict(getattr(old_doc, "metadata", None) or {})
    metadata.update({
        "owner_id": str(owner_id),
        "kind": "generated",
        "renamed_from": old_name,
        "renamed_at": datetime.utcnow(),
    })
    fs.put(
        old_doc.read(),
        filename=new_name,
        content_type=getattr(old_doc, "content_type", None),
        metadata=metadata,
    )
    return {
        "name": new_name,
        "type": ext,
        "content_type": getattr(old_doc, "content_type", "") or "",
        "copied": True,
    }

def _messages_with_renamed_file(messages: list, old_name: str, new_name: str, file_type: str) -> list:
    updated = []
    for msg in messages or []:
        next_msg = dict(msg) if isinstance(msg, dict) else msg
        if isinstance(next_msg, dict):
            if next_msg.get("generated_file_name") == old_name:
                next_msg["generated_file_name"] = new_name
                next_msg["generated_file_url"] = f"/uploads/{new_name}"
            if next_msg.get("pdf_name") == old_name:
                next_msg["pdf_name"] = new_name
                next_msg["pdf_url"] = f"/api/download_pdf/{new_name}"
            if next_msg.get("generated_file_type") == "pdf" and file_type == "pdf":
                next_msg["pdf_name"] = next_msg.get("pdf_name") or new_name
                next_msg["pdf_url"] = next_msg.get("pdf_url") or f"/api/download_pdf/{new_name}"
        updated.append(next_msg)
    return updated

def _skill_ready_text(skill_type: str, file_name: str, lang: str) -> str:
    label = (skill_type or "file").upper()
    if lang == "Chinese":
        return f"好了，我已经生成了 {label} 文件。"
    return f"Done. I created the {label} file."

def _infer_local_skill_type(text: str) -> str:
    """Infer local file-generation skill from an explicit agent-mode request."""
    raw = text or ""
    low = raw.lower()
    if not low.strip():
        return ""
    google_action = re.search(
        r"\b(gmail|google\s+drive|gdrive|google\s+docs|google\s+sheets|google\s+slides?|gslides|google\s+presentation)\b|"
        r"\b(send|mail|email|upload)\b.{0,80}\b(to|drive|gmail)\b|"
        r"(发送|寄送|上传|保存到|存到).{0,20}(邮箱|邮件|drive|google|谷歌)",
        low,
        flags=re.IGNORECASE,
    )
    if google_action:
        return ""

    create_intent = re.search(
        r"\b(create|generate|make|export|produce|prepare|build|download)\b|"
        r"(生成|制作|创建|导出|做一份|做一个|產生|hasilkan|buat|jana)",
        low,
        flags=re.IGNORECASE,
    )
    if not create_intent:
        return ""

    candidates = [
        ("docx", r"\b(docx|docsx)\b|word\s+document|word\s+file|word文档|docx文件|文档"),
        ("xlsx", r"\b(xlsx|xls)\b|excel\s+file|spreadsheet|worksheet|电子表格|表格"),
        ("pptx", r"\b(pptx|ppt)\b|powerpoint|slide\s+deck|presentation|简报|演示文稿|幻灯片"),
        ("pdf", r"\bpdf\b|pdf文件|pdf报告"),
    ]
    matches = []
    for skill, pattern in candidates:
        match = re.search(pattern, low, flags=re.IGNORECASE)
        if match:
            matches.append((match.start(), skill))
    if not matches:
        return ""
    return sorted(matches, key=lambda item: item[0])[0][1]

def _extract_pdf(path: str, max_chars: int = 50000) -> str:
    """Extract text from PDF with increased context limit."""
    import pymupdf
    doc = pymupdf.open(path)
    text = ""
    for page in doc:
        text += page.get_text()
        if len(text) > max_chars:
            break
    return text[:max_chars]

class FeedbackRequest(BaseModel):
    chat_id: str
    msg_index: int
    rating: int

@app.get("/", response_class=HTMLResponse)
async def get_index():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
    with open(p, "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache", "Expires": "0"
    })

@app.get("/api/public-config")
async def public_config():
    public_site_url = cfg.public_site_url.rstrip("/")
    login_client_id = cfg.google_login_oauth_client_id or cfg.google_oauth_client_id
    return {
        "profile": cfg.profile,
        "google_oauth_client_id": login_client_id,
        "google_login_oauth_client_id": login_client_id,
        "google_connector_oauth_client_id": cfg.google_oauth_client_id,
        "google_login_redirect_uri": f"{public_site_url}/" if public_site_url else "/",
        "public_site_url": cfg.public_site_url,
        "think_model": cfg.think_model,
        "supports_think_mode": _think_mode_supported,
    }


@app.get("/api/geo/location")
async def geo_location(request: Request, timezone: str = "", browser_language: str = ""):
    header_country_code = _extract_country_code_from_headers(request)
    client_ip = _extract_client_ip(request)
    ip_country_code = ""
    if not header_country_code and client_ip:
        ip_country_code = await asyncio.to_thread(_lookup_country_code_from_ip, client_ip)

    inferred_country = _infer_country_from_locale_timezone(browser_language, timezone)
    country_code = header_country_code or ip_country_code or COUNTRY_NAME_TO_CODE.get(inferred_country, "")
    country = COUNTRY_CODE_TO_NAME.get(country_code) or inferred_country or "Malaysia"
    country_code = country_code or COUNTRY_NAME_TO_CODE.get(country, "MY")

    return {
        "status": "success",
        "country": country,
        "country_code": country_code,
    }


@app.post("/api/upload_files")
async def upload_files_endpoint(request: Request, files: List[UploadFile] = File(...)):
    user = _auth_user(request)
    user_id = str(user["_id"])
    if not files or len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"Upload between 1 and {MAX_UPLOAD_FILES} files")
    try:
        saved_files = []
        for file in files:
            original_name = os.path.basename(file.filename or "")
            ext = os.path.splitext(original_name)[1].lower()
            content_type = UPLOAD_CONTENT_TYPES.get(ext)
            if not content_type:
                raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext or 'unknown'}")
            file_id = str(uuid.uuid4())
            safe_name = f"{file_id}{ext}"
            file_content = await file.read(MAX_UPLOAD_BYTES + 1)
            file_size = len(file_content)
            if file_size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Each file must be 20 MB or smaller")

            # Save directly to MongoDB GridFS
            fs.put(
                file_content,
                filename=safe_name,
                content_type=content_type,
                metadata={"owner_id": user_id, "kind": "upload"},
            )

            saved_files.append({
                "file_id": file_id,
                "original_name": original_name,
                "saved_path": safe_name,
                "url": f"/uploads/{safe_name}",
                "size": file_size,
                "content_type": content_type,
            })
        return JSONResponse(content={"status": "success", "files": saved_files})
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Upload Error] {e}")
        return JSONResponse(status_code=500, content={"error": "Upload failed"})

@app.get("/uploads/{filename}")
async def get_uploaded_file(filename: str, request: Request):
    user = _auth_user(request)
    user_id = str(user["_id"])
    try:
        file_doc = fs.find_one({"filename": filename})
        if not file_doc:
            return JSONResponse(status_code=404, content={"error": "File not found"})
        if not _user_can_access_file(user_id, filename):
            raise HTTPException(status_code=403, detail="You do not have access to this file")
        content_type = file_doc.content_type if file_doc.content_type in INLINE_CONTENT_TYPES else "application/octet-stream"
        disposition = "inline" if content_type in INLINE_CONTENT_TYPES else "attachment"
        return StreamingResponse(
            io.BytesIO(file_doc.read()),
            media_type=content_type,
            headers={
                "Content-Disposition": f'{disposition}; filename="{file_doc.filename}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"[File Read Error] {e}")
        return JSONResponse(status_code=500, content={"error": "File download failed"})
@app.get("/api/history")
async def get_history(request: Request):
    user_id = str(_auth_user(request)["_id"])
    try:
        q = {"user_id": user_id}
        chats = list(chats_col.find(q, {"messages": 0}).sort("updated_at", -1))
        for c in chats:
            c["_id"] = str(c["_id"])
            if isinstance(c.get("updated_at"), datetime):
                c["updated_at"] = c["updated_at"].isoformat()
        return JSONResponse(content={"chats": chats})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

def _history_owner_query(chat_id: str, user_id: str) -> dict:
    return {"_id": chat_id, "user_id": str(user_id)}

@app.get("/api/history/{chat_id}")
async def get_chat(chat_id: str, request: Request):
    user_id = str(_auth_user(request)["_id"])
    try:
        chat = chats_col.find_one(_history_owner_query(chat_id, user_id))
        if chat:
            chat["_id"] = str(chat["_id"])
            if isinstance(chat.get("updated_at"), datetime):
                chat["updated_at"] = chat["updated_at"].isoformat()
            
            fb_docs = feedbacks_col.find({"chat_id": chat_id, "user_id": user_id})
            chat["feedback"] = {str(doc["msg_index"]): doc["rating"] for doc in fb_docs}
            
            return JSONResponse(content={"chat": chat})
        return JSONResponse(status_code=404, content={"error": "Not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.delete("/api/history/{chat_id}")
async def delete_chat(chat_id: str, request: Request):
    user_id = str(_auth_user(request)["_id"])
    try:
        result = chats_col.delete_one(_history_owner_query(chat_id, user_id))
        if result.deleted_count > 0:
            feedbacks_col.delete_many({"chat_id": chat_id, "user_id": user_id})
            try:
                pdf_agent.agent_memory.pop(chat_id, None)
            except Exception:
                pass
            return JSONResponse(content={"status": "success"})
        return JSONResponse(status_code=404, content={"error": "Not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
class RenameChatRequest(BaseModel):
    title: str

@app.put("/api/history/{chat_id}")
async def rename_chat(chat_id: str, req: RenameChatRequest, request: Request):
    user_id = str(_auth_user(request)["_id"])
    try:
        result = chats_col.update_one(_history_owner_query(chat_id, user_id), {"$set": {"title": req.title}})
        if result.matched_count > 0:
            return JSONResponse(content={"status": "success", "title": req.title})
        return JSONResponse(status_code=404, content={"error": "Not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})



async def stream_generator(
    chat_id,
    messages,
    think_mode,
    web_mode,
    is_resume=False,
    max_tokens_override=None,
    agent_mode=False,
    attachments=None,
    user_timezone="",
    client_request=None,
    regenerate_pdf=False,
    browser_language="",
    client_ip="",
    user_country="",
    skill_type="",
):
    async def _client_gone():
        if client_request is None:
            return False
        try:
            return await client_request.is_disconnected()
        except Exception:
            return False

    def _close_stream(stream_obj):
        if stream_obj is None:
            return
        close_fn = getattr(stream_obj, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                pass

    # --- Language detection ---
    latest_user_msg = ""
    latest_user_idx = None
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if msg.get("role") == "user":
            latest_user_msg = msg.get("content", "")
            latest_user_idx = idx
            break
    image_attachments = image_gemma4.image_attachments(attachments or [])
    if image_attachments and not (latest_user_msg or "").strip():
        latest_user_msg = image_gemma4.default_image_analysis_prompt(browser_language or "")
        if latest_user_idx is not None:
            messages = list(messages)
            messages[latest_user_idx] = dict(messages[latest_user_idx])
            messages[latest_user_idx]["content"] = latest_user_msg
    latest_generated_image_ref = image_gemma4.latest_generated_image_reference(messages, latest_user_idx)
    new_image_request = bool(image_gemma4.is_image_generation_request(latest_user_msg))
    previous_image_edit_request = False
    uploaded_image_edit_request = bool(image_attachments and image_gemma4.is_image_edit_request(latest_user_msg))
    if latest_generated_image_ref and not is_resume and not new_image_request:
        yield _sse({"status": "routing_image_intent"})
        image_route = await asyncio.to_thread(
            _classify_previous_image_route,
            latest_user_msg,
            latest_generated_image_ref,
        )
        previous_image_edit_request = image_route == "edit_previous_image"
        new_image_request = image_route == "new_image"
    image_route_request = bool(
        not is_resume
        and (new_image_request or previous_image_edit_request or uploaded_image_edit_request)
    )
    agent_user_msg = latest_user_msg
    if regenerate_pdf:
        agent_user_msg = (agent_user_msg + "\n\nRegenerate the current PDF report and produce a new downloadable PDF.").strip()
    user_lang = detect_language(latest_user_msg)
    has_pdf = any(
        att.get("saved_path", "").lower().endswith(".pdf")
        for att in (attachments or [])
    )
    has_financial_data_upload = bool(
        agent_mode and financial_data_agent.has_supported_data_attachment(attachments or [])
    )
    has_financial_data_context = bool(
        agent_mode and not has_pdf and (
            has_financial_data_upload or financial_data_agent.has_active_data(chat_id)
        )
    )
    response_profile = _response_profile(
        latest_user_msg,
        agent_mode=agent_mode,
        web_mode=web_mode,
        has_pdf=(has_pdf or has_financial_data_context),
    )

    # --- History compression: summarise old turns, then apply sliding window ---
    if agent_mode and not image_route_request:
        inference_messages = list(messages)
    else:
        inference_messages = _summarise_history(messages)
        inference_messages = _apply_sliding_window(inference_messages)

    _chat_doc = chats_col.find_one({"_id": chat_id})
    _stream_user_id = _chat_doc.get("user_id") if _chat_doc else None
    _pre_agent_state = pdf_agent.agent_memory.get(chat_id, {}) if agent_mode else {}
    _active_document_id = _pre_agent_state.get("active_document_id") if _pre_agent_state else None
    route_financial_data_agent = bool(
        agent_mode
        and has_financial_data_context
        and not _pre_agent_state.get("source_data")
        and not _pre_agent_state.get("template_data")
        and not _pre_agent_state.get("active_document_id")
    )

    if (
        agent_mode
        and not is_resume
        and not image_route_request
        and _is_generated_file_rename_request(latest_user_msg)
    ):
        requested_name = skill_generator.extract_requested_file_stem(latest_user_msg)
        target_file = _latest_renameable_file(messages, _chat_doc)
        if not target_file:
            msg = (
                "没有找到可以重命名的已生成文件。请先生成一个 DOCX、PDF、XLSX 或 PPTX 文件。"
                if user_lang == "Chinese"
                else "I could not find a generated DOCX, PDF, XLSX, or PPTX file to rename."
            )
            yield _sse({"text": msg})
            chats_col.update_one(
                {"_id": chat_id},
                {
                    "$push": {"messages": {"role": "assistant", "content": msg}},
                    "$set": {"updated_at": datetime.utcnow()},
                },
            )
            yield "data: [DONE]\n\n"
            return
        try:
            renamed = await asyncio.to_thread(
                _copy_gridfs_file_with_name,
                target_file["name"],
                requested_name,
                target_file["type"],
                _stream_user_id,
            )
            new_name = renamed["name"]
            file_type = renamed["type"]
            content_type = (
                renamed.get("content_type")
                or target_file.get("content_type")
                or getattr(skill_generator, "CONTENT_TYPES", {}).get(file_type, "application/octet-stream")
            )
            ready_text = (
                f"好了，我已经把文件重命名为 {new_name}。"
                if user_lang == "Chinese"
                else f"Done. I renamed the file to {new_name}."
            )
            yield _sse({"text": ready_text})
            if file_type == "pdf":
                yield _sse({
                    "pdf_ready": True,
                    "pdf_url": f"/api/download_pdf/{new_name}",
                    "pdf_name": new_name,
                })
            else:
                yield _sse({
                    "file_ready": True,
                    "file_url": f"/uploads/{new_name}",
                    "file_name": new_name,
                    "file_type": file_type,
                    "content_type": content_type,
                    "title": requested_name,
                })

            updated_messages = _messages_with_renamed_file(
                messages,
                target_file["name"],
                new_name,
                file_type,
            )
            assistant_msg = {
                "role": "assistant",
                "content": ready_text,
                "generated_file_url": f"/uploads/{new_name}",
                "generated_file_name": new_name,
                "generated_file_type": file_type,
                "generated_file_content_type": content_type,
            }
            set_fields = {
                "messages": updated_messages + [assistant_msg],
                "updated_at": datetime.utcnow(),
                "last_generated_file_name": new_name,
                "last_generated_file_type": file_type,
            }
            if file_type == "pdf":
                assistant_msg["pdf_url"] = f"/api/download_pdf/{new_name}"
                assistant_msg["pdf_name"] = new_name
                set_fields["last_generated_pdf"] = new_name
                pdf_agent.agent_memory.setdefault(chat_id, {})["last_generated_pdf"] = new_name
            chats_col.update_one({"_id": chat_id}, {"$set": set_fields})
        except Exception as rename_err:
            err_text = (
                f"文件重命名失败：{rename_err}"
                if user_lang == "Chinese"
                else f"File rename failed: {rename_err}"
            )
            yield _sse({"text": err_text})
            chats_col.update_one(
                {"_id": chat_id},
                {
                    "$push": {"messages": {"role": "assistant", "content": err_text}},
                    "$set": {"updated_at": datetime.utcnow()},
                },
            )
        yield "data: [DONE]\n\n"
        return

    # Memory Retrieval (Run in thread to avoid blocking loop)
    memory_injection = ""
    if _stream_user_id and not image_route_request and not (agent_mode and (has_pdf or route_financial_data_agent)):
        memory_injection = await asyncio.to_thread(
            memory_agent.retrieve_memory_context,
            _stream_user_id,
            latest_user_msg,
            3,
            _active_document_id if agent_mode else None,
        )

    knowledge_injection = ""
    if not image_route_request and not (agent_mode and (has_pdf or route_financial_data_agent)):
        knowledge_injection = await asyncio.to_thread(
            knowledge_agent.retrieve_knowledge_context,
            latest_user_msg,
        )

    final_messages = inference_messages
    raw_accum_text = ""
    initial_phase  = None
    if is_resume and messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "assistant":
        raw_accum_text = messages[-1].get("content", "")
        if think_mode:
            initial_phase = "answering" if "</think>" in raw_accum_text else "thinking"

    if (
        agent_mode
        and not is_resume
        and not has_pdf
        and not has_financial_data_upload
        and not _pre_agent_state.get("source_data")
        and not _pre_agent_state.get("template_data")
        and not _pre_agent_state.get("active_document_id")
        and _is_plain_pdf_export_request(latest_user_msg)
    ):
        export_source = _latest_exportable_assistant_text(messages)
        if export_source:
            yield _sse({"status": "model_starting"})
            try:
                requested_pdf_name = skill_generator.extract_requested_file_stem(latest_user_msg, "pdf")
                _, pdf_filename = await pdf_generator.markdown_to_pdf(
                    export_source,
                    "general",
                    is_template=False,
                    filename_stem=requested_pdf_name,
                )
                _set_file_owner(pdf_filename, _stream_user_id, "generated")
                pdf_agent.agent_memory.setdefault(chat_id, {})["last_generated_pdf"] = pdf_filename
                ready_text = "Done. I created the PDF from the previous response."
                yield _sse({"text": ready_text})
                yield _sse({
                    "pdf_ready": True,
                    "pdf_url": f"/api/download_pdf/{pdf_filename}",
                    "pdf_name": pdf_filename,
                })
                chats_col.update_one(
                    {"_id": chat_id},
                    {
                        "$push": {
                            "messages": {
                                "role": "assistant",
                                "content": ready_text,
                                "pdf_url": f"/api/download_pdf/{pdf_filename}",
                                "pdf_name": pdf_filename,
                            }
                        },
                        "$set": {
                            "updated_at": datetime.utcnow(),
                            "last_generated_pdf": pdf_filename,
                        },
                    },
                )
            except Exception as pdf_err:
                print(f"[Normal PDF Export Error] {pdf_err}")
                err_text = f"PDF generation failed: {pdf_err}"
                yield _sse({"text": err_text})
                chats_col.update_one(
                    {"_id": chat_id},
                    {
                        "$push": {"messages": {"role": "assistant", "content": err_text}},
                        "$set": {"updated_at": datetime.utcnow()},
                    },
                )
            yield "data: [DONE]\n\n"
            return

    requested_skill_type = (skill_type or "").strip().lower().lstrip(".")
    if not requested_skill_type:
        requested_skill_type = _infer_local_skill_type(latest_user_msg)
    if (
        not is_resume
        and requested_skill_type in getattr(skill_generator, "SUPPORTED_SKILLS", set())
    ):
        yield _sse({"status": "creating_skill_file", "file_type": requested_skill_type})
        try:
            authored_content = ""
            if model is not None:
                authoring_messages = skill_generator.build_authoring_messages(
                    requested_skill_type,
                    latest_user_msg,
                    messages,
                    attachments or [],
                )
                authored_content = await asyncio.to_thread(
                    ms.generate_response,
                    cfg.fast_model,
                    tokenizer,
                    authoring_messages,
                    think_mode=False,
                    show_thinking=False,
                    stream=False,
                )

            result = await asyncio.to_thread(
                skill_generator.generate_skill_file,
                requested_skill_type,
                latest_user_msg,
                messages,
                fs,
                authored_content,
                attachments or [],
            )
            _set_file_owner(result.file_name, _stream_user_id, "generated")
            ready_text = _skill_ready_text(result.file_type, result.file_name, user_lang)
            yield _sse({"text": ready_text})
            yield _sse({
                "file_ready": True,
                "file_url": result.file_url,
                "file_name": result.file_name,
                "file_type": result.file_type,
                "content_type": result.content_type,
                "title": result.title,
            })

            new_msg = {
                "role": "assistant",
                "content": ready_text,
                "generated_file_url": result.file_url,
                "generated_file_name": result.file_name,
                "generated_file_type": result.file_type,
                "generated_file_content_type": result.content_type,
            }
            set_fields = {
                "updated_at": datetime.utcnow(),
                "last_generated_file_name": result.file_name,
                "last_generated_file_type": result.file_type,
            }
            if result.file_type == "pdf":
                new_msg["pdf_url"] = f"/api/download_pdf/{result.file_name}"
                new_msg["pdf_name"] = result.file_name
                set_fields["last_generated_pdf"] = result.file_name
                pdf_agent.agent_memory.setdefault(chat_id, {})["last_generated_pdf"] = result.file_name

            chats_col.update_one(
                {"_id": chat_id},
                {
                    "$push": {"messages": new_msg},
                    "$set": set_fields,
                },
            )
        except Exception as skill_err:
            print(f"[Skill Generation Error] {skill_err}")
            err_text = (
                f"文件生成失败：{skill_err}"
                if user_lang == "Chinese"
                else f"File generation failed: {skill_err}"
            )
            yield _sse({"text": err_text})
            chats_col.update_one(
                {"_id": chat_id},
                {
                    "$push": {"messages": {"role": "assistant", "content": err_text}},
                    "$set": {"updated_at": datetime.utcnow()},
                },
            )
        yield "data: [DONE]\n\n"
        return

    # Resolve effective max tokens
    max_new_tok = max_tokens_override if max_tokens_override else ms.MAX_NEW_TOKENS
    
    agent_system_context = ""
    if agent_mode and not image_route_request:
        if has_pdf:
            # Signal UI immediately — PDF parsing is CPU-heavy (15-30s for large files)
            yield _sse({"status": "parsing_pdf"})
        elif route_financial_data_agent and has_financial_data_upload:
            yield _sse({"status": "parsing_data"})

        # ── Pre-flight: If a fresh source PDF arrives, wipe old per-document state first. ──
        if has_pdf and pdf_agent.should_reset_for_new_pdf(chat_id, latest_user_msg, attachments):
            pdf_agent.reset_for_new_source(chat_id)

        # Run in thread so SSE stream stays alive during heavy extraction
        if route_financial_data_agent:
            agent_inst, agent_ctx = await asyncio.to_thread(
                financial_data_agent.process_agent_request, chat_id, agent_user_msg, attachments, messages
            )
            _agent_state = {}
        else:
            agent_inst, agent_ctx = await asyncio.to_thread(
                pdf_agent.process_agent_request, chat_id, agent_user_msg, attachments, messages
            )
            _agent_state = pdf_agent.agent_memory.get(chat_id, {})
        if _agent_state.get("new_source_loaded"):
            _agent_state["document_context_start_index"] = max(len(messages) - 1, 0)
            _agent_state["new_source_loaded"] = False
        _doc_start = _agent_state.get("document_context_start_index")
        if isinstance(_doc_start, int) and _doc_start >= 0:
            scoped_messages = messages[_doc_start:]
            if agent_mode:
                if _agent_state.get("generate_pdf_now") or pdf_agent._is_explicit_pdf_output_request(latest_user_msg):
                    inference_messages = _prune_agent_generation_history(scoped_messages)
                    print(f"[AGENT CONTEXT] Regeneration context pruned: {len(scoped_messages)} -> {len(inference_messages)} messages")
                else:
                    inference_messages = list(scoped_messages)
            else:
                inference_messages = _summarise_history(scoped_messages)
                inference_messages = _apply_sliding_window(inference_messages)
            final_messages = inference_messages

        # ── Intercept Google Connector Requests via Google Agent ──
        _agent_user_id = _stream_user_id
        
        if agent_mode and _agent_user_id and google_agent.is_google_request(latest_user_msg, _agent_user_id):
            yield _sse({"status": "executing Google Agent"})
            
            async def google_cb(msgs):
                return await asyncio.to_thread(
                    ms.generate_response, cfg.fast_model, tokenizer, msgs, 
                    think_mode=False, show_thinking=False, stream=False
                )
            
            _agent_mem_pdf = pdf_agent.agent_memory.get(chat_id, {}).get("last_generated_pdf", None)
            if not _agent_mem_pdf:
                _agent_mem_pdf = pdf_agent.agent_memory.get(chat_id, {}).get("last_pdf", None)
            if not _agent_mem_pdf and _chat_doc:
                _agent_mem_pdf = _chat_doc.get("last_generated_pdf", None)
            # Also check recent chat messages for a PDF attachment
            if not _agent_mem_pdf:
                _agent_mem_pdf = _latest_pdf_filename_from_messages(messages[-10:])
            if not _agent_mem_pdf and _chat_doc:
                _agent_mem_pdf = _latest_pdf_filename_from_messages((_chat_doc.get("messages") or [])[-10:])
            if _agent_mem_pdf and not _user_can_access_file(_agent_user_id, _agent_mem_pdf):
                _agent_mem_pdf = None
            _agent_mem_image = (latest_generated_image_ref or {}).get("generated_image_name") or None
            if _agent_mem_image and not _user_can_access_file(_agent_user_id, _agent_mem_image):
                _agent_mem_image = None
            _agent_mem_file = _latest_generated_file_from_messages(messages[-10:])
            if not _agent_mem_file and _chat_doc:
                _agent_mem_file = {
                    "name": _chat_doc.get("last_generated_file_name"),
                    "type": _chat_doc.get("last_generated_file_type"),
                } if _chat_doc.get("last_generated_file_name") else {}
            if not _agent_mem_file and _chat_doc:
                _agent_mem_file = _latest_generated_file_from_messages((_chat_doc.get("messages") or [])[-10:])
            if _agent_mem_file and not _user_can_access_file(_agent_user_id, _agent_mem_file.get("name", "")):
                _agent_mem_file = {}

            # The file logic is handled dynamically now directly from MongoDB in google_agent/google_workspace_tools
            # We don't need to resolve real physical directories anymore.
            _out = await google_agent.process_google_request(
                user_id=_agent_user_id,
                current_msg=latest_user_msg,
                messages=messages,
                active_scopes="",
                llm_callback=google_cb,
                upload_dir="mongodb_gridfs",  # Dummy value
                pdf_filename=_agent_mem_pdf,
                image_filename=_agent_mem_image,
                file_filename=_agent_mem_file.get("name") if _agent_mem_file else None,
                file_kind=_agent_mem_file.get("type") if _agent_mem_file else None,
                user_timezone=user_timezone or "",
            )
            
            if _out == "__NORMAL_CHAT_FALLBACK__":
                # The agent explicitly refused to hijack this message for Google Workspace.
                # Fall through to normal conversational LLM generation.
                pass
            else:
                yield _sse({"text": _out + "\n\n"})
                
                # Save Google Agent result to DB and finish early
                _db_msgs = deepcopy(messages)
                _db_msgs.append({"role": "assistant", "content": _out})
                chats_col.update_one({"_id": chat_id}, {"$set": {
                    "messages": _db_msgs,
                    "updated_at": dt.datetime.utcnow().isoformat(),
                }})
                yield "data: [DONE]\n\n"
                
                return
        # ── End Google Intercept ──

        if agent_inst or agent_ctx:
            agent_system_context = f"{agent_inst}\n\n{agent_ctx}"

    if model is None:
        yield _sse({'text': 'Error: Model not loaded.'})
        yield "data: [DONE]\n\n"
        return

    if image_route_request:
        yield _sse({"status": "generating_image"})
        try:
            _image_provider = os.getenv("LOCAL_IMAGE_PROVIDER", "sd35").strip().lower()
            _generate_local_image = (
                sd35_medium_local.generate_local_image
                if _image_provider in {"sd35", "sd3.5", "sd3.5-medium", "stable-diffusion-3.5-medium"}
                else comfyui_local.generate_local_image
            )
            image_generation_attachments = list(attachments or [])
            if latest_generated_image_ref and previous_image_edit_request:
                ref_attachment = image_gemma4.generated_image_reference_attachment(latest_generated_image_ref)
                if ref_attachment:
                    if not _user_can_access_file(_stream_user_id, ref_attachment.get("saved_path", "")):
                        raise PermissionError("You do not have access to the image selected for editing")
                    image_generation_attachments.insert(0, ref_attachment)
            gen_task = asyncio.create_task(
                asyncio.to_thread(
                    _generate_local_image,
                    latest_user_msg,
                    image_generation_attachments,
                    fs=fs,
                )
            )
            while True:
                done, _ = await asyncio.wait({gen_task}, timeout=8)
                if done:
                    break
                if await _client_gone():
                    gen_task.cancel()
                    print(f"[STREAM] Client disconnected during image generation; chat_id={chat_id}")
                    return
                yield _sse({"heartbeat": True, "status": "generating_image"})

            image_result = await gen_task
            image_filename = image_result["filename"]
            _set_file_owner(image_filename, _stream_user_id, "generated")
            image_url = f"/uploads/{image_filename}"
            ready_text = ""
            yield _sse({
                "image_ready": True,
                "image_url": image_url,
                "image_name": image_filename,
                "image_format": image_result.get("format", "svg"),
            })
            chats_col.update_one(
                {"_id": chat_id},
                {
                    "$push": {
                        "messages": {
                            "role": "assistant",
                            "content": ready_text,
                            "generated_image_url": image_url,
                            "generated_image_name": image_filename,
                        }
                    },
                    "$set": {"updated_at": datetime.utcnow()},
                },
            )
        except Exception as img_err:
            print(f"[IMAGE GEN ERROR] {img_err}")
            _image_provider = os.getenv("LOCAL_IMAGE_PROVIDER", "sd35").strip().lower()
            if _image_provider in {"sd35", "sd3.5", "sd3.5-medium", "stable-diffusion-3.5-medium"}:
                err_text = (
                    "本地 Stable Diffusion 图片生成失败。常见原因是 Ollama/Gemma 仍占用显存，"
                    "或 SD3.5/T5 模型加载时显存不足。"
                    f"\n\n错误：{img_err}"
                    if user_lang == "Chinese"
                    else "Local Stable Diffusion image generation failed. The most common cause is Ollama/Gemma "
                    f"still occupying GPU memory, or SD3.5/T5 running out of VRAM while loading.\n\nError: {img_err}"
                )
            else:
                err_text = (
                    "本地图片生成失败：请确认 ComfyUI 已在 127.0.0.1:8188 启动，并且已经加载 FLUX/Qwen-Image 的 API workflow。"
                    f"\n\n错误：{img_err}"
                    if user_lang == "Chinese"
                    else f"Local image generation failed. Make sure ComfyUI is running at 127.0.0.1:8188 and a FLUX/Qwen-Image API workflow is configured.\n\nError: {img_err}"
                )
            yield _sse({"text": err_text})
            chats_col.update_one(
                {"_id": chat_id},
                {
                    "$push": {"messages": {"role": "assistant", "content": err_text}},
                    "$set": {"updated_at": datetime.utcnow()},
                },
            )
        yield "data: [DONE]\n\n"
        return

    sources = []

    # Web mode
    if web_mode:
        if WebResearcher is None:
            yield _sse({'text': 'Error: WebResearcher not available.'})
            yield "data: [DONE]\n\n"
            return
        yield _sse({'status': 'searching'})
        wa = WebResearcher(cfg, user_location=user_country)
        try:
            prepare_task = asyncio.create_task(wa.prepare(inference_messages, force_search=True))
            while True:
                done, _ = await asyncio.wait({prepare_task}, timeout=8)
                if done:
                    break
                if await _client_gone():
                    prepare_task.cancel()
                    print(f"[STREAM] Client disconnected during web search; chat_id={chat_id}")
                    return
                yield _sse({"heartbeat": True, "status": "searching"})
            final_messages, sources = await prepare_task
        except Exception as e:
            yield _sse({'text': f'Web Search Error: {e}'})
            yield "data: [DONE]\n\n"
            return
        yield _sse({'sources': sources})
        yield _sse({'status': 'answering'})
        
        # Inject identity, language, agent context, and memory into web mode system prompt
        _web_additions = []
        _today = dt.datetime.now().strftime("%Y-%m-%d")
        _web_location = user_country or "the user's region"
        _web_context = (
            f"DATE AND USER CONTEXT: Today is {_today}. "
            f"User timezone: {user_timezone or 'unknown'}. "
            f"User country/region hint: {_web_location}. "
            f"Browser language: {browser_language or 'unknown'}. "
            f"Client IP is available only as private routing metadata and must not be displayed unless directly relevant.\n"
            f"WEB ANSWER STYLE: Prefer fresh {_today[:4]} sources. For location-sensitive questions, localize to {_web_location}. "
            f"Use clean markdown, inline source links, compact tables for comparisons/prices, and tasteful small icons/symbols only when they improve scanning."
        )
        _identity_lang = (
            f"IDENTITY: Your name is Pepper Labs AI. You are an AI assistant created and trained by Pepper Labs. "
            f"If asked who you are, always say: 'I am Pepper Labs AI, an AI assistant built by Pepper Labs.'\n"
            f"LANGUAGE: Detect the language of the user's message and reply in that exact same language. "
            f"Only use Chinese (中文), English, or Malay (Bahasa Malaysia). Never use any other language."
        )
        _web_additions.append(_web_context)
        _web_additions.append(_identity_lang)
        _web_additions.append(response_profile["instruction"])
        if agent_system_context:
            _web_additions.append(agent_system_context)
        if memory_injection:
            _web_additions.append(memory_injection)
        if knowledge_injection:
            _web_additions.append(knowledge_injection)
        if _web_additions and len(final_messages) > 0 and final_messages[0]["role"] == "system":
            final_messages[0]["content"] = "\n\n".join(_web_additions) + "\n\n" + final_messages[0]["content"]
    else:
        # Use day-level precision so Ollama can reuse the KV-cache for the system
        # prompt across all requests on the same day (minutes would bust the cache
        # on every request and force a full re-prefill of the system prompt).
        today = dt.datetime.now().strftime("%Y-%m-%d")

        system_instruction = (
            f"Date: {today}\n\n"
            f"IDENTITY: Your name is Pepper Labs AI. You are an AI assistant created and trained by Pepper Labs. "
            f"If asked who you are, always say: 'I am Pepper Labs AI, an AI assistant built by Pepper Labs.'\n\n"
            f"ROLE: You are a highly capable AI assistant.\n"
            f"RULES:\n"
            f"- Answer directly and specifically.\n"
            f"- Prefer accuracy over sounding confident; say when information is uncertain or missing.\n"
            f"- For factual claims involving dates, prices, laws, policies, or companies, be conservative and precise.\n"
            f"- Use the best format: paragraphs, lists, tables, or code — whatever is clearest.\n"
            f"- For technical or math questions, be precise and include examples.\n\n"
            f"{response_profile['instruction']}\n\n"
            f"LANGUAGE: Detect the language of the user's message and reply in that exact same language. "
            f"Only use Chinese (中文), English, or Malay (Bahasa Malaysia). Never switch to another language."
        )
        
        if agent_system_context:
            system_instruction = f"{agent_system_context}\n\n{system_instruction}"
        if memory_injection:
            system_instruction = f"{memory_injection}\n\n{system_instruction}"
        if knowledge_injection:
            system_instruction = f"{knowledge_injection}\n\n{system_instruction}"

        final_messages = [{"role": "system", "content": system_instruction}] + list(inference_messages)

    # === Inject images from attachments into the last user message ===
    if image_attachments:
        if not (tokenizer == "ollama" or model_type in ("gguf", "ollama")):
            yield _sse({
                "text": (
                    "当前本地 HuggingFace 加载通道还没有接视觉编码器；请使用 Ollama/Gemma4 视觉模型来分析图片。"
                    if user_lang == "Chinese"
                    else "This local HuggingFace loading path is not wired for vision input yet. Please use an Ollama/Gemma4 vision-capable model for image analysis."
                )
            })
            yield "data: [DONE]\n\n"
            return

        yield _sse({"status": "preparing_image"})
        prepared_images = await asyncio.to_thread(
            image_gemma4.prepared_images_from_attachments,
            attachments or [],
            fs,
        )
        if not prepared_images:
            yield _sse({
                "text": (
                    "我没能读取到可分析的图片，请重新上传或粘贴图片。"
                    if user_lang == "Chinese"
                    else "I could not read any analyzable image. Please upload or paste the image again."
                )
            })
            yield "data: [DONE]\n\n"
            return
        image_instruction = image_gemma4.image_analysis_protocol(latest_user_msg, prepared_images)
        if final_messages and final_messages[0].get("role") == "system":
            final_messages[0] = dict(final_messages[0])
            final_messages[0]["content"] = f"{image_instruction}\n\n{final_messages[0].get('content', '')}"
        else:
            final_messages = [{"role": "system", "content": image_instruction}] + list(final_messages)

        encoded_images = [item["base64"] for item in prepared_images]
        if encoded_images:
            for i in range(len(final_messages) - 1, -1, -1):
                if final_messages[i].get("role") == "user":
                    final_messages[i] = dict(final_messages[i])
                    final_messages[i]["images"] = encoded_images
                    image_refs = image_gemma4.image_reference_lines(prepared_images)
                    if image_refs not in final_messages[i].get("content", ""):
                        final_messages[i]["content"] = (
                            f"{final_messages[i].get('content', '').strip()}\n\n"
                            f"[Uploaded image references]\n{image_refs}"
                        ).strip()
                    break
            yield _sse({"status": "analyzing_image"})

    answer_text = ""

    # === GGUF/Ollama Model (served by Ollama) ===
    if model_type in ("gguf", "ollama") or tokenizer == "ollama":
        if is_resume and final_messages and isinstance(final_messages[-1], dict) and final_messages[-1].get("role") == "assistant":
            last_msg = final_messages[-1]
            prompt_trick = f"Please continue your previous response EXACTLY from where you left off without repeating. Here is what you generated so far:\n{last_msg.get('content', '')}"
            final_messages = list(final_messages[:-1])
            final_messages.append({"role": "user", "content": prompt_trick})

        # ── Model selection: agent forces fast during analysis, think during generation ──
        _agent_mem     = pdf_agent.agent_memory.get(chat_id, {})
        _use_fast      = _agent_mem.get("use_fast_model", False) if agent_mode else False
        _ollama_model  = cfg.fast_model if _use_fast else (cfg.think_model if think_mode else cfg.fast_model)
        _is_think_call = (not _use_fast) and think_mode
        _ollama_has_think_tags = _model_supports_thinking(_ollama_model)

        if agent_mode and _use_fast:
            print(f"[AGENT SPEED] Analysis stage → fast_model (skip think tokens)")
        elif agent_mode:
            print(f"[AGENT SPEED] Generate stage → think_model (quality mode)")

        # ── Ollama GPU optimisation ──────────────────────────────
        # Only add the think-token budget when the model actually emits <think> tags.
        _think_budget = 768 if (_is_think_call and _ollama_has_think_tags) else 0
        _system_text = ""
        if final_messages and isinstance(final_messages[0], dict):
            _system_text = final_messages[0].get("content", "")
        _web_factual = web_mode and "MODE: 事实检索模式" in _system_text
        _web_no_sources = web_mode and "WEB SEARCH ATTEMPTED: No reliable live sources" in _system_text

        # --- KV-cache window: smaller window = less VRAM = faster generation ---
        # Agent (PDF) needs the full window for document context.
        # Web search needs room for search snippets.
        # Regular chat rarely exceeds 4 K tokens of useful context.
        if agent_mode:
            _ctx = response_profile["ctx"]
        elif web_mode:
            _ctx = min(response_profile["ctx"], 4096 if _web_factual else 6144)
        else:
            _ctx = response_profile["ctx"]

        # --- Output token cap per mode ---
        # Regular chat answers are almost never longer than 2048 tokens.
        # Web/agent responses can be longer but still capped to avoid runaway generation.
        if agent_mode:
            _max_predict = min(max_new_tok, response_profile["max_predict"]) + _think_budget
        elif web_mode:
            _web_cap = 384 if _web_no_sources else (2048 if _web_factual else 3072)
            _max_predict = min(max_new_tok, response_profile["max_predict"], _web_cap) + _think_budget
        else:
            _max_predict = min(max_new_tok, response_profile["max_predict"]) + _think_budget

        _is_gemma4 = "gemma4" in (_ollama_model or "").lower()
        if _is_gemma4 and web_mode:
            _temperature = 0.30 if _web_factual else 0.36
            _top_p = 0.88
        else:
            _temperature = 0.42 if _is_gemma4 else ms.TEMPERATURE
            _top_p = 0.90 if _is_gemma4 else ms.TOP_P
        _min_p = 0.03 if _is_gemma4 else 0.05
        _repeat_penalty = 1.08 if _is_gemma4 else ms.REPETITION_PENALTY
        _num_batch = 1024 if _ctx <= 8192 else 512

        _ollama_opts = {
            "temperature":    _temperature if ms.DO_SAMPLE else 0.0,
            "top_p":          _top_p if ms.DO_SAMPLE else 1.0,
            "top_k":          40,
            "min_p":          _min_p,
            "repeat_penalty": _repeat_penalty,
            "repeat_last_n":  128,
            "num_predict":    _max_predict,
            "num_ctx":        _ctx,
            "num_batch":      _num_batch,
            "num_gpu":        cfg.ollama_num_gpu,
            "num_thread":     cfg.ollama_num_thread,
            "use_mmap":       True,
        }

        _gen_started = time.perf_counter()
        yield _sse({"status": "model_starting"})
        ollama_stream = None

        gguf_all   = ""
        think_raw  = ""   # 思考内容（用于存档）
        answer_raw = ""   # 回答内容（用于存档 + 显示）



        # Only reasoning models such as DeepSeek/QwQ are expected to emit <think>.
        # Gemma and other standard instruct models should stream directly as answers.
        gguf_phase      = initial_phase if initial_phase else ("thinking" if _ollama_has_think_tags else "answering")
        gguf_sent_start = (gguf_phase == "answering")
        detected_think_tag = True if _ollama_has_think_tags else False

        try:
            ollama_stream = _ollama_client.chat(
                model=_ollama_model,
                messages=final_messages,
                stream=True,
                think=bool(_is_think_call and _ollama_has_think_tags),
                options=_ollama_opts,
                keep_alive="45m",
            )

            for chunk in ollama_stream:
                if await _client_gone():
                    print(f"[STREAM] Client disconnected; stopping Ollama stream for chat_id={chat_id}")
                    _close_stream(ollama_stream)
                    return

                if chunk.get("done"):
                    elapsed = time.perf_counter() - _gen_started
                    prompt_tokens = chunk.get("prompt_eval_count") or 0
                    output_tokens = chunk.get("eval_count") or 0
                    prompt_duration = (chunk.get("prompt_eval_duration") or 0) / 1_000_000_000
                    output_duration = (chunk.get("eval_duration") or 0) / 1_000_000_000
                    prompt_rate = prompt_tokens / prompt_duration if prompt_duration > 0 else 0
                    output_rate = output_tokens / output_duration if output_duration > 0 else 0
                    print(
                        f"[OLLAMA PERF] model={_ollama_model} web={web_mode} factual={_web_factual} "
                        f"ctx={_ctx} predict={_max_predict} elapsed={elapsed:.1f}s "
                        f"prompt={prompt_tokens} tok @ {prompt_rate:.1f} tok/s | "
                        f"output={output_tokens} tok @ {output_rate:.1f} tok/s"
                    )
                    continue

                piece = chunk['message']['content']
                if not piece:
                    continue
                gguf_all += piece

                # 动态探测非思考模式下的模型是否在吐出 <think>
                if detected_think_tag is None and not initial_phase and not think_mode:
                    if "<think>" in gguf_all:
                        detected_think_tag = True
                    elif len(gguf_all) >= 100:
                        detected_think_tag = False
                        # 确定该模型不吐出 <think>，立刻切换为回答模式并将累积内容当作正文
                        gguf_phase = "answering"
                        answer_raw += gguf_all
                        answer_text += gguf_all
                        yield _sse({'text': gguf_all})
                        continue
                    else:
                        # 长度不足100且还没看到 <think>，暂时缓存不发送
                        continue

                # ── 思考阶段 ────────────────────────────────────
                if gguf_phase == "thinking":

                    # 仅在 think_mode=True 时才发送 think_start
                    if think_mode and not gguf_sent_start:
                        yield _sse({'think_start': True})
                        gguf_sent_start = True

                    if '</think>' in gguf_all:
                        # 思考结束 → 切换到回答阶段
                        gguf_phase = "answering"
                        think_raw  = gguf_all.split('</think>', 1)[0]

                        if think_mode:
                            yield _sse({'think_end': True})

                        # </think> 之后的内容是实际回答
                        after = gguf_all.split('</think>', 1)[1].lstrip('\n')
                        if after:
                            answer_raw  += after
                            answer_text += after
                            yield _sse({'text': after})
                    else:
                        # 仍在思考中
                        if think_mode:
                            # 开启了思考模式 → 显示给用户
                            clean_piece = piece.replace('<think>', '').replace('</think>', '')
                            if clean_piece:
                                yield _sse({'text': clean_piece, 'thinking': True})
                        # think_mode=False → 静默跳过思考内容，不发送给前端

                # ── 回答阶段 ────────────────────────────────────
                elif gguf_phase == "answering":
                    answer_raw  += piece
                    answer_text += piece
                    yield _sse({'text': piece})

                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            print(f"[STREAM] Request cancelled; closing Ollama stream for chat_id={chat_id}")
            _close_stream(ollama_stream)
            return
        finally:
            _close_stream(ollama_stream)

        # ── 流结束后处理 ─────────────────────────────────────
        if gguf_phase == "thinking":
            # 模型没有输出 </think>，把所有内容当回答处理
            if think_mode:
                yield _sse({'think_end': True})
            answer_raw  = gguf_all.strip()
            answer_text = answer_raw

        # 构建存档文本
        # think_mode=True  → 保留思考标签，方便加载历史时显示思考面板
        # think_mode=False → 只保存回答部分，不污染历史记录
        if think_mode and think_raw.strip():
            raw_accum_text += f"<think>\n{think_raw.strip()}\n</think>\n{answer_raw.strip()}"
        else:
            raw_accum_text += answer_raw.strip()






    # === HuggingFace Model with PhaseStreamer ===
    else:
        input_text = ms.build_prompt(tokenizer, final_messages, think_mode=think_mode, is_resume=is_resume)
        inputs = tokenizer(input_text, return_tensors="pt", padding=True)
        input_ids = inputs["input_ids"].to(model.device)
        attention_mask = inputs["attention_mask"].to(model.device)

        streamer = PhaseStreamer(tokenizer, think_mode=think_mode, initial_phase=initial_phase)

        gen_kwargs = {
            "input_ids":      input_ids,
            "attention_mask": attention_mask,
            "max_new_tokens": max_new_tok + (512 if think_mode else 0),
            "pad_token_id":   tokenizer.pad_token_id,
            "streamer":       streamer,
        }
        if ms.DO_SAMPLE:
            gen_kwargs.update({"do_sample": True, "temperature": ms.TEMPERATURE,
                               "top_p": ms.TOP_P, "repetition_penalty": ms.REPETITION_PENALTY})
        else:
            gen_kwargs["do_sample"] = False
            gen_kwargs["repetition_penalty"] = ms.REPETITION_PENALTY

        thread = Thread(target=model.generate, kwargs=gen_kwargs)
        thread.start()

        for event in streamer:
            yield _sse(event)
            # Track ALL text including tags for DB
            if event.get('think_start'): 
                raw_accum_text += '<think>\n'
            if event.get('think_end'):
                if '<think>' not in raw_accum_text:
                    raw_accum_text = '<think>\n' + raw_accum_text
                raw_accum_text += '\n</think>\n'
                
            if 'text' in event:
                if event.get('thinking') and '<think>' not in raw_accum_text:
                    raw_accum_text += '<think>\n'
                raw_accum_text += event['text']
            await asyncio.sleep(0.005)

        thread.join()

    # === Force mandatory interactive prompt if LLM dropped it ===
    _mem = pdf_agent.agent_memory.get(chat_id, {})
    if (
        agent_mode
        and _mem.get("stage") == "wait_template"
        and _mem.get("generation_question_pending")
        and not _mem.get("generation_question_asked")
    ):
        _reply_lang = _mem.get("reply_lang", "en")
        _routing_q = pdf_agent.get_routing_question(_reply_lang)
        mandatory_q = (
            "\n\n---\n\n"
            + _routing_q
        )
        if _routing_q not in raw_accum_text:
            yield _sse({'text': mandatory_q})
            raw_accum_text += mandatory_q
            answer_text = (answer_text if answer_text else "") + mandatory_q
        if chat_id in pdf_agent.agent_memory:
            pdf_agent.agent_memory[chat_id]["generation_question_pending"] = False
            pdf_agent.agent_memory[chat_id]["generation_question_asked"] = True

    # === PDF Auto-Generation (Agent Mode) ===
    _mem = pdf_agent.agent_memory.get(chat_id, {}) if agent_mode else {}
    _pdf_filename = None
    if agent_mode and _mem.get("generate_pdf_now"):
        pdf_agent.agent_memory[chat_id]["generate_pdf_now"] = False
        pdf_source = answer_text if answer_text else raw_accum_text
        _doc_type  = _mem.get("doc_type", "general")
        
        # ── Last-resort placeholder sanitizer ──
        # Catch any [Value], [Amount], [X], [Name] etc. that the LLM failed to replace
        import re as _re
        pdf_source = _re.sub(r'\[(?:Value|value|Amount|amount|X|x|Name|name|数据|金额|数值)\]', 'N/A', pdf_source)
        
        print(f"[PDF GEN] Generating PDF, source_len={len(pdf_source)}, type={_doc_type}")
        try:
            _has_template = bool(_mem.get("template_data"))
            requested_pdf_name = skill_generator.extract_requested_file_stem(latest_user_msg, "pdf")
            _, _pdf_filename = await pdf_generator.markdown_to_pdf(
                pdf_source,
                _doc_type,
                is_template=_has_template,
                filename_stem=requested_pdf_name,
            )
            _set_file_owner(_pdf_filename, _stream_user_id, "generated")
            print(f"[PDF GEN] Done: {_pdf_filename}")
            # Advance agent stage to 'done' — but ONLY if still in 'generate'.
            # A newer request may have already reset the state (e.g., user uploaded new PDF).
            if chat_id in pdf_agent.agent_memory and pdf_agent.agent_memory[chat_id].get("stage") == "generate":
                pdf_agent.agent_memory[chat_id]["stage"] = "done"
                # Store generated PDF so Google Agent can email it later
                pdf_agent.agent_memory[chat_id]["last_generated_pdf"] = _pdf_filename
            yield _sse({
                "pdf_ready": True,
                "pdf_url":   f"/api/download_pdf/{_pdf_filename}",
                "pdf_name":  _pdf_filename,
            })
        except Exception as _pdf_err:
            print(f"[PDF Gen Error] {_pdf_err}")
            yield _sse({"text": f"\n\n\u26a0\ufe0f PDF generation failed: {_pdf_err}"})

        # Old inline tool parsing for Google Connectors has been removed and completely delegated to google_agent.py early intercept.

    # Save to DB (Full text including think content)
    new_msg = {"role": "assistant", "content": raw_accum_text.strip()}
    if sources:
        new_msg["sources"] = sources
    if _pdf_filename:
        new_msg["pdf_url"]  = f"/api/download_pdf/{_pdf_filename}"
        new_msg["pdf_name"] = _pdf_filename
        
    if is_resume and messages:
        chats_col.update_one(
            {"_id": chat_id},
            {"$set": {f"messages.{len(messages)-1}": new_msg}}
        )
    else:
        chats_col.update_one(
            {"_id": chat_id},
            {"$push": {"messages": new_msg}}
        )
        
    # Schedule Long-Term Memory Extraction
    if _stream_user_id:
        async def _bg_mem_cb(msgs):
            return await asyncio.to_thread(
                ms.generate_response, cfg.fast_model, tokenizer, msgs,
                think_mode=False, show_thinking=False, stream=False
            )
        _doc_id_for_memory = None
        if agent_mode:
            _doc_id_for_memory = pdf_agent.agent_memory.get(chat_id, {}).get("active_document_id")
        asyncio.create_task(
            memory_agent.extract_and_store_memory(
                _stream_user_id,
                list(messages) + [new_msg],
                _bg_mem_cb,
                document_id=_doc_id_for_memory,
            )
        )
        
    yield "data: [DONE]\n\n"


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest, request: Request):
    user = _optional_auth_user(request)
    authenticated_user_id = str(user["_id"]) if user else None
    if req.user_id and req.user_id != authenticated_user_id:
        raise HTTPException(status_code=403, detail="The requested user does not match the active session")
    if not user and (
        req.agent_mode
        or req.attachments
        or req.skill_type
        or req.regenerate_pdf
        or image_gemma4.is_image_generation_request(req.message or "")
    ):
        raise HTTPException(status_code=401, detail="Sign in to use files, skills, and Agent Mode")
    if user:
        _validate_owned_attachments(req.attachments, authenticated_user_id)

    client_ip = _extract_client_ip(request)
    user_country = _infer_country_from_locale_timezone(req.browser_language or "", req.user_timezone or "")
    chat_id = req.chat_id
    if user and not chat_id:
        chat_id = str(uuid.uuid4())
        title = req.message[:30] + ("..." if len(req.message) > 30 else "")
        chats_col.insert_one({
            "_id": chat_id, "user_id": authenticated_user_id,
            "title": title, "updated_at": datetime.utcnow(), "messages": [],
            "agent_mode": req.agent_mode
        })
    elif user:
        owner_query = {"_id": chat_id, "user_id": authenticated_user_id}
        if not chats_col.find_one(owner_query, {"_id": 1}):
            raise HTTPException(status_code=404, detail="Chat not found")
        chats_col.update_one(owner_query, {"$set": {"updated_at": datetime.utcnow()}})
    else:
        # Guests may use normal chat, but their transcript is never persisted.
        chat_id = chat_id or str(uuid.uuid4())

    if user:
        # A browser-provided transcript is untrusted: accepting it would let an
        # attacker manufacture legacy file references and later claim ownership.
        # The database is the canonical transcript for authenticated chats.
        chat_doc = chats_col.find_one({"_id": chat_id, "user_id": authenticated_user_id}) or {}
        messages = list(chat_doc.get("messages") or [])
        if not req.is_resume:
            user_message = {"role": "user", "content": req.message}
            if req.attachments:
                user_message["attachments"] = req.attachments
            chats_col.update_one(
                {"_id": chat_id, "user_id": authenticated_user_id},
                {"$push": {"messages": user_message}},
            )
            messages.append(user_message)
    elif req.messages is not None:
        # Guest sessions are deliberately non-persistent and cannot use files.
        messages = req.messages
    else:
        messages = [{"role": "user", "content": req.message}]

    async def wrapped():
        yield _sse({'chat_id': chat_id})
        # Agent mode: force think=True, web=False (hardcoded)
        _think = True if req.agent_mode else req.think_mode
        _web   = False if req.agent_mode else req.web_mode
        async for chunk in stream_generator(
            chat_id, messages,
            _think, _web,
            req.is_resume,
            max_tokens_override=req.max_tokens,
            agent_mode=req.agent_mode,
            attachments=req.attachments,
            user_timezone=req.user_timezone or "",
            client_request=request,
            regenerate_pdf=req.regenerate_pdf,
            browser_language=req.browser_language or "",
            client_ip=client_ip,
            user_country=user_country,
            skill_type=req.skill_type or "",
        ):
            yield chunk

    return StreamingResponse(wrapped(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/download_pdf/{filename}")
async def download_pdf(filename: str, request: Request):
    """Serve a generated PDF report for download directly from GridFS."""
    user_id = str(_auth_user(request)["_id"])
    import re
    if not re.match(r'^[\w\-\.]+\.pdf$', filename):
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
    
    try:
        file_doc = fs.find_one({"filename": filename})
        if not file_doc:
            return JSONResponse(status_code=404, content={"error": "File not found in database"})
        if not _user_can_access_file(user_id, filename):
            raise HTTPException(status_code=403, detail="You do not have access to this file")
            
        file_size = file_doc.length
        if file_size == 0:
            return JSONResponse(status_code=500, content={"error": "File is corrupted (0 bytes)."})
            
        return StreamingResponse(
            io.BytesIO(file_doc.read()),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{file_doc.filename}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"[PDF Download Error] {e}")
        return JSONResponse(status_code=500, content={"error": "PDF download failed"})



@app.post("/api/settings")
async def update_settings(req: SettingsRequest, request: Request):
    """Generation limits are deployment settings, not a user-controlled API."""
    _auth_user(request)
    raise HTTPException(status_code=403, detail="Server generation settings can only be changed through configuration")


@app.get("/api/settings")
async def get_settings(request: Request):
    """Return current generation settings to the frontend."""
    _auth_user(request)
    return JSONResponse(content={"max_new_tokens": ms.MAX_NEW_TOKENS})

@app.post("/api/chat/feedback")
async def chat_feedback(req: FeedbackRequest, request: Request):
    user_id = str(_auth_user(request)["_id"])
    try:
        if not chats_col.find_one({"_id": req.chat_id, "user_id": user_id}, {"_id": 1}):
            raise HTTPException(status_code=404, detail="Chat not found")
        query = {"chat_id": req.chat_id, "msg_index": req.msg_index, "user_id": user_id}
        if req.rating == 0:
            feedbacks_col.delete_one(query)
        else:
            feedbacks_col.update_one(
                query,
                {"$set": {"rating": req.rating, "updated_at": datetime.utcnow()}},
                upsert=True
            )
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Feedback Error] {e}")
        return JSONResponse(status_code=500, content={"error": "Could not save feedback"})

if __name__ == "__main__":
    cfg.print_summary()
    uvicorn.run("server:app", host=cfg.host, port=cfg.port, reload=False, log_level="warning", access_log=False)
