"""
pdf_agent.py  ─ Context-Aware Document Analysis Agent
======================================================
Stage flow
----------
init          → PDF received → wait_template
                Model: deep analysis + asks one routing question

 wait_template → template PDF uploaded   → generate  (with template, no second ask)
              → "直接生成" / "没有样板"  → generate  (auto-detected type layout)
              → unclear                  → re-ask

generate      → model outputs full structured report → generate_pdf_now = True
refine        → user requests changes               → generate_pdf_now = True
"""
import os
import sys
import re
import tempfile
import hashlib
import importlib.util as _ilu
import gridfs
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config_loader import cfg

# ─── PDF Parser ───────────────────────────────────────────────────────────────
# Fast path via PyMuPDF (fitz) to remove the heavy 15-30s CPU delay.
try:
    import pymupdf
except ImportError:
    pymupdf = None
_USE_MARKITDOWN = False

# ─── GridFS Connection (for file retrieval) ───────────────────────────────────
_mongo_client = MongoClient(cfg.mongo_uri)
_db = _mongo_client[cfg.mongo_database]
_fs = gridfs.GridFS(_db)

_prompts_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_agent_prompts.py")
_prompts_spec = _ilu.spec_from_file_location("pdf_agent_prompts", _prompts_path)
_prompts = _ilu.module_from_spec(_prompts_spec)
_prompts_spec.loader.exec_module(_prompts)

agent_memory: dict = {}

def reset_for_new_source(chat_id: str):
    """Fully reset agent memory for a chat when user uploads a brand-new source PDF.
    Call this BEFORE process_agent_request when we detect a new-source-PDF scenario
    (e.g. user paused generation and uploaded a different document)."""
    if chat_id in agent_memory:
        _log(f"[reset] Wiping agent memory for chat={chat_id}")
        del agent_memory[chat_id]

def _pdf_attachment_ids(attachments: list) -> list:
    return [
        att.get("saved_path", "")
        for att in (attachments or [])
        if att.get("saved_path", "").lower().endswith(".pdf")
    ]

def _make_document_id(file_ids: list) -> str:
    joined = "|".join(sorted(fid for fid in file_ids if fid))
    if not joined:
        return ""
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]

def _is_new_source_request(text: str) -> bool:
    low = (text or "").lower()
    compact = re.sub(r"\s+", "", low)
    source_words = [
        "新pdf", "新的pdf", "新文件", "新资料", "新文档", "重新分析", "分析这个",
        "分析新的", "换一个", "换成这个", "这是新的资料", "这是新pdf",
        "new pdf", "new document", "new file", "new source", "analyze this",
        "analyse this", "analyze the new", "analyse the new", "replace source",
    ]
    compact_words = [re.sub(r"\s+", "", w.lower()) for w in source_words]
    return any(w in low for w in source_words) or any(w in compact for w in compact_words)

def _mark_source_document(state: dict, attachments: list):
    file_ids = _pdf_attachment_ids(attachments)
    state["source_file_ids"] = file_ids
    state["active_document_id"] = _make_document_id(file_ids)
    state["new_source_loaded"] = True
    state["isolate_chat_history"] = True

def should_reset_for_new_pdf(chat_id: str, user_message: str, attachments: list) -> bool:
    """Return True when uploaded PDFs should start a fresh source-document analysis."""
    state = agent_memory.get(chat_id, {})
    if not state:
        return False
    new_pdf_ids = [
        fid for fid in _pdf_attachment_ids(attachments)
        if fid not in state.get("processed_files", set())
    ]
    if not new_pdf_ids:
        return False
    stage = state.get("stage")
    if stage in ("generate", "done"):
        return not _is_template_yes(user_message)
    if stage == "wait_template":
        return _is_new_source_request(user_message)
    return False

# ─── Keywords ─────────────────────────────────────────────────────────────────
_DIRECT_WORDS = [
    '直接', '直接生成', '没有', '无', 'no', 'none', 'default', 'skip',
    '不用', '不需要', 'without', 'proceed', 'just generate', '就行了',
    '随便', '默认', '直接做', '自动', '帮我生成', '生成吧', '可以了',
    '没有样板', '没有模板', '没有模版', '直接生成即可', '由你设计',
    '你来设计', '自主设计', '自由发挥',
]
def _is_direct(t): return any(w in t.lower() for w in _DIRECT_WORDS)

