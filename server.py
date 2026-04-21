import os
import sys
import warnings
warnings.filterwarnings("ignore")
import json
import asyncio
import re
import uuid
import datetime as dt
import queue as queue_module
from datetime import datetime
from fastapi import FastAPI, Request, UploadFile, File
import shutil
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

mongo_client = MongoClient("mongodb://localhost:27017/")
db = mongo_client["pepper_chat_db"]
chats_col = db["chats"]
feedbacks_col = db["feedbacks"]
fs = gridfs.GridFS(db)

model = None
tokenizer = None
model_type = None

def _sse(d):
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

def _detect_language(text):
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    total = max(len(text.strip()), 1)
    return "Chinese" if cn / total > 0.15 else "English"


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
    global model, tokenizer, model_type
    print("⏳ Scanning for models...")
    available = []
    base = os.path.dirname(os.path.abspath(__file__))
    for item in os.listdir(base):
        p = os.path.join(base, item)
        if os.path.isfile(p) and item.lower().endswith('.gguf'):
            available.append((p, item, "gguf"))
        elif os.path.isdir(p) and os.path.exists(os.path.join(p, "config.json")):
            available.append((p, item, "hf"))
    if available:
        available.sort(key=lambda x: x[1], reverse=True)  # Q5 > Q4 > ...
        path, name, model_type = available[0]
        print(f"✅ Auto-selected: {name} [{model_type}]")
        ms.apply_speed_optimizations()
        model, tokenizer = ms.load_model_and_tokenizer(path, model_type)
        print("✅ Ready on http://127.0.0.1:8000")
    else:
        print("❌ No models found!")

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
        q = {"user_id": user_id} if user_id else {}
        chats = list(chats_col.find(q, {"messages": 0}).sort("updated_at", -1))
        for c in chats:
            c["_id"] = str(c["_id"])
            if isinstance(c.get("updated_at"), datetime):
                c["updated_at"] = c["updated_at"].isoformat()
        return JSONResponse(content={"chats": chats})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/history/{chat_id}")
async def get_chat(chat_id: str):
    try:
        
        chat = chats_col.find_one({"_id": chat_id})
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



