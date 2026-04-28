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

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "interface functions"))
from auth import auth_router

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)
sys.path.append(os.path.join(_BASE, "Model Networking"))

# ── Load central config ──────────────────────────────────
from config_loader import cfg
os.environ.setdefault("OLLAMA_HOST", cfg.ollama_base_url)
_ollama_client = _ol.Client(host=cfg.ollama_base_url)

import Model_StartUp as ms

try:
    from web_agent import WebSearchAgent, detect_language
except ImportError:
    WebSearchAgent = None
    def detect_language(text): return "English"

app = FastAPI()

app.include_router(auth_router)

static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

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

mongo_client = MongoClient(cfg.mongo_uri)
db = mongo_client[cfg.mongo_database]
chats_col = db["chats"]
feedbacks_col = db["feedbacks"]
fs = gridfs.GridFS(db)

model = None
tokenizer = None
model_type = None
_think_mode_supported = False   # resolved during startup from actual model name

def _sse(d):
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

def _detect_language(text):
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    total = max(len(text.strip()), 1)
    return "Chinese" if cn / total > 0.15 else "English"

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
            "ctx": cfg.ollama_num_ctx_cap if has_pdf else min(cfg.ollama_num_ctx_cap, 12288),
            "instruction": (
                "ANSWER DEPTH: Agent mode should be action-oriented and complete. "
                "Be concise while planning, but provide a substantial final result with clear sections, "
                "tables when useful, and no filler."
            ),
        }
    if web_mode:
        return {
            "depth": "web_deep" if score >= 2 else "web_standard",
            "max_predict": 3072 if score >= 2 else 2048,
            "ctx": min(cfg.ollama_num_ctx_cap, 8192),
            "instruction": (
                "ANSWER DEPTH: Use the live sources to produce a grounded answer. "
                "For simple lookup questions, answer briefly with citations. "
                "For business, legal, financial, or comparison questions, synthesize the evidence thoroughly."
            ),
        }
    if score <= 0:
        return {
            "depth": "short",
            "max_predict": 768,
            "ctx": min(cfg.ollama_num_ctx_cap, 4096),
            "instruction": "ANSWER DEPTH: This appears simple. Answer directly in 2-5 concise paragraphs or bullets.",
        }
    if score <= 2:
        return {
            "depth": "standard",
            "max_predict": 1536,
            "ctx": min(cfg.ollama_num_ctx_cap, 4096),
            "instruction": "ANSWER DEPTH: Give a balanced answer with enough detail to be useful, avoiding unnecessary length.",
        }
    return {
        "depth": "deep",
        "max_predict": 3072,
        "ctx": min(cfg.ollama_num_ctx_cap, 6144),
        "instruction": "ANSWER DEPTH: This is complex. Provide a high-quality structured answer with reasoning, examples, and tables where useful.",
    }


def _model_supports_thinking(model_name: str) -> bool:
    name = (model_name or "").lower()
    _non_thinking = ("gemma",)
    if any(m in name for m in _non_thinking):
        return False
    return any(marker in name for marker in ("deepseek", "qwq", "qwen3", "qwen-3", "reasoning"))


def _generate_search_query_response(messages) -> str:
    """Use a tiny utility model for web-search query planning when available."""
    if tokenizer == "ollama" or model_type in ("gguf", "ollama"):
        tried = set()
        for query_model in (cfg.search_query_model, cfg.fast_model):
            if not query_model or query_model in tried:
                continue
            tried.add(query_model)
            try:
                resp = _ollama_client.chat(
                    model=query_model,
                    messages=messages,
                    stream=False,
                    options={
                        "temperature": 0.1,
                        "top_p": 0.9,
                        "num_predict": 256,
                        "num_ctx": 2048,
                        "num_gpu": cfg.ollama_num_gpu,
                        "num_thread": cfg.ollama_num_thread,
                        "use_mmap": True,
                        "use_mlock": False,
                    },
                )
                msg = resp.get("message") if isinstance(resp, dict) else getattr(resp, "message", None)
                content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
                if content:
                    return content
            except Exception as exc:
                print(f"  ⚠️ Search query model '{query_model}' unavailable: {exc}")

    return ms.generate_response(
        cfg.fast_model, tokenizer, messages,
        think_mode=False, show_thinking=False, stream=False
    )


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
    return {
        "profile": cfg.profile,
        "google_oauth_client_id": cfg.google_oauth_client_id,
        "public_site_url": cfg.public_site_url,
        "think_model": cfg.think_model,
        "supports_think_mode": _think_mode_supported,
    }