_GENERATE_PDF_WORDS = [
    '生成pdf', '生成最终pdf', '生成pdf报告', '制作pdf', '导出pdf',
    '生成报告', '最终报告', '生成最终报告', '开始生成', '现在生成',
    'generate pdf', 'create pdf', 'make pdf', 'export pdf',
    'generate the pdf', 'create the report', 'generate final report',
]
def _is_pdf_generation_request(t):
    low = (t or "").lower()
    compact = re.sub(r"\s+", "", low)
    return any(w in low for w in _GENERATE_PDF_WORDS) or any(w in compact for w in _GENERATE_PDF_WORDS)

def _is_explicit_pdf_output_request(t: str) -> bool:
    return _is_regenerate_request(t) or _is_pdf_generation_request(t) or _is_direct(t)

_TEMPLATE_YES_WORDS = [
    '是的', '按这个来', '按这个做', '照这个来', '按样板来', '按模板来',
    '按模版来', '用这个模板', '用这个模版', '就按这个', '确认', '开始吧',
    'yes', 'yep', 'yeah', 'sure', 'ok', 'okay', 'go ahead',
    'use this template', 'use this one', 'this template',
]
def _is_template_yes(t): return any(w in t.lower() for w in _TEMPLATE_YES_WORDS)

_TEMPLATE_ANSWER_WORDS = [
    '有模板', '有模版', '有样板', '有pdf模板', '有pdf模版',
    '使用模板', '使用模版', '用模板', '用模版', '按模板', '按模版',
    '不要模板', '不用模板', '不需要模板', '没有模板', '没有模版', '没有样板',
    '无模板', '无模版', '自动排版', '专业排版',
    'i have a template', 'use template', 'with template',
    'no template', 'without template', 'no sample', 'default layout',
    'professional layout',
]
def _has_generation_answer(t):
    low = (t or "").lower()
    compact = re.sub(r"\s+", "", low)
    compact_answers = [re.sub(r"\s+", "", w.lower()) for w in _TEMPLATE_ANSWER_WORDS]
    return (
        _is_pdf_generation_request(t)
        or _is_direct(t)
        or _is_template_yes(t)
        or any(w in low for w in _TEMPLATE_ANSWER_WORDS)
        or any(w in compact for w in compact_answers)
    )

# Keywords that signal user explicitly wants to regenerate/update the PDF
_REGENERATE_WORDS = [
    '重新生成', '再生成', '更新报告', '更新PDF', '更新pdf', '重新做', '重做',
    '再做一次', '再来一次', '重新制作', '修改报告', '修改PDF', '修改pdf',
    '再生成一次', '重新输出', '重新导出', '再导出', '更新一下报告',
    '多生成一次', '再来一版', '再出一版', '重出一版', '再来一个版本', '重新来一份',
    '再给我一版', '再做一份', '再生成多一次',
    '不满意', '改一下', '帮我修改', '修改', '改', '重写',
    'regenerate', 'redo', 'update pdf', 'update report', 'redo pdf',
    'recreate', 'generate again', 'make again', 'new pdf', 'new report',
    'revise report', 'revise pdf', 'modify report', 'modify pdf',
    'one more time', 'once more', 'again', 'do it again', 'make another one',
    'another version', 'rerender', 'render again', 'regenerate again',
]
def _is_regenerate_request(t):
    low = (t or "").lower()
    compact = re.sub(r"\s+", "", low)
    compact_words = [re.sub(r"\s+", "", w.lower()) for w in _REGENERATE_WORDS]
    if any(w in low for w in _REGENERATE_WORDS) or any(w in compact for w in compact_words):
        return True
    regen_patterns = [
        r"(再|重新|重).{0,8}(生成|导出|输出|做|出).{0,8}(pdf|报告|版本|一版)?",
        r"(one more time|once more|again).{0,12}(pdf|report|version|file)?",
        r"(redo|rerender|regenerate).{0,12}(again|pdf|report|version)?",
    ]
    return any(re.search(p, low) for p in regen_patterns)