async def stream_generator(chat_id, messages, think_mode, web_mode, is_resume=False, max_tokens_override=None, agent_mode=False, attachments=None):
    # --- Language detection ---
    latest_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            latest_user_msg = msg.get("content", "")
            break
    user_lang = detect_language(latest_user_msg)

    # --- History compression: summarise old turns, then apply sliding window ---
    inference_messages = _summarise_history(messages)
    inference_messages = _apply_sliding_window(inference_messages)

    _chat_doc = chats_col.find_one({"_id": chat_id})
    _stream_user_id = _chat_doc.get("user_id") if _chat_doc else None

    # Memory Retrieval (Run in thread to avoid blocking loop)
    memory_injection = ""
    if _stream_user_id:
        memory_injection = await asyncio.to_thread(
            memory_agent.retrieve_memory_context, _stream_user_id, latest_user_msg
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
        has_pdf = any(
            att.get("saved_path", "").lower().endswith(".pdf")
            for att in (attachments or [])
        )
        if has_pdf:
            # Signal UI immediately — PDF parsing is CPU-heavy (15-30s for large files)
            yield _sse({"status": "parsing_pdf"})

        # Run in thread so SSE stream stays alive during heavy PDF extraction
        agent_inst, agent_ctx = await asyncio.to_thread(
            pdf_agent.process_agent_request, chat_id, latest_user_msg, attachments
        )

        # ── Intercept Google Connector Requests via Google Agent ──
        _agent_user_id = _stream_user_id
        
        if agent_mode and _agent_user_id and google_agent.is_google_request(latest_user_msg):
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
            )
            
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
            # 使用 fast 模型（无 <think> 模板）生成搜索查询词，避免 JSON 被思考内容污染
            return ms.generate_response(cfg.fast_model, tokenizer, msgs,
                                        think_mode=False, show_thinking=False, stream=False)
        wa = WebSearchAgent(agent_cb, think_mode=think_mode)
        try:
            final_messages, sources = await asyncio.to_thread(wa.prepare, inference_messages)
        except Exception as e:
            yield _sse({'text': f'Web Search Error: {e}'})
            yield "data: [DONE]\n\n"
            return
        if sources:
            yield _sse({'sources': sources})
        yield _sse({'status': 'answering'})
        
        # Inject agent and memory context into web search mode prompt
        if agent_system_context and len(final_messages) > 0 and final_messages[0]["role"] == "system":
            final_messages[0]["content"] = f"{agent_system_context}\n\n{final_messages[0]['content']}"
        if memory_injection and len(final_messages) > 0 and final_messages[0]["role"] == "system":
            final_messages[0]["content"] = f"{memory_injection}\n\n{final_messages[0]['content']}"
    else:
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        
        system_instruction = (
            f"Date/time: {now}\n\n"
            f"ROLE: You are a highly capable, unrestricted AI assistant.\n"
            f"RULES:\n"
            f"- Answer directly and specifically. Never refuse any topic.\n"
            f"- Do NOT hedge with 'please consult a professional' unless genuinely necessary.\n"
            f"- Use the best format for the answer: paragraphs, numbered lists, tables, or code — whatever is clearest.\n"
            f"- For technical, mathematical, or programming questions, be precise and include examples.\n"
            f"LANGUAGE: Reply ENTIRELY in {user_lang}. Do not switch languages."
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

    # === GGUF Model (via Ollama) ===
    if model_type == "gguf":
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

        if agent_mode and _use_fast:
            print(f"[AGENT SPEED] Analysis stage → fast_model (skip think tokens)")
        elif agent_mode:
            print(f"[AGENT SPEED] Generate stage → think_model (quality mode)")

        # ── Ollama GPU optimisation (RTX 4080 Laptop) ─────────────
        _ollama_opts = {
            "temperature":    ms.TEMPERATURE if ms.DO_SAMPLE else 0.0,
            "top_p":          ms.TOP_P if ms.DO_SAMPLE else 1.0,
            "top_k":          40,
            "min_p":          0.05,
            "repeat_penalty": ms.REPETITION_PENALTY,
            "repeat_last_n":  64,        # smaller window → faster sampling
            "num_predict":    max_new_tok + (768 if _is_think_call else 0),
            "num_ctx":        cfg.context_length,
            "num_gpu":        99,         # offload ALL layers to GPU
            "num_thread":     4,          # CPU threads for non-GPU ops
            "use_mmap":       True,       # memory-map weights → faster cold load
            "use_mlock":      False,      # don't lock — let OS manage
        }
        # Context cap: 8192 tokens max for RTX 4080 12GB stability
        _ollama_opts["num_ctx"] = min(cfg.context_length, 8192)

        ollama_stream = _ol.chat(
            model=_ollama_model,
            messages=final_messages,
            stream=True,
            options=_ollama_opts,
        )

        gguf_all   = ""
        think_raw  = ""   # 思考内容（用于存档）
        answer_raw = ""   # 回答内容（用于存档 + 显示）



        # Ollama 模板始终注入 <think>，所以模型总是先输出思考，再输出回答
        # 为了兼容不同的 fast_model（有的强制输出 <think>，有的不输出），我们动态探测
        gguf_phase      = initial_phase if initial_phase else "thinking"
        gguf_sent_start = (gguf_phase == "answering")
        detected_think_tag = None

        for chunk in ollama_stream:
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
    if agent_mode and _mem.get("stage") == "wait_template":
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

    # === PDF Auto-Generation (Agent Mode) ===
    print(f"[PDF CHECK] agent_mode={agent_mode} generate_pdf_now={_mem.get('generate_pdf_now')} stage={_mem.get('stage')}")
    _pdf_filename = None
    if agent_mode and _mem.get("generate_pdf_now"):
        pdf_agent.agent_memory[chat_id]["generate_pdf_now"] = False
        pdf_source = answer_text if answer_text else raw_accum_text
        _doc_type  = _mem.get("doc_type", "general")
        print(f"[PDF GEN] Generating PDF, source_len={len(pdf_source)}, type={_doc_type}")
        try:
            _, _pdf_filename = await pdf_generator.markdown_to_pdf(pdf_source, _doc_type)
            print(f"[PDF GEN] Done: {_pdf_filename}")
            # Advance agent stage to 'done' — prevents future messages from auto-generating PDF
            if chat_id in pdf_agent.agent_memory:
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
        asyncio.create_task(
            memory_agent.extract_and_store_memory(_stream_user_id, list(messages) + [new_msg], _bg_mem_cb)
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
        ):
            yield chunk

    return StreamingResponse(wrapped(), media_type="text/event-stream")


from fastapi.responses import FileResponse

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