@app.post("/api/upload_files")
async def upload_files_endpoint(files: List[UploadFile] = File(...)):
    try:
        saved_files = []
        for file in files:
            file_id = str(uuid.uuid4())
            ext = os.path.splitext(file.filename)[1]
            safe_name = f"{file_id}{ext}"
            
            file_content = await file.read()
            file_size = len(file_content)
            
            # Save directly to MongoDB GridFS
            fs.put(file_content, filename=safe_name, content_type=file.content_type)
            
            saved_files.append({
                "file_id": file_id,
                "original_name": file.filename,
                "saved_path": safe_name,
                "url": f"/uploads/{safe_name}",
                "size": file_size,
                "content_type": file.content_type
            })
            
        return JSONResponse(content={"status": "success", "files": saved_files})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/uploads/{filename}")
async def get_uploaded_file(filename: str):
    try:
        file_doc = fs.find_one({"filename": filename})
        if not file_doc:
            return JSONResponse(status_code=404, content={"error": "File not found"})
        return StreamingResponse(
            io.BytesIO(file_doc.read()), 
            media_type=file_doc.content_type, 
            headers={"Content-Disposition": f"inline; filename={file_doc.filename}"}
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
@app.get("/api/history")
async def get_history(user_id: Optional[str] = None):
    try:
        if not user_id:
            return JSONResponse(content={"chats": []})
        q = {"user_id": user_id}
        chats = list(chats_col.find(q, {"messages": 0}).sort("updated_at", -1))
        for c in chats:
            c["_id"] = str(c["_id"])
            if isinstance(c.get("updated_at"), datetime):
                c["updated_at"] = c["updated_at"].isoformat()
        return JSONResponse(content={"chats": chats})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/history/{chat_id}")
async def get_chat(chat_id: str, user_id: Optional[str] = None):
    try:
        q = {"_id": chat_id, "user_id": user_id} if user_id else {"_id": chat_id, "user_id": None}
        chat = chats_col.find_one(q)
        if chat:
            chat["_id"] = str(chat["_id"])
            if isinstance(chat.get("updated_at"), datetime):
                chat["updated_at"] = chat["updated_at"].isoformat()
            
            fb_docs = feedbacks_col.find({"chat_id": chat_id})
            chat["feedback"] = {str(doc["msg_index"]): doc["rating"] for doc in fb_docs}
            
            return JSONResponse(content={"chat": chat})
        return JSONResponse(status_code=404, content={"error": "Not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.delete("/api/history/{chat_id}")
async def delete_chat(chat_id: str):
    try:
        result = chats_col.delete_one({"_id": chat_id})
        if result.deleted_count > 0:
            return JSONResponse(content={"status": "success"})
        return JSONResponse(status_code=404, content={"error": "Not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
class RenameChatRequest(BaseModel):
    title: str

@app.put("/api/history/{chat_id}")
async def rename_chat(chat_id: str, req: RenameChatRequest):
    try:
        result = chats_col.update_one({"_id": chat_id}, {"$set": {"title": req.title}})
        if result.matched_count > 0:
            return JSONResponse(content={"status": "success", "title": req.title})
        return JSONResponse(status_code=404, content={"error": "Not found"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})



async def stream_generator(chat_id, messages, think_mode, web_mode, is_resume=False, max_tokens_override=None, agent_mode=False, attachments=None, user_timezone=""):
    # --- Language detection ---
    latest_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            latest_user_msg = msg.get("content", "")
            break
    user_lang = detect_language(latest_user_msg)
    has_pdf = any(
        att.get("saved_path", "").lower().endswith(".pdf")
        for att in (attachments or [])
    )
    response_profile = _response_profile(latest_user_msg, agent_mode=agent_mode, web_mode=web_mode, has_pdf=has_pdf)

    # --- History compression: summarise old turns, then apply sliding window ---
    if agent_mode:
        inference_messages = list(messages)
    else:
        inference_messages = _summarise_history(messages)
        inference_messages = _apply_sliding_window(inference_messages)

    _chat_doc = chats_col.find_one({"_id": chat_id})
    _stream_user_id = _chat_doc.get("user_id") if _chat_doc else None
    _pre_agent_state = pdf_agent.agent_memory.get(chat_id, {}) if agent_mode else {}
    _active_document_id = _pre_agent_state.get("active_document_id") if _pre_agent_state else None

    # Memory Retrieval (Run in thread to avoid blocking loop)
    memory_injection = ""
    if _stream_user_id and not (agent_mode and has_pdf):
        memory_injection = await asyncio.to_thread(
            memory_agent.retrieve_memory_context,
            _stream_user_id,
            latest_user_msg,
            3,
            _active_document_id if agent_mode else None,
        )

    final_messages = inference_messages
    raw_accum_text = ""
    initial_phase  = None
    if is_resume and messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "assistant":
        raw_accum_text = messages[-1].get("content", "")
        if think_mode:
            initial_phase = "answering" if "</think>" in raw_accum_text else "thinking"

    # Resolve effective max tokens
    max_new_tok = max_tokens_override if max_tokens_override else ms.MAX_NEW_TOKENS
    
    agent_system_context = ""
    if agent_mode:
        if has_pdf:
            # Signal UI immediately — PDF parsing is CPU-heavy (15-30s for large files)
            yield _sse({"status": "parsing_pdf"})

        # ── Pre-flight: If a fresh source PDF arrives, wipe old per-document state first. ──
        if has_pdf and pdf_agent.should_reset_for_new_pdf(chat_id, latest_user_msg, attachments):
            pdf_agent.reset_for_new_source(chat_id)

        # Run in thread so SSE stream stays alive during heavy PDF extraction
        agent_inst, agent_ctx = await asyncio.to_thread(
            pdf_agent.process_agent_request, chat_id, latest_user_msg, attachments
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
            # Also check recent chat messages for a PDF attachment
            if not _agent_mem_pdf:
                for m in reversed(messages[-10:]):
                    if m.get("pdf_name"):
                        _agent_mem_pdf = m["pdf_name"]
                        break
            
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

    sources = []

    # Web mode
    if web_mode:
        if WebSearchAgent is None:
            yield _sse({'text': 'Error: WebSearchAgent not available.'})
            yield "data: [DONE]\n\n"
            return
        yield _sse({'status': 'searching'})
        def agent_cb(msgs):
            # Query planning is a tiny JSON task; keep it off the 31B model when possible.
            return _generate_search_query_response(msgs)
        wa = WebSearchAgent(agent_cb, think_mode=think_mode)
        try:
            final_messages, sources = await asyncio.to_thread(wa.prepare, inference_messages)
        except Exception as e:
            yield _sse({'text': f'Web Search Error: {e}'})
            yield "data: [DONE]\n\n"
            return
        yield _sse({'sources': sources})
        yield _sse({'status': 'answering'})
        
        # Inject identity, language, agent context, and memory into web mode system prompt
        _web_additions = []
        _identity_lang = (
            f"IDENTITY: Your name is Pepper Labs AI. You are an AI assistant created and trained by Pepper Labs. "
            f"If asked who you are, always say: 'I am Pepper Labs AI, an AI assistant built by Pepper Labs.'\n"
            f"LANGUAGE: Detect the language of the user's message and reply in that exact same language. "
            f"Only use Chinese (中文), English, or Malay (Bahasa Malaysia). Never use any other language."
        )
        _web_additions.append(_identity_lang)
        _web_additions.append(response_profile["instruction"])
        if agent_system_context:
            _web_additions.append(agent_system_context)
        if memory_injection:
            _web_additions.append(memory_injection)
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

        final_messages = [{"role": "system", "content": system_instruction}] + list(inference_messages)

    if model is None:
        yield _sse({'text': 'Error: Model not loaded.'})
        yield "data: [DONE]\n\n"
        return

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

        # --- KV-cache window: smaller window = less VRAM = faster generation ---
        # Agent (PDF) needs the full window for document context.
        # Web search needs room for search snippets.
        # Regular chat rarely exceeds 4 K tokens of useful context.
        if agent_mode:
            _ctx = response_profile["ctx"]
        elif web_mode:
            _ctx = min(response_profile["ctx"], 6144 if _web_factual else 8192)
        else:
            _ctx = response_profile["ctx"]

        # --- Output token cap per mode ---
        # Regular chat answers are almost never longer than 2048 tokens.
        # Web/agent responses can be longer but still capped to avoid runaway generation.
        if agent_mode:
            _max_predict = min(max_new_tok, response_profile["max_predict"]) + _think_budget
        elif web_mode:
            _max_predict = min(max_new_tok, response_profile["max_predict"], 1536 if _web_factual else 3072) + _think_budget
        else:
            _max_predict = min(max_new_tok, response_profile["max_predict"]) + _think_budget

        _ollama_opts = {
            "temperature":    ms.TEMPERATURE if ms.DO_SAMPLE else 0.0,
            "top_p":          ms.TOP_P if ms.DO_SAMPLE else 1.0,
            "top_k":          40,
            "min_p":          0.05,
            "repeat_penalty": ms.REPETITION_PENALTY,
            "repeat_last_n":  64,
            "num_predict":    _max_predict,
            "num_ctx":        _ctx,
            "num_batch":      512,        # 默认批次 — 避免 prefill 占用过多显存
            "num_gpu":        cfg.ollama_num_gpu,
            "num_thread":     cfg.ollama_num_thread,
            "f16_kv":         True,       # fp16 KV cache → KV 显存占用减半
            "use_mmap":       True,
            "use_mlock":      False,
        }

        _gen_started = time.perf_counter()
        ollama_stream = _ollama_client.chat(
            model=_ollama_model,
            messages=final_messages,
            stream=True,
            options=_ollama_opts,
        )

        gguf_all   = ""
        think_raw  = ""   # 思考内容（用于存档）
        answer_raw = ""   # 回答内容（用于存档 + 显示）



        # Only reasoning models such as DeepSeek/QwQ are expected to emit <think>.
        # Gemma and other standard instruct models should stream directly as answers.
        gguf_phase      = initial_phase if initial_phase else ("thinking" if _ollama_has_think_tags else "answering")
        gguf_sent_start = (gguf_phase == "answering")
        detected_think_tag = True if _ollama_has_think_tags else False

        for chunk in ollama_stream:
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
            _, _pdf_filename = await pdf_generator.markdown_to_pdf(pdf_source, _doc_type, is_template=_has_template)
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
async def chat_endpoint(req: ChatRequest):
    chat_id = req.chat_id
    if not chat_id:
        chat_id = str(uuid.uuid4())
        title = req.message[:30] + ("..." if len(req.message) > 30 else "")
        chats_col.insert_one({
            "_id": chat_id, "user_id": req.user_id,
            "title": title, "updated_at": datetime.utcnow(), "messages": [],
            "agent_mode": req.agent_mode
        })
    else:
        chats_col.update_one({"_id": chat_id}, {"$set": {"updated_at": datetime.utcnow()}})
        
    if req.messages is not None:
        chats_col.update_one({"_id": chat_id}, {"$set": {"messages": req.messages}})
        messages = req.messages
    else:
        chats_col.update_one({"_id": chat_id},
                             {"$push": {"messages": {"role": "user", "content": req.message}}})
        chat_doc = chats_col.find_one({"_id": chat_id})
        messages = chat_doc["messages"] if chat_doc else [{"role": "user", "content": req.message}]

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
        ):
            yield chunk

    return StreamingResponse(wrapped(), media_type="text/event-stream")


@app.get("/api/download_pdf/{filename}")
async def download_pdf(filename: str):
    """Serve a generated PDF report for download directly from GridFS."""
    import re
    if not re.match(r'^[\w\-\.]+\.pdf$', filename):
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
    
    try:
        file_doc = fs.find_one({"filename": filename})
        if not file_doc:
            return JSONResponse(status_code=404, content={"error": "File not found in database"})
            
        file_size = file_doc.length
        if file_size == 0:
            return JSONResponse(status_code=500, content={"error": "File is corrupted (0 bytes)."})
            
        print(f"[Download] Serving {filename} from GridFS ({file_size} bytes)")
        return StreamingResponse(
            io.BytesIO(file_doc.read()), 
            media_type="application/pdf", 
            headers={"Content-Disposition": f"attachment; filename={file_doc.filename}"}
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})



@app.post("/api/settings")
async def update_settings(req: SettingsRequest):
    """Allow the frontend to update server-side generation settings."""
    if req.max_new_tokens is not None:
        if not 64 <= req.max_new_tokens <= 8192:
            return JSONResponse(status_code=400, content={"error": "max_new_tokens must be 64-8192"})
        ms.MAX_NEW_TOKENS = req.max_new_tokens
        return JSONResponse(content={"status": "ok", "max_new_tokens": ms.MAX_NEW_TOKENS})
    return JSONResponse(content={"status": "ok", "max_new_tokens": ms.MAX_NEW_TOKENS})


@app.get("/api/settings")
async def get_settings():
    """Return current generation settings to the frontend."""
    return JSONResponse(content={"max_new_tokens": ms.MAX_NEW_TOKENS})

@app.post("/api/chat/feedback")
async def chat_feedback(req: FeedbackRequest):
    try:
        if req.rating == 0:
            feedbacks_col.delete_one({"chat_id": req.chat_id, "msg_index": req.msg_index})
        else:
            feedbacks_col.update_one(
                {"chat_id": req.chat_id, "msg_index": req.msg_index},
                {"$set": {"rating": req.rating, "updated_at": datetime.utcnow()}},
                upsert=True
            )
        return {"status": "success"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

if __name__ == "__main__":
    cfg.print_summary()
    uvicorn.run("server:app", host=cfg.host, port=cfg.port, reload=False, log_level="warning", access_log=False)