# ─── Language Helpers ─────────────────────────────────────────────────────────
def _detect_reply_lang(text: str) -> str:
    """
    Detect the reply language from the latest user message.
    We keep this lightweight and deterministic for routing stability.
    """
    t = (text or "").strip()
    if not t:
        return "en"
    if re.search('[\u4e00-\u9fff]', t):
        return "zh"
    if re.search('[\u3040-\u30ff]', t):
        return "ja"
    if re.search('[\uac00-\ud7af]', t):
        return "ko"
    if re.search('[\u0600-\u06ff]', t):
        return "ar"
    if re.search('[\u0e00-\u0e7f]', t):
        return "th"
    words = re.findall(r"[a-zA-Z']+", t.lower())
    if words:
        malay_markers = {
            "saya", "nak", "tak", "boleh", "dengan", "yang", "tidak",
            "untuk", "dalam", "atau", "sudah", "akan", "dari", "juga",
            "kepada", "tolong", "kami", "kita", "awak", "mereka", "kalau",
        }
        spanish_markers = {
            "yo", "tu", "usted", "nosotros", "ellos", "una", "es", "que", "en", "con",
            "por", "para", "gracias", "hola", "puedes", "resumir", "este", "mi", "porfavor",
        }
        french_markers = {
            "je", "tu", "il", "elle", "nous", "vous", "ils", "une", "est", "que",
            "dans", "avec", "bonjour", "merci", "pouvez", "resumer", "ce", "mon",
        }
        german_markers = {
            "ich", "du", "er", "sie", "wir", "ihr", "der", "die", "das", "ist",
            "und", "mit", "nicht", "danke", "hallo", "kannst", "zusammenfassen", "dieses", "mein",
        }
        scores = {
            "ms": sum(1 for w in words if w in malay_markers),
            "es": sum(1 for w in words if w in spanish_markers),
            "fr": sum(1 for w in words if w in french_markers),
            "de": sum(1 for w in words if w in german_markers),
        }
        best_lang, best_score = max(scores.items(), key=lambda x: x[1])
        if best_score >= 2:
            return best_lang
    return "en"


def _language_rule(lang: str) -> str:
    return _prompts.language_rule(lang)


def get_routing_question(lang: str = "en") -> str:
    """Public helper used by server-side fallback injection as well."""
    return _prompts.get_routing_question(lang)

# ─── Logging ──────────────────────────────────────────────────────────────────
def _log(msg: str):
    print(f"[PDF Agent] {msg}")

def _table_to_markdown(rows) -> str:
    rows = rows or []
    rows = [
        [re.sub(r"\s+", " ", str(c or "")).strip() for c in row]
        for row in rows
        if row and any(str(c or "").strip() for c in row)
    ]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    md = "| " + " | ".join(header) + " |\n"
    md += "| " + " | ".join("---" for _ in header) + " |\n"
    for row in rows[1:]:
        md += "| " + " | ".join(row) + " |\n"
    return md

def _current_pdf_ids(attachments: list) -> set:
    return set(_pdf_attachment_ids(attachments))

def _latest_previous_pdf_attachments(history_messages: list, exclude_ids: set) -> list:
    """Return the most recent previous PDF attachment group from chat history."""
    exclude_ids = exclude_ids or set()
    for msg in reversed(history_messages or []):
        pdfs = [
            att for att in (msg.get("attachments") or [])
            if att.get("saved_path", "").lower().endswith(".pdf")
            and att.get("saved_path", "") not in exclude_ids
        ]
        if pdfs:
            return pdfs
    return []

def _recover_source_from_history(state: dict, history_messages: list, exclude_ids: set) -> bool:
    """Rebuild source PDF state after a server restart using persisted chat attachments."""
    if state.get("source_data"):
        return True
    previous_pdfs = _latest_previous_pdf_attachments(history_messages, exclude_ids)
    if not previous_pdfs:
        return False
    source_text, source_names = _extract_attachments(previous_pdfs)
    if not source_text.strip():
        return False
    state["source_data"] = source_text
    state["doc_type"] = _detect_doc_type(source_text)
    _mark_source_document(state, previous_pdfs)
    for att in previous_pdfs:
        if att.get("saved_path"):
            state["processed_files"].add(att.get("saved_path"))
    _log(
        f"[recover] Restored source from chat history: {', '.join(source_names) or 'previous PDF'}; "
        f"doc_type={state['doc_type']}, chars={len(source_text)}"
    )
    return True

# ─── GridFS File Resolution ───────────────────────────────────────────────────
def _resolve_pdf_from_gridfs(saved_path: str) -> str:
    """Download a PDF from GridFS to a temp file, return its real disk path."""
    try:
        file_doc = _fs.find_one({"filename": saved_path})
        if not file_doc:
            _log(f"[GridFS] File not found: {saved_path}")
            return ""
        tmp_path = os.path.join(tempfile.gettempdir(), saved_path)
        with open(tmp_path, "wb") as f:
            f.write(file_doc.read())
        _log(f"[GridFS] Extracted {saved_path} → {tmp_path} ({os.path.getsize(tmp_path)} bytes)")
        return tmp_path
    except Exception as e:
        _log(f"[GridFS resolve ERROR] {e}")
        return ""

