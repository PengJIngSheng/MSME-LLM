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
import re
import tempfile
import gridfs
from pymongo import MongoClient

# ─── PDF Parser ───────────────────────────────────────────────────────────────
# Fast path via PyMuPDF (fitz) to remove the heavy 15-30s CPU delay.
try:
    import pymupdf
except ImportError:
    pymupdf = None
_USE_MARKITDOWN = False

# ─── GridFS Connection (for file retrieval) ───────────────────────────────────
_mongo_client = MongoClient("mongodb://localhost:27017/")
_db = _mongo_client["pepper_chat_db"]
_fs = gridfs.GridFS(_db)

agent_memory: dict = {}

def reset_for_new_source(chat_id: str):
    """Fully reset agent memory for a chat when user uploads a brand-new source PDF.
    Call this BEFORE process_agent_request when we detect a new-source-PDF scenario
    (e.g. user paused generation and uploaded a different document)."""
    if chat_id in agent_memory:
        _log(f"[reset] Wiping agent memory for chat={chat_id}")
        del agent_memory[chat_id]

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
    '不满意', '改一下', '帮我修改', '修改', '改', '重写',
    'regenerate', 'redo', 'update pdf', 'update report', 'redo pdf',
    'recreate', 'generate again', 'make again', 'new pdf', 'new report',
    'revise report', 'revise pdf', 'modify report', 'modify pdf',
]
def _is_regenerate_request(t): return any(w in t.lower() for w in _REGENERATE_WORDS)


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
    if lang == "zh":
        return "语言规则（最高优先级）：你必须全程使用简体中文回复用户，包括分析、提问与后续内容。"
    if lang == "ja":
        return "Language rule (highest priority): Reply entirely in Japanese for all analysis, questions, and follow-ups."
    if lang == "ko":
        return "Language rule (highest priority): Reply entirely in Korean for all analysis, questions, and follow-ups."
    if lang == "ar":
        return "Language rule (highest priority): Reply entirely in Arabic for all analysis, questions, and follow-ups."
    if lang == "th":
        return "Language rule (highest priority): Reply entirely in Thai for all analysis, questions, and follow-ups."
    if lang == "ms":
        return "Language rule (highest priority): Reply entirely in Malay for all analysis, questions, and follow-ups."
    if lang == "es":
        return "Language rule (highest priority): Reply entirely in Spanish for all analysis, questions, and follow-ups."
    if lang == "fr":
        return "Language rule (highest priority): Reply entirely in French for all analysis, questions, and follow-ups."
    if lang == "de":
        return "Language rule (highest priority): Reply entirely in German for all analysis, questions, and follow-ups."
    return "Language rule (highest priority): Reply entirely in English for all analysis, questions, and follow-ups."


def get_routing_question(lang: str = "en") -> str:
    """
    Public helper used by server-side fallback injection as well.
    """
    if lang == "zh":
        return (
            "我已经完成了数据的深度分析。关于生成最终报告，您是否有现成的 PDF 样板/模版希望我模仿其设计？"
            "如果有，请直接上传；如果没有，我可以为您自主设计一套专业方案。您希望如何处理？"
        )
    if lang == "ja":
        return (
            "データの詳細分析が完了しました。最終レポートの作成について、"
            "デザインを参照する既存の PDF テンプレートはありますか？"
            "ある場合はアップロードしてください。ない場合は、私がプロ仕様のレイアウトを設計できます。"
            "どのように進めますか？"
        )
    if lang == "ko":
        return (
            "데이터에 대한 심층 분석을 완료했습니다. 최종 보고서 생성과 관련해, "
            "제가 참고할 기존 PDF 샘플/템플릿이 있으신가요? "
            "있다면 바로 업로드해 주세요. 없다면 제가 전문적인 레이아웃으로 직접 설계해 드릴 수 있습니다. "
            "어떻게 진행할까요?"
        )
    if lang == "ar":
        return (
            "لقد أكملت التحليل المتعمق لبياناتك. بخصوص إنشاء التقرير النهائي، "
            "هل لديك نموذج/قالب PDF جاهز تريدني أن أحاكي تصميمه؟ "
            "إذا كان لديك، ارفعه مباشرة. وإذا لم يكن لديك، يمكنني تصميم نسخة احترافية لك. "
            "كيف تفضّل المتابعة؟"
        )
    if lang == "th":
        return (
            "ฉันวิเคราะห์ข้อมูลเชิงลึกของคุณเสร็จแล้ว สำหรับการสร้างรายงานฉบับสุดท้าย "
            "คุณมีไฟล์ตัวอย่าง/เทมเพลต PDF ที่ต้องการให้ฉันยึดตามดีไซน์หรือไม่? "
            "ถ้ามี โปรดอัปโหลดได้เลย; ถ้าไม่มี ฉันสามารถออกแบบเลย์เอาต์แบบมืออาชีพให้ได้ "
            "คุณต้องการดำเนินการแบบไหน?"
        )
    if lang == "ms":
        return (
            "Saya telah selesai membuat analisis mendalam terhadap data anda. Untuk laporan akhir, "
            "adakah anda mempunyai sampel/templat PDF sedia ada yang anda mahu saya ikut reka bentuknya? "
            "Jika ya, sila muat naik terus; jika tidak, saya boleh reka susun atur profesional untuk anda. "
            "Bagaimana anda mahu teruskan?"
        )
    if lang == "es":
        return (
            "He completado un análisis profundo de tus datos. Para el informe final, "
            "¿tienes una plantilla/muestra PDF existente que quieras que siga? "
            "Si la tienes, súbela directamente; si no, puedo diseñar una versión profesional para ti. "
            "¿Cómo te gustaría proceder?"
        )
    if lang == "fr":
        return (
            "J’ai terminé une analyse approfondie de vos données. Pour le rapport final, "
            "avez-vous un modèle/exemple PDF existant que vous souhaitez que je suive ? "
            "Si oui, téléversez-le directement ; sinon, je peux concevoir une mise en page professionnelle pour vous. "
            "Comment souhaitez-vous procéder ?"
        )
    if lang == "de":
        return (
            "Ich habe die Datenanalyse vollständig abgeschlossen. Für den Abschlussbericht: "
            "Haben Sie eine vorhandene PDF-Vorlage/ein Muster, an dem ich mich beim Design orientieren soll? "
            "Falls ja, laden Sie es bitte direkt hoch; falls nein, kann ich ein professionelles Layout für Sie entwerfen. "
            "Wie möchten Sie vorgehen?"
        )
    return (
        "I have completed a deep analysis of your data. For the final report, do you have an existing PDF sample/template "
        "you want me to follow? If yes, please upload it directly; if not, I can design a professional layout for you. "
        "How would you like to proceed?"
    )

