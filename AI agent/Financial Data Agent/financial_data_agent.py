"""
financial_data_agent.py - Structured data intake agent for entrepreneur finance.

This module is intentionally separate from the existing PDF Agent so PDF
analysis, PDF generation, Gmail sending, and Google Drive upload flows keep
using their proven path unchanged.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
from typing import Any

import gridfs
from pymongo import MongoClient

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config_loader import cfg


_mongo_client = MongoClient(cfg.mongo_uri)
_db = _mongo_client[cfg.mongo_database]
_fs = gridfs.GridFS(_db)

agent_memory: dict = {}

SUPPORTED_EXTS = {".csv", ".tsv", ".txt", ".md", ".json", ".jsonl", ".xlsx", ".xls"}


def _log(msg: str):
    print(f"[Financial Data Agent] {msg}")


def _ext(att: dict) -> str:
    name = att.get("original_name") or att.get("saved_path") or ""
    return os.path.splitext(name.lower())[1]


def supported_data_attachments(attachments: list) -> list:
    return [
        att for att in (attachments or [])
        if att.get("saved_path") and _ext(att) in SUPPORTED_EXTS
    ]


def has_supported_data_attachment(attachments: list) -> bool:
    return bool(supported_data_attachments(attachments))


def has_active_data(chat_id: str) -> bool:
    return bool(agent_memory.get(chat_id, {}).get("source_data"))


def reset_for_new_source(chat_id: str):
    if chat_id in agent_memory:
        _log(f"[reset] Wiping financial data state for chat={chat_id}")
        del agent_memory[chat_id]


def _resolve_from_gridfs(saved_path: str) -> str:
    try:
        file_doc = _fs.find_one({"filename": saved_path})
        if not file_doc:
            _log(f"[GridFS] File not found: {saved_path}")
            return ""
        tmp_path = os.path.join(tempfile.gettempdir(), saved_path)
        with open(tmp_path, "wb") as f:
            f.write(file_doc.read())
        return tmp_path
    except Exception as exc:
        _log(f"[GridFS resolve ERROR] {exc}")
        return ""


def _decode_text_file(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _table_to_markdown(rows: list[list[Any]]) -> str:
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


def _extract_delimited(path: str, delimiter: str = ",", max_rows: int = 250) -> str:
    text = _decode_text_file(path)
    sample = text[:4096]
    if delimiter == ",":
        try:
            delimiter = csv.Sniffer().sniff(sample).delimiter
        except Exception:
            pass
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for row in reader:
        if any(str(cell or "").strip() for cell in row):
            rows.append(row)
        if len(rows) >= max_rows + 1:
            break
    if not rows:
        return ""
    note = f"{max(len(rows) - 1, 0)} rows shown"
    if len(rows) >= max_rows + 1:
        note += f"; truncated to first {max_rows} data rows"
    return f"[Delimited table: {note}]\n{_table_to_markdown(rows)}"


def _json_to_lines(value: Any, prefix: str = "", depth: int = 0, max_items: int = 80) -> list[str]:
    if depth > 8:
        return [f"{prefix}: {value}" if prefix else str(value)]
    if isinstance(value, dict):
        lines = []
        for idx, (key, item) in enumerate(value.items()):
            if idx >= max_items:
                lines.append(f"{prefix}...: truncated")
                break
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            lines.extend(_json_to_lines(item, next_prefix, depth + 1, max_items))
        return lines
    if isinstance(value, list):
        lines = []
        for idx, item in enumerate(value[:max_items]):
            next_prefix = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            lines.extend(_json_to_lines(item, next_prefix, depth + 1, max_items))
        if len(value) > max_items:
            lines.append(f"{prefix}[...]: truncated")
        return lines
    return [f"{prefix}: {value}" if prefix else str(value)]


def _extract_json(path: str, jsonl: bool = False) -> str:
    text = _decode_text_file(path)
    if jsonl:
        data = []
        for line in text.splitlines()[:250]:
            line = line.strip()
            if not line:
                continue
            try:
                data.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    else:
        data = json.loads(text)
    lines = _json_to_lines(data)
    rendered = "\n".join(lines[:1200])
    if len(lines) > 1200:
        rendered += "\n[... JSON content truncated for context window ...]"
    return f"[JSON data]\n{rendered}"


def _extract_excel(path: str, max_rows: int = 120) -> str:
    import pandas as pd

    sheets = pd.read_excel(path, sheet_name=None, nrows=max_rows)
    parts = []
    for sheet_name, df in sheets.items():
        if df is None or df.empty:
            continue
        df = df.fillna("")
        parts.append(
            f"[Excel sheet '{sheet_name}', first {len(df)} rows]\n"
            f"{df.to_markdown(index=False)}"
        )
    return "\n\n".join(parts)


def _extract_plain_text(path: str, max_chars: int = 60_000) -> str:
    text = _decode_text_file(path)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... content truncated for context window ...]"
    return f"[Text data]\n{text}" if text else ""


def _extract_one(path: str, ext: str) -> str:
    if ext == ".csv":
        return _extract_delimited(path, ",")
    if ext == ".tsv":
        return _extract_delimited(path, "\t")
    if ext == ".json":
        return _extract_json(path, False)
    if ext == ".jsonl":
        return _extract_json(path, True)
    if ext in (".xlsx", ".xls"):
        return _extract_excel(path)
    if ext in (".txt", ".md"):
        return _extract_plain_text(path)
    return ""


def _extract_attachments(attachments: list) -> tuple[str, list[str]]:
    combined = ""
    names: list[str] = []
    temp_files: list[str] = []
    for att in supported_data_attachments(attachments):
        saved_path = att.get("saved_path", "")
        ext = _ext(att)
        real_path = _resolve_from_gridfs(saved_path)
        if not real_path:
            continue
        temp_files.append(real_path)
        try:
            text = _extract_one(real_path, ext)
        except Exception as exc:
            _log(f"[extract ERROR] {saved_path}: {exc}")
            text = ""
        if text:
            name = att.get("original_name") or os.path.basename(saved_path)
            combined += f"\n\n[Content from '{name}' ({ext.lstrip('.').upper()} data file)]:\n{text}\n"
            names.append(name)
    for tmp in temp_files:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return combined, names


def _language_rule(lang: str) -> str:
    if lang == "zh":
        return "Language rule: Reply entirely in Chinese."
    if lang == "ms":
        return "Language rule: Reply entirely in Malay."
    return "Language rule: Reply entirely in English."


def _detect_reply_lang(text: str) -> str:
    t = (text or "").strip()
    if re.search(r"[\u4e00-\u9fff]", t):
        return "zh"
    words = set(re.findall(r"[a-zA-Z']+", t.lower()))
    if len(words & {"saya", "nak", "boleh", "dengan", "untuk", "jualan", "untung", "kos"}) >= 2:
        return "ms"
    return "en"


def _initial_instruction(lang: str) -> str:
    return (
        f"{_language_rule(lang)}\n\n"
        "You are a financial intake analyst for entrepreneurs and MSMEs.\n"
        "The entrepreneur has uploaded structured business/financial data. Use only the data inside "
        "<financial_data_source>; do not invent figures.\n\n"
        "Produce the response in this exact workflow:\n"
        "1. Extract available information from the uploaded data.\n"
        "2. Fill a structured financial table with these rows where possible: Business Name, Period, Revenue/Sales, "
        "COGS/Direct Costs, Gross Profit, Operating Expenses, Net Profit, Cash Balance, Assets, Liabilities, Debt/Loans, "
        "Employees, Key Products/Services, Customer/Market Notes.\n"
        "3. Detect missing fields. Separate `Required for basic summary` from `Useful for better recommendations`.\n"
        "4. Ask concise follow-up questions for the missing fields. Ask no more than 8 questions and prioritize the fields "
        "needed to calculate profitability, cash position, and debt risk.\n"
        "5. Generate a preliminary financial summary and recommendations using only available data. Clearly label it "
        "`Preliminary` if important fields are missing.\n\n"
        "Formatting rules:\n"
        "- Use Markdown tables for the filled financial table and missing-field list.\n"
        "- Write `N/A` when a value is not available.\n"
        "- If a metric can be calculated from available fields, calculate it and state the formula briefly.\n"
        "- When the data supports trends, projections, category breakdowns, or comparisons, include 1-3 chart blocks using this exact fenced format so the PDF renderer can draw real charts:\n"
        "```chart\n"
        "{\"type\":\"bar\",\"title\":\"Sales by Month\",\"labels\":[\"Jan\",\"Feb\"],\"series\":[{\"name\":\"Sales\",\"values\":[1000,1200]}]}\n"
        "```\n"
        "- Use `bar` for comparisons, `line` for trends/projections, and `pie` for composition/breakdown. Only use numeric values grounded in the uploaded data or clearly labelled calculations.\n"
        "- End with the follow-up questions as the final section."
    )


def _followup_instruction(lang: str) -> str:
    return (
        f"{_language_rule(lang)}\n\n"
        "You are continuing a financial intake workflow for an entrepreneur/MSME.\n"
        "Use the uploaded data in <financial_data_source> plus the user's latest answers in the conversation.\n\n"
        "Update the structured financial table, mark which missing fields are now resolved, and then generate:\n"
        "- Financial summary\n"
        "- Key risks or gaps\n"
        "- Practical recommendations\n"
        "- Remaining follow-up questions only if important fields are still missing\n\n"
        "When the user asks for projections, trends, comparisons, or breakdowns, include 1-3 chart blocks using this exact fenced format so the generated PDF can draw real charts:\n"
        "```chart\n"
        "{\"type\":\"line\",\"title\":\"Projected Sales\",\"labels\":[\"Oct\",\"Nov\",\"Dec\"],\"series\":[{\"name\":\"Sales\",\"values\":[4900,5200,5500]}]}\n"
        "```\n"
        "Use `bar` for comparisons, `line` for trends/projections, and `pie` for composition/breakdown. Do not generate a PDF unless the user explicitly asks for one. Use `N/A` rather than invented values."
    )


def process_agent_request(chat_id: str, user_message: str, attachments: list, history_messages: list = None):
    state = agent_memory.setdefault(chat_id, {
        "source_data": "",
        "processed_files": set(),
        "reply_lang": "en",
    })
    if (user_message or "").strip():
        state["reply_lang"] = _detect_reply_lang(user_message)
    reply_lang = state.get("reply_lang", "en")

    new_attachments = [
        att for att in supported_data_attachments(attachments)
        if att.get("saved_path") not in state["processed_files"]
    ]
    new_text, new_names = _extract_attachments(new_attachments)
    for att in new_attachments:
        if att.get("saved_path"):
            state["processed_files"].add(att["saved_path"])

    if new_text:
        state["source_data"] = (state.get("source_data", "") + new_text)[-80_000:]
        state["source_names"] = sorted(set((state.get("source_names") or []) + new_names))
        instruction = _initial_instruction(reply_lang)
    elif state.get("source_data"):
        instruction = _followup_instruction(reply_lang)
    else:
        instruction = (
            f"{_language_rule(reply_lang)}\n\n"
            "Ask the user to upload a CSV, Excel, JSON, or text file containing their business/financial data."
        )

    hidden_context = ""
    if state.get("source_data"):
        hidden_context = f"\n\n<financial_data_source>\n{state['source_data']}\n</financial_data_source>"
    _log(f"[agent] chat={chat_id} new_files={len(new_attachments)} source_chars={len(state.get('source_data', ''))}")
    return instruction, hidden_context
