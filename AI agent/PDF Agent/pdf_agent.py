"""
pdf_agent.py  ─ Context-Aware Document Analysis Agent
======================================================
Stage flow
----------
init          → PDF received → wait_template
                Model: deep analysis + asks about template

wait_template → template PDF uploaded   → generate  (with template)
              → "直接生成" / "no"        → generate  (auto-detected type layout)
              → unclear                 → re-ask

generate      → model outputs full structured report → generate_pdf_now = True
refine        → user requests changes               → generate_pdf_now = True
"""
import os
import re

# ─── PDF Parser ───────────────────────────────────────────────────────────────
# Fast path via PyMuPDF (fitz) to remove the heavy 15-30s CPU delay.
try:
    import pymupdf
except ImportError:
    pymupdf = None
_USE_MARKITDOWN = False

agent_memory: dict = {}

# ─── Keywords ─────────────────────────────────────────────────────────────────
_DIRECT_WORDS = [
    '直接', '直接生成', '没有', '无', 'no', 'none', 'default', 'skip',
    '不用', '不需要', 'without', 'proceed', 'just generate', '就行了',
    '随便', '默认', '直接做', '自动', '帮我生成', '生成吧', '可以了',
]
def _is_direct(t): return any(w in t.lower() for w in _DIRECT_WORDS)

# ─── Logging ──────────────────────────────────────────────────────────────────
def _log(msg: str):
    with open("debug_pdf.log", "a", encoding="utf-8") as f:
        f.write(msg + "\n")

# ─── PDF Extractor ────────────────────────────────────────────────────────────
def _extract_pdf(path: str) -> str:
    """
    Extract full text from PDF instantly using PyMuPDF.
    Allows unlimited characters as per user request to ensure full analysis.
    """
    try:
        doc  = pymupdf.open(path)
        text = "".join(page.get_text() for page in doc).strip()
        _log(f"[PyMuPDF] {path} → {len(text)} chars raw")

        _log(f"[extract] final {len(text)} chars sent to model")
        return text
    except Exception as e:
        _log(f"[extract_pdf ERROR] {e}")
        return ""

def _extract_attachments(attachments: list) -> tuple:
    combined, names = "", []
    for att in (attachments or []):
        path = att.get("saved_path", "")
        if path and path.lower().endswith(".pdf"):
            text = _extract_pdf(path)
            _log(f"[extract] {path} → {len(text)} chars")
            if text:
                name = att.get("original_name", os.path.basename(path))
                combined += f"\n\n[Content from '{name}']:\n{text}\n"
                names.append(name)
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
| Metric               | Prior Period | Current Period | Change (%) |
|----------------------|-------------|----------------|------------|
| Total Revenue        |             |                |            |
| Gross Profit         |             |                |            |
| Operating Profit     |             |                |            |
| Net Profit           |             |                |            |
| Total Assets         |             |                |            |
| Total Liabilities    |             |                |            |
| Shareholders' Equity |             |                |            |

## 2. Income Statement Analysis
### 2.1 Revenue Breakdown
(Table + narrative: revenue by segment/product/geography)
### 2.2 Cost Structure
(COGS, operating expenses — use tables)
### 2.3 Profitability
(Gross margin, EBIT, EBITDA, net margin with % comparisons)

## 3. Balance Sheet Analysis
### 3.1 Assets
(Current vs non-current breakdown with table)
### 3.2 Liabilities & Equity
(Short/long-term liabilities, capital structure)

## 4. Cash Flow Analysis
| Category            | Amount | YoY Change |
|---------------------|--------|------------|
| Operating Activities |       |            |
| Investing Activities |       |            |
| Financing Activities |       |            |
| Net Cash Position    |       |            |

## 5. Key Financial Ratios
| Ratio                | Value | Benchmark / Prior Year | Assessment |
|----------------------|-------|------------------------|------------|
| Current Ratio        |       |                        |            |
| Debt-to-Equity       |       |                        |            |
| Return on Equity     |       |                        |            |
| Net Profit Margin    |       |                        |            |
| Asset Turnover       |       |                        |            |

## 6. Risk Factors & Observations
(Key risks identified from the document)

## 7. Conclusions & Recommendations
(Actionable, data-backed, prioritised recommendations)