# ─── Logging ──────────────────────────────────────────────────────────────────
def _log(msg: str):
    print(f"[PDF Agent] {msg}")

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
                        if extracted and len(extracted) >= 1:
                            header = extracted[0]
                            md = "| " + " | ".join(str(c or "") for c in header) + " |\n"
                            md += "| " + " | ".join("---" for _ in header) + " |\n"
                            for row in extracted[1:]:
                                md += "| " + " | ".join(str(c or "") for c in row) + " |\n"
                            parts.append(f"[Table {ti+1} from Page {page_num}]:\n{md}")
            except Exception as te:
                _log(f"[table extract] Page {page_num} table error (non-fatal): {te}")
        
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
_STRUCTURE = {

    "financial": """\
You are a Senior Financial Analyst generating a professional financial report.
Use EXACTLY this structure (fill every section with real data from the source):

# [Entity Name] — Financial Analysis Report [Year]

## Executive Summary
3–5 bullet points: most critical financial metrics and overall assessment.

## 1. Financial Performance at a Glance
| Metric                 | Latest Period | Prior Period | YoY % Change | Analytical Diagnosis |
|------------------------|---------------|--------------|--------------|----------------------|
| Total Revenue          |               |              |              |                      |
| Gross Profit           |               |              |              |                      |
| EBITDA                 |               |              |              |                      |
| Operating Profit       |               |              |              |                      |
| Net Profit             |               |              |              |                      |
| Total Assets           |               |              |              |                      |
| Total Liabilities      |               |              |              |                      |
| Shareholders' Equity   |               |              |              |                      |

## 2. Revenue & Profitability Deep-Dive
### 2.1 Revenue Composition & Growth Drivers
(Detailed narrative analysis: dissecting revenue streams, volume vs. price impacts, and segment performance. Provide a table if segment data is available.)
### 2.2 Margin Contraction / Expansion
| Margin Type            | Current %     | Prior %      | Variance (bps)| Strategic Implication |
|------------------------|---------------|--------------|---------------|-----------------------|
| Gross Margin           |               |              |               |                       |
| Operating Margin       |               |              |               |                       |
| Net Profit Margin      |               |              |               |                       |

## 3. Liquidity, Solvency & Capital Structure
### 3.1 Working Capital Health
(Narrative on operating cash cycle, receivables, and inventory management)
### 3.2 Leverage & Solvency Analysis
| Ratio                  | Value         | Target/Norm  | Variance     | Risk Assessment (High/Med/Low) & Why |
|------------------------|---------------|--------------|--------------|--------------------------------------|
| Current Ratio          |               | > 1.0        |              |                                      |
| Quick Ratio            |               | > 0.8        |              |                                      |
| Debt-to-Equity         |               |              |              |                                      |
| Interest Coverage      |               | > 3.0x       |              |                                      |

## 4. Operational Efficiency & Cash Flow
### 4.1 Asset Utilisation
| Metric                 | Value         | Prior Period | Interpretation / Management Efficiency |
|------------------------|---------------|--------------|----------------------------------------|
| Asset Turnover         |               |              |                                        |
| Inventory Turnover     |               |              |                                        |
| Receivables Days       |               |              |                                        |

### 4.2 Cash Flow Quality
| Category               | Amount        | YoY Change   | Quality Assessment (Is profit converting to cash?) |
|------------------------|---------------|--------------|----------------------------------------------------|
| Operating Activities   |               |              |                                                    |
| Investing Activities   |               |              |                                                    |
| Financing Activities   |               |              |                                                    |
| Free Cash Flow (FCF)   |               |              |                                                    |

## 5. Strategic Risk Diagnostics & Core Observations
| Risk Domain            | Identified Threat / Weakness | Impact Horizon | Recommended Mitigation |
|------------------------|------------------------------|----------------|------------------------|
| Liquidity/Funding      |                              |                |                        |
| Profitability          |                              |                |                        |
| Operational/Market     |                              |                |                        |

## 6. Executive Conclusions & Action Plan
(Actionable, data-backed, prioritised recommendations. Be extremely specific and financially rigorous.)

RULES: Bold **all** monetary values and percentages. Use appropriate currency prefixes. \
No filler text. Every cell must contain real data or professional analysis derived from <agent_memory_source_data>. \
Mark unknown data values as "N/A", but ALWAYS write the 'Diagnosis/Interpretation' using expert financial reasoning. \
⛔ ABSOLUTE BAN: Never output [Value], [Amount], [X], or any [bracketed placeholder]. \
If data is completely missing, write N/A. If an ENTIRE table has absolutely no data, OMIT THE TABLE entirely — NO EMPTY TABLES!""",


    "annual_report": """\
You are a Corporate Analyst summarising an annual report.
Use EXACTLY this structure:

# [Company Name] — Annual Report [Year] Summary

## Chairman's / CEO's Message — Key Highlights
(3–5 key takeaways from the leadership's statement)

## 1. Corporate Overview
| Item              | Details |
|-------------------|---------|
| Company           |         |
| Listed Exchange   |         |
| Financial Year    |         |
| Core Business     |         |
| No. of Employees  |         |

## 2. Business Operations Review
### 2.1 Key Business Segments
(One paragraph + table per major segment)
### 2.2 Significant Events & Milestones
(Bullet list of major events during the year)

## 3. Financial Highlights
| Metric            | Prior Year | Current Year | Change |
|-------------------|-----------|--------------|--------|
| Revenue           |           |              |        |
| Net Profit        |           |              |        |
| EPS               |           |              |        |
| Dividend Per Share|           |              |        |
| Total Assets      |           |              |        |
| NAV Per Share     |           |              |        |

## 4. Corporate Governance
(Board composition, committees, audit findings — brief but accurate)

## 5. Sustainability / CSR Highlights
(Environmental, social, governance initiatives)

## 6. Outlook & Forward Strategy
(Management's stated targets and strategies)

## 7. Investment Assessment Summary
| Factor          | Rating (1–5) | Comments |
|-----------------|-------------|---------|
| Financial Health |            |         |
| Growth Prospect  |            |         |
| Management Quality|           |         |
| Risk Profile      |            |         |

## 8. Key Takeaways for Stakeholders
(3–5 concise conclusions)

RULES: Bold **all** monetary values. Every table must be fully populated \
with real data from <agent_memory_source_data>. No invented figures. \
⛔ ABSOLUTE BAN: Never output [Value], [Amount], [X], or any [bracketed placeholder]. \
If data is missing, write N/A. If an ENTIRE table has no data, OMIT THE TABLE entirely — NO EMPTY TABLES!""",


    "academic": """\
You are an Academic Paper Analyst generating a structured research summary.
Use EXACTLY this structure:

# [Paper Title]

## Abstract
Concise 150–200 word summary: background, objective, method, findings, conclusion.

## 1. Introduction
### 1.1 Background & Context
### 1.2 Research Problem & Gap
### 1.3 Objectives / Research Questions
(Bullet list of specific objectives)

## 2. Literature Review Summary
(Key prior works cited, theoretical framework)

## 3. Methodology
| Aspect           | Details |
|------------------|---------|
| Research Design  |         |
| Sample / Dataset |         |
| Data Collection  |         |
| Analysis Method  |         |
| Tools / Software |         |

## 4. Results & Findings
(All quantitative results in tables; key qualitative findings as bullets)
### 4.1 Primary Results
### 4.2 Secondary Findings

## 5. Discussion
(Interpretation, comparison with prior work, limitations)

## 6. Conclusions
(Direct answers to each research objective)

## 7. Implications & Future Research
(Practical implications + recommended future studies)

## References Summary
(List key cited works in APA format — only those present in the source)

RULES: Preserve all statistical figures exactly. \
Use tables for any comparative or quantitative data. \
Do not add citations not found in the source document.""",


    "legal": """\
You are a Legal Analyst generating a structured document summary.
Use EXACTLY this structure:

# [Document Title] — Legal Summary

## Document Overview
| Item              | Details |
|-------------------|---------|
| Document Type     |         |
| Date / Version    |         |
| Governing Law     |         |
| Jurisdiction      |         |
| Effective Date    |         |

## Parties Involved
| Party     | Role          | Description |
|-----------|---------------|-------------|
| Party A   |               |             |
| Party B   |               |             |

## 1. Background & Purpose
(Recitals / whereas clauses summarised)

## 2. Key Terms & Definitions
(Table of defined terms from the document)
| Term | Definition |
|------|-----------|

## 3. Core Obligations
### 3.1 Obligations of [Party A]
### 3.2 Obligations of [Party B]

## 4. Rights & Restrictions
(Key rights granted; what is prohibited)

## 5. Financial Terms
| Item           | Details |
|----------------|---------|
| Consideration  |         |
| Payment Terms  |         |
| Penalties      |         |
| Milestones     |         |

## 6. Risk & Liability Clauses
(Indemnification, limitation of liability, warranties)

## 7. Termination & Dispute Resolution
(Conditions for termination; dispute mechanism)

## 8. Key Risks & Red Flags
(Bullet list of potentially unfavourable clauses)

## 9. Summary Assessment
(Overall assessment: favourable / neutral / unfavourable, with reasoning)

RULES: Quote exact clause numbers where available. \
Flag ambiguous language with ⚠️. No legal advice disclaimers needed. \
⛔ ABSOLUTE BAN: Never output [Value], [Amount], [X], or any [bracketed placeholder]. \
If data is missing, write N/A. If an ENTIRE table has no data, OMIT THE TABLE entirely — NO EMPTY TABLES!""",


    "medical": """\
You are a Medical Document Analyst generating a clinical summary.
Use EXACTLY this structure:

# [Document Title] — Medical Summary

## Patient / Study Overview
| Item              | Details |
|-------------------|---------|
| Subject           |         |
| Date              |         |
| Facility          |         |
| Attending         |         |

## 1. Chief Complaint / Objective
## 2. Medical History Summary
## 3. Assessment & Diagnosis
| Condition | ICD Code | Severity | Status |
|-----------|---------|----------|--------|

## 4. Test Results & Investigations
(All lab values, imaging findings — in tables)
| Test          | Result | Normal Range | Status |
|---------------|--------|-------------|--------|

## 5. Treatment Plan
| Intervention    | Dosage / Details | Duration | Goal |
|-----------------|-----------------|----------|------|

## 6. Prognosis & Follow-up
## 7. Key Clinical Notes

RULES: Preserve all numerical values exactly. Flag critical values with ⚠️. \
⛔ ABSOLUTE BAN: Never output [Value], [Amount], [X], or any [bracketed placeholder]. \
If data is missing, write N/A. If an ENTIRE table has no data, OMIT THE TABLE entirely — NO EMPTY TABLES!""",


    "business": """\
You are a Business Strategist generating a structured business analysis.
Use EXACTLY this structure:

# [Company / Project Name] — Business Analysis Report

## Executive Summary
(3–5 bullet strategic takeaways)

## 1. Business Overview
| Item              | Details |
|-------------------|---------|
| Company / Product |         |
| Industry          |         |
| Target Market     |         |
| Business Model    |         |
| Stage             |         |

## 2. Market Analysis
### 2.1 Market Size & Opportunity
### 2.2 Competitive Landscape
| Competitor | Strengths | Weaknesses | Market Share |
|-----------|----------|-----------|-------------|

## 3. SWOT Analysis
| | Strengths | Weaknesses |
|-|-----------|-----------|
| **Internal** | | |
| | **Opportunities** | **Threats** |
| **External** | | |

## 4. Financial Projections / KPIs
| Metric        | Current | Target | Timeline |
|---------------|---------|--------|----------|

## 5. Strategic Recommendations
(Prioritised action plan — bold each priority)

## 6. Risk Assessment
| Risk            | Likelihood | Impact | Mitigation |
|-----------------|-----------|--------|------------|

## 7. Conclusion

RULES: Bold all key numbers and strategic priorities. \
Every table must be populated. No generic filler statements. \
⛔ ABSOLUTE BAN: Never output [Value], [Amount], [X], or any [bracketed placeholder]. \
If data is missing, write N/A. If an ENTIRE table has no data, OMIT THE TABLE entirely — NO EMPTY TABLES!""",


    "general": """\
You are an Expert Document Analyst generating a professional structured report.
Use EXACTLY this structure:

# [Document Title] — Analysis Report

## Executive Summary
(3–5 concise bullet points: the most important findings)

## 1. Document Overview
| Item       | Details |
|------------|---------|
| Title      |         |
| Type       |         |
| Author     |         |
| Date       |         |
| Purpose    |         |

## 2. Key Content Analysis
### 2.1 [First Major Topic]
(Narrative + table if data is present)
### 2.2 [Second Major Topic]
### 2.3 [Continue for all major topics]

## 3. Data & Metrics Summary
(ANY numerical data from the document — in a well-formatted table)
| Metric | Value | Context |
|--------|-------|---------|

## 4. Critical Insights
(Bullet list of non-obvious insights extracted from the document)

## 5. Conclusions & Recommendations
(Data-backed — no generic statements)

RULES: Bold **all** key figures and critical terms. \
Every section heading must match the actual content of the document. \
No filler text. Be exhaustive. \
⛔ ABSOLUTE BAN: Never output [Value], [Amount], [X], or any [bracketed placeholder]. \
If data is missing, write N/A. If an ENTIRE table has no data, OMIT THE TABLE entirely — NO EMPTY TABLES!""",
}