# ─── PDF Extractor ────────────────────────────────────────────────────────────
def _extract_pdf(path: str) -> str:
    """
    Extract full text + table structures from PDF using PyMuPDF.
    Tables are converted to Markdown format for better model comprehension.
    """
    try:
        if pymupdf is None:
            raise RuntimeError("PyMuPDF is not installed")
        doc = pymupdf.open(path)
        parts = []
        for page_num, page in enumerate(doc, 1):
            text = page.get_text().strip()
            if text:
                parts.append(f"--- Page {page_num} ---\n{text}")
            # Extract table structures if available
            try:
                tables = page.find_tables()
                for ti, tbl in enumerate(tables.tables if hasattr(tables, 'tables') else tables):
                    try:
                        df = tbl.to_pandas()
                        if df is not None and not df.empty:
                            md_table = df.to_markdown(index=False)
                            parts.append(f"[Table {ti+1} from Page {page_num}]:\n{md_table}")
                    except Exception:
                        # Fallback: extract as raw cell data
                        extracted = tbl.extract()
                        md = _table_to_markdown(extracted)
                        if md:
                            parts.append(f"[Table {ti+1} from Page {page_num}]:\n{md}")
            except Exception as te:
                _log(f"[table extract] Page {page_num} table error (non-fatal): {te}")

        # Higher-fidelity table fallback. PyMuPDF is fast, but pdfplumber often
        # preserves financial statement cells and multi-column tables better.
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    try:
                        plumber_text = page.extract_text(x_tolerance=1, y_tolerance=3, layout=True) or ""
                        if plumber_text.strip():
                            parts.append(f"--- Page {page_num} layout text ---\n{plumber_text.strip()}")
                    except Exception:
                        pass
                    try:
                        tables = page.extract_tables() or []
                        for ti, rows in enumerate(tables[:12]):
                            md = _table_to_markdown(rows)
                            if md:
                                parts.append(f"[Precise Table {ti+1} from Page {page_num}]:\n{md}")
                    except Exception as pe:
                        _log(f"[pdfplumber table extract] Page {page_num} table error (non-fatal): {pe}")
        except Exception as pe:
            _log(f"[pdfplumber fallback] skipped: {pe}")
        
        result = "\n\n".join(parts)
        _log(f"[PyMuPDF] {path} → {len(result)} chars (with table structures)")
        return result
    except Exception as e:
        _log(f"[extract_pdf ERROR] {e}")
        return ""

def _extract_attachments(attachments: list) -> tuple:
    """Extract text from PDF attachments, resolving files from GridFS."""
    combined, names = "", []
    temp_files = []  # Track temp files for cleanup
    for att in (attachments or []):
        saved_path = att.get("saved_path", "")
        if not saved_path or not saved_path.lower().endswith(".pdf"):
            continue
        # Resolve from GridFS to a real temp file
        real_path = _resolve_pdf_from_gridfs(saved_path)
        if not real_path:
            _log(f"[extract] Could not resolve {saved_path} from GridFS")
            continue
        temp_files.append(real_path)
        text = _extract_pdf(real_path)
        _log(f"[extract] {saved_path} → {len(text)} chars")
        if text:
            name = att.get("original_name", os.path.basename(saved_path))
            combined += f"\n\n[Content from '{name}']:\n{text}\n"
            names.append(name)
    # Cleanup temp files
    for tmp in temp_files:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return combined, names