RULES: Bold **all** monetary values and percentages. Use RM/USD/etc. prefix. \
No filler text. Every cell must contain real data from <agent_memory_source_data>. \
Mark unknown values as "N/A".""",


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
with real data from <agent_memory_source_data>. No invented figures.""",


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
Flag ambiguous language with ⚠️. No legal advice disclaimers needed.""",


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

RULES: Preserve all numerical values exactly. Flag critical values with ⚠️.""",


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
Every table must be populated. No generic filler statements.""",


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
No filler text. Be exhaustive.""",
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
            "use_fast_model":   True,   # fast during analysis, think during generation
        }

    state = agent_memory[chat_id]
    state["generate_pdf_now"] = False
    _log(f"\n[turn] chat={chat_id} stage={state['stage']} msg={user_message[:80]!r}")

    new_text, new_names = _extract_attachments(attachments)

    instruction = ""

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE: init
    # ══════════════════════════════════════════════════════════════════════════
    if state["stage"] == "init":
        if new_text:
            state["source_data"] += new_text
            state["doc_type"]      = _detect_doc_type(state["source_data"])
            state["stage"]         = "wait_template"
            state["use_fast_model"]= True   # analysis: fast model, no think overhead
            doc_list = ", ".join(new_names) or "the uploaded document"
            dtype    = state["doc_type"].replace("_", " ").title()
            _log(f"[agent] Detected doc_type={state['doc_type']} → fast_model for analysis")
            instruction = (
                f"You are an Elite Document Analysis Expert specializing in "
                f"**{dtype}** documents. "
                f"The user has uploaded: **{doc_list}**.\n\n"
                "TASK:\n"
                "1. Perform a THOROUGH, in-depth analysis of the entire document "
                "in <agent_memory_source_data>.\n"
                "2. Answer the user's question with precision — use structured "
                "headings, bullet points, and tables where appropriate.\n"
                "3. End your response with EXACTLY this question "
                "   '✅ Analysis complete. Do you have a PDF template to follow? "
                "If not, reply **\"直接生成\"** for the default professional layout.'"
            )
        else:
            state["use_fast_model"] = True
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
            state["template_data"]  = new_text
            state["stage"]           = "generate"
            state["generate_pdf_now"]= True
            state["use_fast_model"]  = False  # report generation: use think model
            tname = ", ".join(new_names) or "the template"
            _log(f"[agent] Template uploaded → type={state['doc_type']} think_model=True")
            instruction = (
                f"The user provided a TEMPLATE document: **{tname}**.\n\n"
                "TASK: Generate a COMPLETE professional report that:\n"
                "1. Mirrors the EXACT section structure from <agent_memory_template_data>\n"
                "2. Fills every section with accurate data from <agent_memory_source_data>\n"
                "3. Uses Markdown tables for ALL numerical/comparative data\n"
                "4. Bolds all key figures, dates, and critical terms\n\n"
                "Output will be rendered as a premium downloadable PDF. "
                "Be thorough and accurate — no filler text."
            )

        elif _is_direct(user_message):
            state["stage"]           = "generate"
            state["generate_pdf_now"]= True
            state["use_fast_model"]  = False  # report generation: use think model
            doc_type = state.get("doc_type", "general")
            # Get the type-specific structure template
            structure = _STRUCTURE.get(doc_type, _STRUCTURE["general"])

            instruction = (
                f"DOCUMENT TYPE DETECTED: **{doc_type.replace('_', ' ').upper()}**\n\n"
                f"{structure}\n\n"
                "——————————————————————————————————————————\n"
                "NOW GENERATE: Using the structure above as your MANDATORY TEMPLATE, "
                "produce a COMPLETE report populated entirely with real data "
                "from <agent_memory_source_data>.\n"
                "Do NOT use placeholder text. Every section heading, every table row, "
                "every bullet must contain real information from the source document.\n"
                "This Markdown output will be automatically converted to a "
                "professionally styled downloadable PDF."
            )

        else:
            instruction = (
                "The user's reply is unclear. Do NOT generate a report yet. "
                "Politely re-ask: do they have a PDF template to upload, "
                "or shall you proceed with the auto-detected professional layout "
                "(reply '直接生成')?  Keep it short and clear."
            )

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE: generate (refinement loop)
    # ══════════════════════════════════════════════════════════════════════════
    elif state["stage"] == "generate":
        if new_text:
            state["source_data"] += new_text
        state["generate_pdf_now"] = True
        doc_type = state.get("doc_type", "general")
        _log(f"[agent] Refinement → type={doc_type} generate_pdf_now=True")
        instruction = (
            f"You are in REPORT REFINEMENT mode (document type: {doc_type}). "
            "A PDF was just generated. Revise, expand, or update the report "
            "based on the user's feedback. A new PDF will be generated automatically. "
            "Maintain the same professional structure and formatting. "
            "No filler text — all content must be data-backed."
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

    _log(f"[agent] → stage={state['stage']} doc_type={state.get('doc_type')} "
         f"generate_pdf_now={state['generate_pdf_now']}")
    return instruction, hidden_context