# ─── Main Entry Point ─────────────────────────────────────────────────────────
def process_agent_request(chat_id: str, user_message: str, attachments: list):
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
    
    # Mark as processed
    for att in new_attachments:
        if att.get("saved_path"):
            state["processed_files"].add(att.get("saved_path"))

    instruction = ""

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE: init
    # ══════════════════════════════════════════════════════════════════════════
    if state["stage"] == "init":
        if new_text:
            state["source_data"] += new_text
            state["doc_type"]      = _detect_doc_type(state["source_data"])
            state["stage"]         = "wait_template"
            state["use_fast_model"]= False   # analysis: switch to think model for deeper deepseek analysis
            doc_list = ", ".join(new_names) or "the uploaded document"
            dtype    = state["doc_type"].replace("_", " ").title()
            _log(f"[agent] Detected doc_type={state['doc_type']} → think_model for deep analysis")
            instruction = (
                "# Role\n"
                "你是一位顶尖的数据分析与商业情报专家，拥有金融 CFA、审计 ACCA、管理咨询 McKinsey 级别的深度分析能力。\n"
                "你的任务是对用户上传的原始资料进行极致详尽、多维度、可追溯的深层解构。\n\n"
                "# 🧠 深度思考协议 (Deep Thinking Protocol)\n"
                "在开始撰写分析之前，你必须先在内心完成以下思考步骤：\n"
                "1. **通读全文**：先完整阅读 <agent_memory_source_data> 中的所有内容，不要急于输出\n"
                "2. **数据索引**：在脑海中建立一份完整的数据清单（所有出现过的数字、金额、百分比、比率）\n"
                "3. **交叉验证**：检查数据之间的逻辑关系（如：总计 = 各分项之和，利润率 = 利润/收入）\n"
                "4. **异常标注**：识别任何不一致、缺失或异常的数据点\n"
                "5. **深层推理**：基于数据推导出文档表面没有直接写出的隐含结论\n\n"
                "# 分析框架（你必须按照以下 6 个维度逐一展开，每个维度都要有实质性的详细内容）\n\n"
                "## 1. 文档全景扫描 (Document Overview)\n"
                "- 精确识别文档类型（财务报表/年报/审计报告/战略规划/教学资料/合同/其他）\n"
                "- 涉及的组织全名、行业归属、报告周期\n"
                "- 用 1-2 段话概述文档的核心结论与战略意图\n\n"
                "## 2. 关键数据深度提取 (Data Extraction)\n"
                "⚠️ 这是最重要的环节。你必须把文档中的所有核心数据用 Markdown 表格完整呈现：\n"
                "- 所有金额、百分比、比率、指标等数值型数据，必须以表格形式组织\n"
                "- 如有多年度/多周期数据，必须制作年度对比表（含同比变化率）\n"
                "- 如有分项数据（如收入构成、支出明细），必须制作分类汇总表\n"
                "- 标注数据来源页码，确保可追溯\n"
                "- 💡 高阶要求：如果文档提供了原始数据但没有计算比率，你必须**主动计算**（如利润率、增长率、占比等）\n\n"
                "## 3. 趋势与变化分析 (Trend & Change Analysis)\n"
                "- 逐项分析关键指标的同比/环比变化（必须给出具体数值和百分比）\n"
                "- 识别增长最快和下降最快的TOP 3项目，并分析背后原因\n"
                "- 用表格展示趋势变化摘要\n\n"
                "## 4. 结构与构成拆解 (Composition Breakdown)\n"
                "- 收入/支出/资产/负债的构成比例，用表格展示各项占比\n"
                "- 识别主要驱动因素和核心构成项\n"
                "- 评估结构性集中风险（如是否过度依赖单一来源）\n\n"
                "## 5. 风险识别与深层洞察 (Risk & Hidden Insights)\n"
                "- 基于数据推导出至少3个潜在风险点\n"
                "- 发掘数据中隐含的正面信号和负面信号\n"
                "- 指出数据中的异常值或不一致之处\n"
                "- 💡 进行「第二层思考」：这些数据背后暗示了什么趋势？管理层没有明说但数据已经显露的问题是什么？\n\n"
                "## 6. 专业建议与行动方向 (Recommendations)\n"
                "- 针对每个风险点给出可执行的应对策略\n"
                "- 提供至少3条战略性建议\n"
                "- 按优先级排序，标注紧急程度（🔴高/🟡中/🟢低）\n\n"
                "# 格式硬性要求\n"
                "- 涉及数字对比时，必须使用 Markdown 表格（| 列名 | 数据 | 语法）\n"
                "- 每个维度至少写 3-5 行实质性内容，严禁一笔带过\n"
                "- 分析必须基于文档中的真实数据，禁止编造数据\n"
                "- 金额数据必须使用千位分隔符并加粗显示\n\n"
                "# ⛔ 占位符零容忍规则（ABSOLUTE BAN）\n"
                "你的输出中 **绝对禁止** 出现以下任何占位符：\n"
                "- `[Value]`, `[value]`, `[X]`, `[x]`, `[Amount]`, `[Name]`, `[数据]`, `[金额]`\n"
                "- 任何被方括号包裹的占位文字如 `[...]`\n"
                "- 如果源数据中确实找不到某项数据，请写 **N/A** 或 **数据未披露**，绝不能写 `[Value]`\n"
                "- 🚨 **无数据即删除**：如果整个表格或整个章节在源数据中完全没有相关内容，**请直接删除该表格或章节**，绝不允许生成只有表头却没有内容的空表格！\n\n"
                "# 结尾引导（分析写完后，必须在最末尾单独一行输出以下提问，一字不漏）：\n"
                f"\u201c{routing_q}\u201d\n\n"
                "---\n"
                "现在开始你的深度分析：\n"
                "<agent_memory_source_data>"
            )
        else:
            state["use_fast_model"] = False
            instruction = (
                "You are a professional AI Agent in Document Analysis mode. "
                "The user has not uploaded a document yet. "
                "Ask them to upload a PDF document to begin the analysis."
            )

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE: wait_template
    # ══════════════════════════════════════════════════════════════════════════
    elif state["stage"] == "wait_template":
        if new_text:
            layout_report = ""
            table_structures = ""
            _template_temp_files = []
            for att in (attachments or []):
                saved_path = att.get("saved_path", "")
                if not saved_path or not saved_path.lower().endswith(".pdf"):
                    continue
                # Resolve from GridFS to temp disk
                real_path = _resolve_pdf_from_gridfs(saved_path)
                if not real_path:
                    _log(f"[template] Could not resolve {saved_path} from GridFS")
                    continue
                _template_temp_files.append(real_path)
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
                                if extracted and len(extracted) >= 1:
                                    header = extracted[0]
                                    sample_rows = extracted[1:3]
                                    table_structures += f"\n--- Table {ti+1} (Page {pi+1}) ---\n"
                                    table_structures += "| " + " | ".join(str(c or "") for c in header) + " |\n"
                                    table_structures += "| " + " | ".join("---" for _ in header) + " |\n"
                                    for row in sample_rows:
                                        table_structures += "| " + " | ".join(str(c or "") for c in row) + " |\n"
                        has_tables = "有" if table_count > 0 else "无"
                        layout_report += f"\n[Layout Scan - {att.get('original_name', 'Doc')}]: Pages={pages}, Tables={table_count} ({has_tables}表格)\n"
                except Exception as e:
                    _log(f"[pdfplumber error] {e}")
            # Cleanup template temp files
            for tmp in _template_temp_files:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
            
            # ── CRITICAL: Content-Structure Isolation ──
            # Store ONLY structural metadata from template, NOT its text content.
            # This prevents template placeholder data from contaminating the output.
            state["template_data"]  = layout_report + "\n\n[Template Table Structures]:\n" + table_structures if table_structures else layout_report
            state["stage"]           = "generate"
            state["generate_pdf_now"]= True
            state["use_fast_model"]  = False
            tname = ", ".join(new_names) or "the template"
            
            instruction = (
                "你是一位顶尖的文档智能分析与专业排版专家。\n\n"
                f"用户上传了参考样板：**{tname}**。\n"
                "你现在必须完成两个步骤：先进行深度思考并识别模版类型，然后生成高端专业的报告。\n\n"
                "# ━━━ ⚠️ 最高优先级：内容-结构隔离协议 ⚠️ ━━━\n"
                "## 数据源锁定（Source Data Lock）\n"
                "- 第一阶段上传的资料 PDF 是**唯一的内容来源**\n"
                "- 所有数据、数字、公司名、金额、百分比必须且只能来自 <agent_memory_source_data>\n\n"
                "## 模版仅作排版参考（Template = Layout Only）\n"
                "- 从模版中提取的信息仅用于：页面结构、标题层级、表格框架、排版风格\n"
                "- **严禁**将模版中的任何占位符数据（示例公司名、虚假金额、示范文字）带入最终报告\n"
                "- 如果模版表格中有示例数值，你必须用源数据中的真实数值替换\n"
                "# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "# 步骤一：Chain of Thought 深度思考（开篇2-3句话向用户展示你的识别结论）\n\n"
                "请深入分析样板的结构布局、表格形态，判断它属于以下哪种类型，\n"
                "并根据类型决定你的生成策略：\n\n"
                "📊 **财务报表/分析报告**\n"
                "   → 生成策略：必须包含完整的收支对比表、资产负债表、比率分析表、趋势对比表\n"
                "   → 视觉要求：金额列右对齐，百分比变化用+/-标识，关键异常值用**加粗**标注\n\n"
                "📋 **年度报告/工作汇报**\n"
                "   → 生成策略：必须包含年度KPI表、部门绩效表、数据同比表\n"
                "   → 视觉要求：使用分级标题(h1/h2/h3)清晰划分板块，重要结论**加粗**\n\n"
                "📚 **教学/学术文档**\n"
                "   → 生成策略：必须包含课程结构表、评估标准表、内容大纲表\n"
                "   → 视觉要求：步骤编号清晰，重点用**加粗**标注，逻辑层次分明\n\n"
                "💼 **商业计划/项目提案**\n"
                "   → 生成策略：必须包含市场分析表、财务预测表、里程碑时间表\n"
                "   → 视觉要求：数据可视化优先，关键指标**加粗**高亮\n\n"
                "📑 **其他类型**\n"
                "   → 根据样板特征自行判断最合适的表格和排版策略\n\n"
                "开篇用1-2句话向用户确认你的识别结论，例如：\n"
                "\"我识别到这是一份[XX类型]文档，包含[N]个核心数据表格。现在按此模版风格生成完整报告：\"\n\n"
                "# 步骤二：直接输出高端专业报告（紧接在确认之后）\n\n"
                "━━━━━━ ⚠️ 表格生成最高优先级规则 ⚠️ ━━━━━━\n"
                "所有数据展示必须使用 Markdown 表格（| 和 --- 语法）。\n"
                "绝对禁止用纯文本、bullet points 或编号列表替代表格。\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "底层探测引擎从模版中提取到的表格结构骨架（你必须复刻这些框架并填入**源数据中的真实数据**）：\n"
                f"{table_structures}\n\n"
                "## 生成硬性规则\n"
                "1. **章节结构**：复刻样板的标题层级(#/##/###)和段落顺序\n"
                "2. **表格复刻**：按上方提取的表格结构骨架，生成对应的Markdown表格\n"
                "3. **数据填充**：从 <agent_memory_source_data> 中提取真实数据填入表格，严禁使用模版中的占位符数据\n"
                "4. **视觉优化**：\n"
                "   - 用 **加粗** 标注关键数据和重要结论\n"
                "   - 用分级标题让报告层次分明\n"
                "   - 金额数据保持数值格式（千位分隔符）\n"
                "5. **纯净输出**：禁止一切问候语、多余解释、闲聊。直接从 # 标题开始输出报告正文\n\n"
                f"模版布局扫描：{layout_report}\n\n"
                "源数据词典池（唯一可用的数据来源）：\n"
                "<agent_memory_source_data>"
            )

        elif state.get("template_data") and _is_template_yes(user_message):
            state["stage"]           = "generate"
            state["generate_pdf_now"]= True
            state["use_fast_model"]  = False
            instruction = (
                "角色设定：你是一位精通文档智能（Document Intelligence）与排版渲染的专家级 AI 助手。\n\n"
                "【执行指令：生成策略（Precision Generation）】\n"
                "用户已指定沿用指定的分析蓝本/模版进行生成。\n\n"
                "## ⚠️ 最高优先级：占位符替换规则 ⚠️\n"
                "- 模版中通常会包含大量占位符（如 `[Value]`, `[Amount]`, `[Name]`, `xxx` 等）。\n"
                "- **绝对严禁**将这些占位符原样输出到最终报告中！\n"
                "- 你必须从源数据 `<agent_memory_source_data>` 中找到真实的数值来替换这些占位符。\n"
                "- 如果源数据中**确实没有**该项目的数据，你必须将其替换为 `N/A` 或 `-`，绝对不能保留 `[Value]`。\n\n"
                "1. 表格重建：如果原模版有表格，生成内容时必须完整调用 Markdown 表格组件进行复刻结构，**严禁使用纯文本模拟表格排版**。\n"
                "2. 内容填充：将源数据精准填入对应的表格位置，保持逻辑对齐一致性（如：金额自动计算校对）。\n"
                "3. 零损坏强制要求：全篇只保留供打印的规范结构全文，严禁出现任何多余的客套话或打招呼。\n\n"
                "锁定模版并开始生成：\n"
                "<agent_memory_template_data>\n"
                "源文档数据参考：\n"
                "<agent_memory_source_data>"
            )

        elif _is_direct(user_message):
            state["stage"]           = "generate"
            state["generate_pdf_now"]= True
            state["use_fast_model"]  = False
            doc_type = state.get("doc_type", "general")
            structure = _STRUCTURE.get(doc_type, _STRUCTURE["general"])

            instruction = (
                "角色设定：你是一位精通文档智能与自动排版渲染的专家级 AI 助手。\n\n"
                f"DOCUMENT TYPE DETECTED: **{doc_type.replace('_', ' ').upper()}**\n\n"
                f"{structure}\n\n"
                "【执行指令：自主生成策略】\n"
                "用户明确表示不需要指定样板，要求你直接生成。\n"
                "请严格按照以上提供的行业标准模版结构，自主设计专业清晰排版。\n"
                "1. 表格重建：原分析报告中涉及到数值比对的数据必须通过高度严谨的 Markdown 表格完整具现。\n"
                "2. 零损坏要求：保证最终所有的文本都能安全无损地进入底层生成引擎。\n"
                "3. ⛔ 占位符零容忍：绝对禁止输出 [Value], [Amount], [X], [Name] 等任何方括号占位符。找不到数据就写 N/A。\n"
                "4. 🚨 拒绝空表：如果某个表格或章节在源数据中完全没有对应内容，**请直接不生成该表格/章节**，绝对不要输出空壳表格。\n"
                "回答规范性：严禁口语闲聊，直接从 `# [标题]` 开始吐出最终排版内容即可。\n\n"
                "源文档数据参考：<agent_memory_source_data>"
            )

        else:
            state["use_fast_model"] = False
            instruction = (
                "【状态提醒】当前流程停留在“等待用户决定是否生成最终PDF报告”的阶段。\n"
                "用户当前发送的消息不属于模板操作，请正常回答用户的问题。\n\n"
                "在回答的最后，请顺带提醒用户：\n"
                f"“提示：当您准备好后，随时可以上传模板或告诉我‘直接生成’。”"
            )

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE: wait_confirmation (backward compatibility for existing chats)
    # ══════════════════════════════════════════════════════════════════════════
    elif state["stage"] == "wait_confirmation":
        state["stage"]           = "generate"
        state["generate_pdf_now"]= True
        state["use_fast_model"]  = False

        instruction = (
            f"用户的确认或微调指示如下：\n**{user_message}**\n\n"
            "# ⚠️ 内容-结构隔离协议\n"
            "- 数据源：只能使用 <agent_memory_source_data> 中的真实数据\n"
            "- 模版：仅参考其排版结构，严禁使用模版中的占位符数据\n\n"
            "# Fidelity Protocol\n"
            "1. 模板即准则：严禁擅自更改模板设计、表格间距或整体核心布局。\n"
            "2. 动态填充：将第一阶段分析出的结构化数据像填空一样精准映射到模板对应位置。\n"
            "3. 输出要求：直接输出最终用于生成 PDF 的完整高保真 Markdown，禁止虚假占位符。\n"
            "4. ⛔ 占位符零容忍：绝对禁止输出 [Value] 等方括号占位符。找不到数据就写 N/A。\n"
            "5. 🚨 拒绝空表：如果某个表格或章节在源数据中完全没有对应内容，**请直接跳过并删除该表格/章节**，绝对不要输出空壳表格。\n\n"
            "源数据词典池：<agent_memory_source_data>\n"
            "模板结构蓝本：<agent_memory_template_data>\n\n"
            "现在开始直接输出最后一步用于打印的 Markdown 内容全本。"
        )

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
                instruction = (
                    "用户提供了一份**新的样板/模版**。\n"
                    "请使用最新提取的模版骨架，严格遵循高保真填充规则，重新排版并生成报告正文：\n"
                    f"{state['template_data']}\n\n"
                    "源数据词典池：<agent_memory_source_data>"
                )
            else:
                # It's a brand-new source PDF! Full reset for a fresh analysis cycle
                state["source_data"]      = new_text  # replace, not append
                state["template_data"]    = ""
                state["doc_type"]         = _detect_doc_type(new_text)
                state["stage"]            = "wait_template"
                state["generate_pdf_now"] = False
                state["use_fast_model"]   = False
                state["processed_files"]  = set()  # Clear so future uploads are not blocked
                _log(f"[agent] New source PDF during generate → full reset to wait_template")
                instruction = (
                    "# Role\n"
                    "你是一位顶尖的数据分析与商业情报专家，拥有金融 CFA、审计 ACCA、管理咨询 McKinsey 级别的深度分析能力。\n"
                    "你的任务是对用户上传的原始资料进行极致详尽、多维度、可追溯的深层解构。\n\n"
                    "# 分析框架（你必须按照以下 6 个维度逐一展开，每个维度都要有实质性的详细内容）\n\n"
                    "## 1. 文档全景扫描 (Document Overview)\n"
                    "## 2. 关键数据深度提取 (Data Extraction)\n"
                    "## 3. 趋势与变化分析 (Trend & Change Analysis)\n"
                    "## 4. 结构与构成拆解 (Composition Breakdown)\n"
                    "## 5. 风险识别与深层洞察 (Risk & Hidden Insights)\n"
                    "## 6. 专业建议与行动方向 (Recommendations)\n\n"
                    "# 结尾引导（分析写完后，必须在最末尾单独一行输出以下提问，一字不漏）：\n"
                    f"\u201c{routing_q}\u201d\n\n"
                    "---\n"
                    "现在开始你的深度分析：\n"
                    "<agent_memory_source_data>"
                )
        else:
            # Normal first-time generation (triggered from wait_template)
            state["generate_pdf_now"] = True
            doc_type = state.get("doc_type", "general")
            _log(f"[agent] First-time generation → type={doc_type} generate_pdf_now=True")
            if state.get("template_data"):
                instruction = (
                    f"你现在处于报告生成模式（document type: {doc_type}）。"
                    "请根据用户的要求生成完整的专业报告，严格遵守高保真复刻协议。"
                    "保持模板视觉结构不变，用源数据填充所有内容。"
                    "所有内容都必须有数据依据。"
                )
            else:
                instruction = (
                    f"你现在处于报告生成模式（document type: {doc_type}）。"
                    "请根据用户的要求生成完整的专业报告。"
                    "保持专业结构和可打印排版。"
                    "禁止 filler text，所有内容都必须有数据依据。"
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
                instruction = (
                    "用户提供了一份**新的样板/模版**。\n"
                    "请使用最新提取的模版骨架，严格遵循高保真填充规则，重新排版并生成报告正文：\n"
                    f"{state['template_data']}\n\n"
                    "源数据词典池：<agent_memory_source_data>"
                )
            elif not _is_regenerate_request(user_message):
                # It's a brand-new source PDF! Reset to init for a fresh analysis cycle
                state["source_data"]     = new_text
                state["template_data"]   = ""
                state["doc_type"]        = _detect_doc_type(new_text)
                state["stage"]           = "wait_template"
                state["generate_pdf_now"]= False
                state["use_fast_model"]  = False
                state["processed_files"] = set()  # Clear so future uploads are not blocked
                doc_list = ", ".join(new_names) or "the uploaded document"
                dtype    = state["doc_type"].replace("_", " ").title()
                _log(f"[agent] New source PDF in done stage → reset to wait_template for fresh analysis")
            instruction = (
                "# Role\n"
                "你是一位顶尖的数据分析与商业情报专家，拥有金融 CFA、审计 ACCA、管理咨询 McKinsey 级别的深度分析能力。\n"
                "你的任务是对用户上传的原始资料进行极致详尽、多维度、可追溯的深层解构。\n\n"
                "# 分析框架（你必须按照以下 6 个维度逐一展开，每个维度都要有实质性的详细内容）\n\n"
                "## 1. 文档全景扫描 (Document Overview)\n"
                "## 2. 关键数据深度提取 (Data Extraction)\n"
                "## 3. 趋势与变化分析 (Trend & Change Analysis)\n"
                "## 4. 结构与构成拆解 (Composition Breakdown)\n"
                "## 5. 风险识别与深层洞察 (Risk & Hidden Insights)\n"
                "## 6. 专业建议与行动方向 (Recommendations)\n\n"
                "# 结尾引导（分析写完后，必须在最末尾单独一行输出以下提问，一字不漏）：\n"
                f"\u201c{routing_q}\u201d\n\n"
                "---\n"
                "现在开始你的深度分析：\n"
                "<agent_memory_source_data>"
            )

        # Case 2: User uploaded a new TEMPLATE PDF with regeneration intent
        elif new_text and _is_regenerate_request(user_message):
            state["template_data"]   = new_text
            state["stage"]           = "generate"
            state["generate_pdf_now"]= True
            state["use_fast_model"]  = False
            _log(f"[agent] New template + regenerate request in done stage → generate")
            instruction = (
                f"你现在处于报告迭代模式（document type: {doc_type}）。"
                "用户提供了新的模板并要求重新生成报告。"
                "请根据新模板的设计风格，结合源数据重新生成**完整报告全文**。"
                "严格遵守高保真复刻协议。必须输出从 `# 标题` 开始的完整内容，不要只输出修改的部分。所有内容都必须有数据依据。"
            )

        # Case 3: User explicitly asked to regenerate/update PDF (no new file)
        elif _is_regenerate_request(user_message):
            state["stage"]           = "generate"
            state["generate_pdf_now"]= True
            state["use_fast_model"]  = False
            _log(f"[agent] Explicit regenerate request in done stage → generate")
            if state.get("template_data"):
                instruction = (
                    f"你现在处于报告迭代模式（document type: {doc_type}）。"
                    "用户要求重新生成/更新报告。请根据用户反馈更新内容，并继续严格遵守高保真复刻协议。"
                    "保持模板视觉结构不变，但你必须**重新输出从 `# 标题` 开始的完整报告全文**！绝对不要只输出修改的部分。"
                    "新的 PDF 会在本次输出后自动生成。所有内容都必须有数据依据。"
                )
            else:
                instruction = (
                    f"你现在处于报告迭代模式（document type: {doc_type}）。"
                    "用户要求重新生成/更新报告。请根据用户反馈修订、扩展或更新报告。"
                    "保持同样的专业结构和可打印排版，你必须**重新输出从 `# 标题` 开始的完整报告全文**！绝对不要只输出片段。"
                    "禁止 filler text，所有内容都必须有数据依据。"
                )

        # Case 4: Normal follow-up question → just answer, NO PDF generation
        else:
            state["generate_pdf_now"] = False
            state["use_fast_model"]   = False
            _log(f"[agent] Follow-up question in done stage → text-only response, no PDF")
            instruction = (
                f"你现在处于文档问答模式（document type: {doc_type}）。\n"
                "用户之前已经完成了 PDF 报告的生成。现在用户正在基于分析结果进行追问。\n\n"
                "⚠️ 重要规则：\n"
                "- 不要生成新的 PDF 报告\n"
                "- 不要输出完整的报告格式内容\n"
                "- 主要是正常文字回答用户的问题、提供解释、做总结或回应延伸问题\n"
                "- 除非用户明确要求（例如“把结果保存到Google Docs”、“给我发邮件”等），否则不要主动调用工具\n"
                "- 可以引用 <agent_memory_source_data> 中的数据来佐证你的回答\n\n"
                "直接回答用户的问题即可。"
            )

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

    hidden_context = "\n\n" + "\n\n".join(ctx_parts) if ctx_parts else ""
    
    # Inject smart generation rules if a PDF is about to be generated
    if state.get("generate_pdf_now"):
        smart_rules = (
            "\n\n# 🧠 智能分析与强制命名协议 (Smart Generation & Naming Protocol)\n"
            "在输出报告正文之前，你必须遵守以下核心要求：\n"
            "1. **智能命名**：你必须根据报告内容构思一个专业、精准的英文或中文报告名称。在输出的第一行，你必须**强制输出**一个一级标题（例如：`# 2023年度XX公司财务深度分析报告`）。这将被用作最终生成文件的物理文件名，**无论使用何种模板，这一行绝对不能省略！**\n"
            "2. **100%表格填满强制令**：绝不允许在任何表格中出现空白单元格（Empty Cells/Columns）！如果某一列或某一行没有直接的数据，你必须发挥专家能力**进行推算、计算，或者填入极度详细的深度文字分析**。绝对不要留出像 `|   |` 这样的空位！\n"
            "3. **深度推演填补**：对于模板中出现的分析类字段（如 Interpretation/Intepretasi/分析/得分/Skor/评价 等），绝对不允许写N/A！你必须像顶级分析师一样，基于填入的数据**自主撰写极其详细、专业的深度财务分析和业务洞察**。\n"
            "4. **数据填充**：将源数据精准填入表格。如果某个表格在源数据中完全找不到相关内容，**请直接删除整个表格**，严禁生成空壳表格。\n"
        )
        instruction = f"{lang_rule}\n\n{instruction}{smart_rules}"
    else:
        instruction = f"{lang_rule}\n\n{instruction}"

    _log(f"[agent] → stage={state['stage']} doc_type={state.get('doc_type')} "
         f"generate_pdf_now={state['generate_pdf_now']}")
    return instruction, hidden_context