def _parse_template_layout(template_attachments: list) -> tuple:
    """Extract only layout/table skeletons from template PDFs; never template content values."""
    layout_report = ""
    table_structures = ""
    temp_files = []
    for att in (template_attachments or []):
        saved_path = att.get("saved_path", "")
        if not saved_path or not saved_path.lower().endswith(".pdf"):
            continue
        real_path = _resolve_pdf_from_gridfs(saved_path)
        if not real_path:
            _log(f"[template] Could not resolve {saved_path} from GridFS")
            continue
        temp_files.append(real_path)
        try:
            import pdfplumber
            with pdfplumber.open(real_path) as pdf:
                pages = len(pdf.pages)
                table_count = 0
                for pi, page in enumerate(pdf.pages):
                    found = page.find_tables()
                    table_count += len(found)
                    for ti, tbl in enumerate(found[:8]):
                        extracted = tbl.extract()
                        md = _table_to_markdown((extracted or [])[:3])
                        if md:
                            table_structures += f"\n--- Table {ti+1} (Page {pi+1}) ---\n{md}"
                has_tables = "yes" if table_count > 0 else "no"
                layout_report += (
                    f"\n[Layout Scan - {att.get('original_name', 'Doc')}]: "
                    f"Pages={pages}, Tables={table_count} ({has_tables} tables)\n"
                )
        except Exception as e:
            _log(f"[template pdfplumber error] {e}")
    for tmp in temp_files:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return layout_report, table_structures

# ─── Document Type Detection ──────────────────────────────────────────────────
def _detect_doc_type(text: str) -> str:
    """
    Returns one of: financial | annual_report | academic | legal |
                    medical   | business     | general
    """
    t = text.lower()

    def _has(*keywords):
        return any(k in t for k in keywords)

    # Annual report (check BEFORE financial — overlapping keywords)
    if _has("annual report", "laporan tahunan", "chairman's statement",
            "board of directors", "corporate governance", "dividend",
            "pengerusi", "ahli lembaga", "pemegang saham"):
        return "annual_report"

    # Financial statements
    if _has("balance sheet", "income statement", "profit and loss",
            "cash flow", "penyata kewangan", "penyata pendapatan",
            "aset semasa", "liabiliti", "ekuiti", "revenue", "ebitda",
            "earnings per share", "fiscal year", "financial statements",
            "kunci kira-kira", "현금흐름", "손익계산서", "财务报表",
            "资产负债", "损益表", "现金流量"):
        return "financial"

    # Academic / research
    if _has("abstract", "methodology", "literature review", "hypothesis",
            "conclusion", "references", "journal", "research objectives",
            "sample size", "p-value", "statistical analysis", "findings",
            "study design", "peer-reviewed", "citation"):
        return "academic"

    # Legal / contracts
    if _has("agreement", "contract", "terms and conditions", "herein",
            "whereas", "pursuant", "indemnify", "liability clause",
            "jurisdiction", "parties agree", "force majeure",
            "intellectual property", "non-disclosure", "termination clause"):
        return "legal"

    # Medical / clinical
    if _has("patient", "diagnosis", "clinical", "treatment", "symptoms",
            "dosage", "prescription", "hospital", "medical record",
            "pathology", "prognosis", "therapeutic", "healthcare"):
        return "medical"

    # Business / strategy
    if _has("market share", "swot", "business plan", "strategic objective",
            "kpi", "competitive advantage", "value proposition", "forecast",
            "go-to-market", "customer acquisition", "roi", "cagr"):
        return "business"

    return "general"


# ─── Type-Specific Structure Templates ───────────────────────────────────────
# Long report structure prompts live in pdf_agent_prompts.py.

# ─── Main Entry Point ─────────────────────────────────────────────────────────
def process_agent_request(chat_id: str, user_message: str, attachments: list, history_messages: list = None):
    if chat_id not in agent_memory:
        agent_memory[chat_id] = {
            "stage":            "init",
            "source_data":      "",
            "template_data":    "",
            "doc_type":         "general",
            "generate_pdf_now": False,
            "use_fast_model":   False,   # fast during analysis, think during generation
            "reply_lang":       "en",
            "processed_files":  set(),   # Track files to avoid reprocessing on regenerate
            "generation_question_pending": False,
            "generation_question_asked":   False,
            "generation_choice_answered":  False,
        }

    state = agent_memory[chat_id]
    if "processed_files" not in state:
        state["processed_files"] = set()
    state.setdefault("generation_question_pending", False)
    state.setdefault("generation_question_asked", False)
    state.setdefault("generation_choice_answered", False)
        
    state["generate_pdf_now"] = False
    if (user_message or "").strip():
        state["reply_lang"] = _detect_reply_lang(user_message)
    reply_lang = state.get("reply_lang", "en")
    routing_q = get_routing_question(reply_lang)
    lang_rule = _language_rule(reply_lang)
    _log(f"\n[turn] chat={chat_id} stage={state['stage']} msg={user_message[:80]!r}")

    # Only process attachments if they haven't been processed yet
    new_attachments = [att for att in (attachments or []) if att.get("saved_path") not in state["processed_files"]]
    new_text, new_names = _extract_attachments(new_attachments)
    current_pdf_ids = _current_pdf_ids(new_attachments or attachments)
    current_upload_is_template = bool(new_text and _is_template_yes(user_message))
    if current_upload_is_template and not state.get("source_data"):
        _recover_source_from_history(state, history_messages or [], current_pdf_ids)
    
    # Mark as processed
    for att in new_attachments:
        if att.get("saved_path"):
            state["processed_files"].add(att.get("saved_path"))

    instruction = ""

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE: init
    # ══════════════════════════════════════════════════════════════════════════
    if state["stage"] == "init":
        if new_text and current_upload_is_template and state.get("source_data"):
            layout_report, table_structures = _parse_template_layout(new_attachments)
            state["template_data"] = (
                layout_report + "\n\n[Template Table Structures]:\n" + table_structures
                if table_structures else layout_report
            )
            state["stage"]           = "generate"
            state["generate_pdf_now"]= True
            state["use_fast_model"]  = False
            state["generation_question_pending"] = False
            state["generation_question_asked"] = True
            state["generation_choice_answered"] = True
            tname = ", ".join(new_names) or "the template"
            _log("[recover] Current upload treated as template after restoring previous source data")
            instruction = _prompts.build_template_generation_instruction(
                tname, table_structures, layout_report, reply_lang
            )
        elif new_text and current_upload_is_template and not state.get("source_data"):
            layout_report, table_structures = _parse_template_layout(new_attachments)
            state["template_data"] = (
                layout_report + "\n\n[Template Table Structures]:\n" + table_structures
                if table_structures else layout_report
            )
            state["stage"] = "init"
            state["generate_pdf_now"] = False
            state["use_fast_model"] = False
            instruction = (
                "I received this PDF as a report template/layout reference. "
                "To generate the final PDF accurately, please upload the source data document that should fill this template. "
                "Do not upload another template yet."
            )
        elif new_text:
            state["source_data"] += new_text
            state["doc_type"]      = _detect_doc_type(state["source_data"])
            _mark_source_document(state, new_attachments)
            state["stage"]         = "wait_template"
            state["use_fast_model"]= False   # analysis: switch to think model for deeper deepseek analysis
            state["generation_question_pending"] = True
            state["generation_question_asked"] = False
            state["generation_choice_answered"] = False
            doc_list = ", ".join(new_names) or "the uploaded document"
            dtype    = state["doc_type"].replace("_", " ").title()
            _log(f"[agent] Detected doc_type={state['doc_type']} → think_model for deep analysis")
            instruction = _prompts.build_initial_analysis_instruction(routing_q, reply_lang)
        else:
            state["use_fast_model"] = False
            instruction = _prompts.build_no_document_instruction()

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE: wait_template
    # ══════════════════════════════════════════════════════════════════════════
    elif state["stage"] == "wait_template":
        if new_text:
            if not state.get("source_data"):
                _recover_source_from_history(state, history_messages or [], current_pdf_ids)

            layout_report, table_structures = _parse_template_layout(new_attachments)
            if not state.get("source_data"):
                state["template_data"] = (
                    layout_report + "\n\n[Template Table Structures]:\n" + table_structures
                    if table_structures else layout_report
                )
                state["stage"] = "init"
                state["generate_pdf_now"] = False
                state["use_fast_model"] = False
                instruction = (
                    "I received this PDF as the template/layout reference, but I cannot find the source data document in this chat. "
                    "Please upload the data PDF that should be analyzed and filled into this template."
                )
            else:
            # ── CRITICAL: Content-Structure Isolation ──
            # Store ONLY structural metadata from template, NOT its text content.
            # This prevents template placeholder data from contaminating the output.
                state["template_data"]  = layout_report + "\n\n[Template Table Structures]:\n" + table_structures if table_structures else layout_report
                state["stage"]           = "generate"
                state["generate_pdf_now"]= True
                state["use_fast_model"]  = False
                state["generation_question_pending"] = False
                state["generation_question_asked"] = True
                state["generation_choice_answered"] = True
                tname = ", ".join(new_names) or "the template"
                instruction = _prompts.build_template_generation_instruction(
                    tname, table_structures, layout_report, reply_lang
                )

        elif state.get("template_data") and _is_template_yes(user_message):
            state["stage"]           = "generate"
            state["generate_pdf_now"]= True
            state["use_fast_model"]  = False
            state["generation_question_pending"] = False
            state["generation_question_asked"] = True
            state["generation_choice_answered"] = True
            instruction = _prompts.build_existing_template_generation_instruction(reply_lang)

        else:
            state["stage"]           = "generate"
            state["generate_pdf_now"]= True
            state["use_fast_model"]  = False
            state["generation_question_pending"] = False
            state["generation_question_asked"] = True
            state["generation_choice_answered"] = True
            doc_type = state.get("doc_type", "general")
            structure = _prompts.get_structure(doc_type)
            instruction = _prompts.build_default_generation_instruction(
                doc_type, structure, user_message, reply_lang
            )

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE: wait_confirmation (backward compatibility for existing chats)
    # ══════════════════════════════════════════════════════════════════════════
    elif state["stage"] == "wait_confirmation":
        state["stage"]           = "generate"
        state["generate_pdf_now"]= True
        state["use_fast_model"]  = False
        state["generation_question_pending"] = False
        state["generation_question_asked"] = True
        state["generation_choice_answered"] = True
        instruction = _prompts.build_wait_confirmation_instruction(user_message, reply_lang)

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE: generate (first-time generation — PDF will be created this turn)
    # After PDF is generated, server.py will advance stage to "done".
    # ══════════════════════════════════════════════════════════════════════════
    elif state["stage"] == "generate":
        # If user uploaded a file, determine if it's a new template or new source data
        if new_text:
            if _is_template_yes(user_message):
                # It's a new template upload! Parse its layout, do NOT reset source data.
                layout_report, table_structures = _parse_template_layout(new_attachments)
                state["template_data"] = layout_report + "\n\n[Template Table Structures]:\n" + table_structures
                state["generate_pdf_now"] = True
                state["use_fast_model"] = False
                state["generation_question_pending"] = False
                state["generation_question_asked"] = True
                state["generation_choice_answered"] = True
                instruction = _prompts.build_template_regeneration_instruction(
                    state["template_data"], reply_lang
                )
            else:
                # It's a brand-new source PDF! Full reset for a fresh analysis cycle
                state["source_data"]      = new_text  # replace, not append
                state["template_data"]    = ""
                state["doc_type"]         = _detect_doc_type(new_text)
                _mark_source_document(state, new_attachments)
                state["stage"]            = "wait_template"
                state["generate_pdf_now"] = False
                state["use_fast_model"]   = False
                state["processed_files"]  = set(_pdf_attachment_ids(new_attachments))
                state["generation_question_pending"] = True
                state["generation_question_asked"] = False
                state["generation_choice_answered"] = False
                _log(f"[agent] New source PDF during generate → full reset to wait_template")
                instruction = _prompts.build_new_source_analysis_instruction(routing_q, reply_lang)
        else:
            # Normal first-time generation (triggered from wait_template)
            state["generate_pdf_now"] = True
            state["generation_question_pending"] = False
            state["generation_question_asked"] = True
            state["generation_choice_answered"] = True
            doc_type = state.get("doc_type", "general")
            _log(f"[agent] First-time generation → type={doc_type} generate_pdf_now=True")
            if _is_regenerate_request(user_message) or _is_pdf_generation_request(user_message):
                instruction = _prompts.build_done_regenerate_instruction(
                    doc_type,
                    bool(state.get("template_data")),
                    user_message,
                    _prompts.get_structure(doc_type),
                    reply_lang,
                )
            else:
                instruction = _prompts.build_generate_mode_instruction(
                    doc_type,
                    bool(state.get("template_data")),
                    _prompts.get_structure(doc_type),
                    reply_lang,
                )

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE: done (PDF already generated — conversation mode)
    # Only regenerate PDF if user explicitly requests it, uploads new files,
    # or provides a new template PDF.
    # ══════════════════════════════════════════════════════════════════════════
    elif state["stage"] == "done":
        doc_type = state.get("doc_type", "general")

        # Case 1: User uploaded a NEW PDF
        if new_text:
            if _is_template_yes(user_message):
                # It's a new template upload! Parse its layout, do NOT reset source data.
                layout_report, table_structures = _parse_template_layout(new_attachments)
                state["template_data"] = layout_report + "\n\n[Template Table Structures]:\n" + table_structures
                state["stage"]           = "generate"
                state["generate_pdf_now"]= True
                state["use_fast_model"]  = False
                state["generation_question_pending"] = False
                state["generation_question_asked"] = True
                state["generation_choice_answered"] = True
                instruction = _prompts.build_template_regeneration_instruction(
                    state["template_data"], reply_lang
                )
            elif _is_regenerate_request(user_message):
                layout_report, table_structures = _parse_template_layout(new_attachments)
                state["template_data"]   = layout_report + "\n\n[Template Table Structures]:\n" + table_structures
                state["stage"]           = "generate"
                state["generate_pdf_now"]= True
                state["use_fast_model"]  = False
                state["generation_question_pending"] = False
                state["generation_question_asked"] = True
                state["generation_choice_answered"] = True
                _log(f"[agent] New template + regenerate request in done stage → generate")
                instruction = _prompts.build_done_template_regeneration_instruction(
                    doc_type, _prompts.get_structure(doc_type), reply_lang
                )
            elif not _is_regenerate_request(user_message):
                # It's a brand-new source PDF! Reset to init for a fresh analysis cycle
                state["source_data"]     = new_text
                state["template_data"]   = ""
                state["doc_type"]        = _detect_doc_type(new_text)
                _mark_source_document(state, new_attachments)
                state["stage"]           = "wait_template"
                state["generate_pdf_now"]= False
                state["use_fast_model"]  = False
                state["processed_files"] = set(_pdf_attachment_ids(new_attachments))
                state["generation_question_pending"] = True
                state["generation_question_asked"] = False
                state["generation_choice_answered"] = False
                doc_list = ", ".join(new_names) or "the uploaded document"
                dtype    = state["doc_type"].replace("_", " ").title()
                _log(f"[agent] New source PDF in done stage → reset to wait_template for fresh analysis")
                instruction = _prompts.build_new_source_analysis_instruction(routing_q, reply_lang)

        # Case 3: User explicitly asked to regenerate/update PDF (no new file)
        elif _is_regenerate_request(user_message) or _is_pdf_generation_request(user_message):
            state["stage"]           = "generate"
            state["generate_pdf_now"]= True
            state["use_fast_model"]  = False
            state["generation_question_pending"] = False
            state["generation_question_asked"] = True
            state["generation_choice_answered"] = True
            _log(f"[agent] Explicit regenerate request in done stage → generate")
            instruction = _prompts.build_done_regenerate_instruction(
                doc_type,
                bool(state.get("template_data")),
                user_message,
                _prompts.get_structure(doc_type),
                reply_lang,
            )

        # Case 4: Normal follow-up question → just answer, NO PDF generation
        else:
            state["generate_pdf_now"] = False
            state["use_fast_model"]   = False
            _log(f"[agent] Follow-up question in done stage → text-only response, no PDF")
            instruction = _prompts.build_done_followup_instruction(doc_type, reply_lang)

    # ── Hidden context ────────────────────────────────────────────────────────
    ctx_parts = []
    if state["source_data"]:
        ctx_parts.append(
            f"<agent_memory_source_data>\n{state['source_data']}\n</agent_memory_source_data>"
        )
    if state.get("template_data"):
        ctx_parts.append(
            f"<agent_memory_template_data>\n{state['template_data']}\n</agent_memory_template_data>"
        )

    # Safety net: whenever the user explicitly asks to generate/regenerate the PDF
    # and we already have source data loaded, never fall back to a text-only reply.
    if (
        state.get("source_data")
        and _is_explicit_pdf_output_request(user_message)
        and state.get("stage") in ("done", "generate")
        and not new_text
    ):
        state["stage"] = "generate"
        state["generate_pdf_now"] = True
        state["use_fast_model"] = False
        state["generation_question_pending"] = False
        state["generation_question_asked"] = True
        state["generation_choice_answered"] = True
        instruction = _prompts.build_done_regenerate_instruction(
            state.get("doc_type", "general"),
            bool(state.get("template_data")),
            user_message,
            _prompts.get_structure(state.get("doc_type", "general")),
            reply_lang,
        )

    hidden_context = "\n\n" + "\n\n".join(ctx_parts) if ctx_parts else ""
    
    instruction = _prompts.apply_language_and_generation_rules(
        lang_rule, instruction, state.get("generate_pdf_now"), reply_lang
    )

    _log(f"[agent] → stage={state['stage']} doc_type={state.get('doc_type')} "
         f"generate_pdf_now={state['generate_pdf_now']}")
    return instruction, hidden_context

