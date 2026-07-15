#!/usr/bin/env python3
"""
Ingest Finetune source files into the PGVector knowledge collection.
PATCHED: Added bank-account-aware JSON loader and improved list-of-records handling.

Supported formats: .jsonl, .json, .pdf, .docx, .xlsx, .xls, .csv, .txt, .md
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import importlib.util
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

from langchain_core.documents import Document

# ---------------------------------------------------------------------------
# Bootstrap: load knowledge_agent from sibling directory
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[1]
KNOWLEDGE_AGENT_PATH = ROOT_DIR / "AI agent" / "knowledge_agent.py"
spec = importlib.util.spec_from_file_location("knowledge_agent", KNOWLEDGE_AGENT_PATH)
knowledge_agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(knowledge_agent)


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

def clean_text(value: Any) -> str:
    """Convert any value to a clean, normalised string."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return clean_text(_deep_stringify(value))
    text = str(value)
    text = text.replace("\u0007", " ")
    text = text.replace("\u00a0", " ")
    text = text.replace("\u200b", "")
    text = text.replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\ufeff", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text


def _deep_stringify(value: Any, depth: int = 0) -> str:
    if depth > 12:
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [_deep_stringify(v, depth + 1) for v in value]
        return " | ".join(p for p in parts if p)
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            v_str = _deep_stringify(v, depth + 1)
            if v_str:
                parts.append(f"{k}: {v_str}")
        return " | ".join(parts)
    return str(value)


def chunk_text(text: str, max_chars: int = 1800, overlap: int = 200) -> Iterable[str]:
    text = clean_text(text)
    if not text:
        return
    if len(text) <= max_chars:
        yield text
        return
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            for sep in ("\n\n", "。", ".\n", ". ", ";\n", ":\n", "\n"):
                cut = text.rfind(sep, start + max_chars // 2, end)
                if cut != -1:
                    end = cut + len(sep)
                    break
            else:
                space_cut = text.rfind(" ", start + max_chars // 2, end)
                if space_cut != -1:
                    end = space_cut + 1
        chunk = text[start:end].strip()
        if chunk:
            yield chunk
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)


# ---------------------------------------------------------------------------
# JSONL loaders
# ---------------------------------------------------------------------------

def load_jsonl_file(
    path: Path, rel_source: str, max_chars: int, min_chars: int
) -> Iterable[Document]:
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line_no, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue

            text_parts: list[str] = []
            for field in (
                "content", "text", "answer", "response", "body",
                "description", "instruction", "question", "prompt",
                "message", "output", "details", "notes", "summary",
            ):
                val = record.get(field)
                if val and str(val).strip():
                    text_parts.append(clean_text(val))

            if not text_parts:
                skip_keys = {"tags", "dictionary_tags", "source_type", "id", "line"}
                for k, v in record.items():
                    if k in skip_keys:
                        continue
                    v_str = clean_text(v)
                    if v_str:
                        text_parts.append(f"{k}: {v_str}")

            full_text = "\n".join(text_parts)
            if not full_text.strip():
                continue

            metadata: dict[str, Any] = {"source": rel_source, "line": line_no}
            for meta_field in ("title", "agency", "filename", "source_file",
                               "source_type", "category", "name"):
                val = record.get(meta_field)
                if val and isinstance(val, (str, int, float)):
                    metadata[meta_field] = clean_text(val)

            for tag_field in ("tags", "dictionary_tags"):
                tags = record.get(tag_field)
                if isinstance(tags, list) and tags:
                    metadata[tag_field] = ", ".join(str(t) for t in tags if t)

            prefix_parts: list[str] = []
            if metadata.get("title"):
                prefix_parts.append(metadata["title"])
            if metadata.get("agency"):
                prefix_parts.append(f"Agensi: {metadata['agency']}")
            context_prefix = " | ".join(prefix_parts)

            for idx, chunk in enumerate(chunk_text(full_text, max_chars), start=1):
                if len(chunk) < min_chars:
                    continue
                enriched_content = (
                    f"{context_prefix}\n{chunk}" if context_prefix else chunk
                )
                yield Document(
                    page_content=enriched_content,
                    metadata={**metadata, "chunk_index": idx},
                )


# ---------------------------------------------------------------------------
# *** PATCHED: Bank-account-aware JSON loader ***
# ---------------------------------------------------------------------------

def _flatten_docs_list(
    value: Any, depth: int = 0
) -> str:
    """Recursively flatten a dict/list into readable key: value lines."""
    if depth > 8:
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            s = _flatten_docs_list(item, depth + 1)
            if s:
                parts.append(f"  - {s}")
        return "\n".join(parts)
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            v_str = _flatten_docs_list(v, depth + 1)
            if v_str:
                # Indent nested content for readability
                if "\n" in v_str:
                    lines.append(f"{k}:\n{v_str}")
                else:
                    lines.append(f"{k}: {v_str}")
        return "\n".join(lines)
    return str(value)


def _render_bank_account(account: dict) -> str:
    """
    Render a single bank account record into a rich, self-contained text block.
    Every section explicitly re-states the bank name so all chunks carry identity.
    
    This is the KEY FIX: instead of fragmenting nested dicts into tiny scalar pairs,
    we produce one coherent narrative per bank that survives chunking with identity intact.
    """
    bank = account.get("bank_name", "Unknown Bank")
    acct = account.get("account_name", "")
    best_for = account.get("best_for", "Not stated in dataset")

    # Header — repeated in every chunk via context prefix
    lines = [
        f"Bank: {bank}",
        f"Account: {acct}" if acct else "",
        f"Best For: {best_for}",
    ]

    # Key features
    kf = account.get("key_features", {})
    if kf:
        lines.append("\nKey Features:")
        init_dep = kf.get("initial_deposit", "Not stated in dataset")
        svc = kf.get("service_charge", "Not stated in dataset")
        opening = kf.get("account_opening", "Not stated in dataset")
        introducer = kf.get("introducer_required")
        intro_str = "Yes" if introducer else ("No" if introducer is False else "Not stated in dataset")
        lines.append(f"  Initial Deposit: {init_dep}")
        lines.append(f"  Service Charge: {svc}")
        lines.append(f"  Account Opening Method: {opening}")
        lines.append(f"  Introducer Required: {intro_str}")
        standout = kf.get("standout_features", [])
        if standout:
            lines.append("  Standout Features:")
            for feat in standout:
                lines.append(f"    - {feat}")

    # Documents required
    docs_req = account.get("documents_required", {})
    if docs_req:
        lines.append("\nDocuments Required:")
        for entity_type, items in docs_req.items():
            if isinstance(items, list):
                lines.append(f"  {entity_type.replace('_', ' ').title()}:")
                for item in items:
                    lines.append(f"    - {item}")
            elif isinstance(items, dict):
                lines.append(f"  {entity_type.replace('_', ' ').title()}:")
                rendered = _flatten_docs_list(items)
                for sub_line in rendered.splitlines():
                    lines.append(f"    {sub_line}")
            elif isinstance(items, str):
                lines.append(f"  {entity_type.replace('_', ' ').title()}: {items}")

    return "\n".join(l for l in lines if l is not None)


def _is_bank_accounts_json(data: Any) -> bool:
    """Detect the Malaysia Business Bank Accounts JSON shape."""
    if not isinstance(data, dict):
        return False
    accounts = data.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        return False
    first = accounts[0]
    return isinstance(first, dict) and "bank_name" in first and "key_features" in first


def load_bank_accounts_json(
    data: dict, path: Path, rel_source: str, max_chars: int, min_chars: int
) -> Iterable[Document]:
    """
    Dedicated loader for the bank accounts JSON.
    Produces one rich Document per bank, chunked only if text exceeds max_chars.
    Every chunk carries the bank name in metadata for reliable retrieval.
    """
    dataset_name = data.get("dataset", path.stem)
    base_meta = {
        "source": rel_source,
        "filename": path.name,
        "dataset": dataset_name,
        "last_reviewed": data.get("last_reviewed", ""),
    }

    accounts: list[dict] = data.get("accounts", [])
    print(f"  [BANK] Detected bank accounts JSON — loading {len(accounts)} banks")

    for account in accounts:
        if not isinstance(account, dict):
            continue
        bank_name = account.get("bank_name", "Unknown")
        bank_text = _render_bank_account(account)

        # Prefix for every chunk: dataset + bank name = strong retrieval anchor
        context_prefix = f"{dataset_name} | Bank: {bank_name}"
        acct_meta = {
            **base_meta,
            "bank_name": bank_name,
            "account_name": account.get("account_name", ""),
            "record_type": "bank_account",
        }

        for idx, chunk in enumerate(chunk_text(bank_text, max_chars), start=1):
            if len(chunk) < min_chars:
                continue
            enriched = f"{context_prefix}\n{chunk}"
            yield Document(
                page_content=enriched,
                metadata={**acct_meta, "chunk_index": idx},
            )


# ---------------------------------------------------------------------------
# JSON loader (handles arbitrary nesting, with bank-account specialisation)
# ---------------------------------------------------------------------------

def _extract_text_blocks(
    value: Any,
    path: str = "",
    depth: int = 0,
) -> Iterable[tuple[str, str]]:
    if depth > 15:
        return

    if isinstance(value, dict):
        scalar_parts: list[str] = []
        for k, v in value.items():
            if isinstance(v, (str, int, float)) and str(v).strip():
                v_clean = clean_text(str(v))
                if len(v_clean) >= 3:
                    scalar_parts.append(f"{k}: {v_clean}")
        if scalar_parts:
            block = " | ".join(scalar_parts)
            if len(block) >= 40:
                yield path or "root", block
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                child_path = f"{path}.{k}" if path else k
                yield from _extract_text_blocks(v, child_path, depth + 1)

    elif isinstance(value, list):
        for i, item in enumerate(value):
            child_path = f"{path}[{i}]"
            if isinstance(item, (dict, list)):
                yield from _extract_text_blocks(item, child_path, depth + 1)
            elif isinstance(item, str) and len(item.strip()) >= 20:
                yield child_path, clean_text(item)

    elif isinstance(value, str):
        t = clean_text(value)
        if len(t) >= 20:
            yield path or "value", t


def load_json_file(
    path: Path, rel_source: str, max_chars: int, min_chars: int
) -> Iterable[Document]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON parse error in {rel_source}: {e}")
        return

    # *** PATCH: Route bank account JSON to its dedicated loader ***
    if _is_bank_accounts_json(data):
        yield from load_bank_accounts_json(data, path, rel_source, max_chars, min_chars)
        return

    base_meta: dict[str, Any] = {"source": rel_source, "filename": path.name}

    if isinstance(data, dict):
        for scalar_key in ("title", "agency", "name", "category",
                           "dataset", "source_type", "version", "tahun", "year"):
            val = data.get(scalar_key)
            if val and isinstance(val, (str, int, float)):
                base_meta[scalar_key] = clean_text(str(val))

    for block_path, block_text in _extract_text_blocks(data, path.stem):
        anchor = base_meta.get("title") or base_meta.get("name") or base_meta.get("dataset", "")
        anchored_text = f"{anchor}\n{block_text}" if anchor and anchor not in block_text else block_text

        for idx, chunk in enumerate(chunk_text(anchored_text, max_chars), start=1):
            if len(chunk) < min_chars:
                continue
            yield Document(
                page_content=chunk,
                metadata={**base_meta, "section": block_path, "chunk_index": idx},
            )


# ---------------------------------------------------------------------------
# PDF loader
# ---------------------------------------------------------------------------

def load_pdf_file(
    path: Path, rel_source: str, max_chars: int, min_chars: int
) -> Iterable[Document]:
    try:
        import pymupdf
    except ImportError:
        print(f"  [SKIP] pymupdf not installed — skipping PDF: {rel_source}")
        return
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        print(f"  [WARN] Cannot open PDF {rel_source}: {e}")
        return
    base_meta = {"source": rel_source, "filename": path.name}
    try:
        for page_no, page in enumerate(doc, start=1):
            try:
                text = clean_text(page.get_text())
            except Exception:
                continue
            for idx, chunk in enumerate(chunk_text(text, max_chars), start=1):
                if len(chunk) < min_chars:
                    continue
                yield Document(
                    page_content=chunk,
                    metadata={**base_meta, "page": page_no, "chunk_index": idx},
                )
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# DOCX loader
# ---------------------------------------------------------------------------

def load_docx_file(
    path: Path, rel_source: str, max_chars: int, min_chars: int
) -> Iterable[Document]:
    try:
        with zipfile.ZipFile(path, "r") as z:
            with z.open("word/document.xml") as f:
                tree = ET.parse(f)
    except Exception as e:
        print(f"  [WARN] Cannot read DOCX {rel_source}: {e}")
        return
    paragraphs: list[str] = []
    current: list[str] = []
    for node in tree.iter():
        tag = node.tag
        if not isinstance(tag, str):
            continue
        local = tag.split("}")[-1] if "}" in tag else tag
        if local == "p":
            para = "".join(current).strip()
            if para:
                paragraphs.append(para)
            current = []
        elif local == "t":
            current.append(node.text or "")
    if current:
        para = "".join(current).strip()
        if para:
            paragraphs.append(para)
    full_text = "\n".join(paragraphs)
    if not full_text.strip():
        return
    base_meta = {"source": rel_source, "filename": path.name}
    for idx, chunk in enumerate(chunk_text(full_text, max_chars), start=1):
        if len(chunk) < min_chars:
            continue
        yield Document(page_content=chunk, metadata={**base_meta, "chunk_index": idx})


# ---------------------------------------------------------------------------
# XLSX loader
# ---------------------------------------------------------------------------

def load_xlsx_file(
    path: Path, rel_source: str, max_chars: int, min_chars: int
) -> Iterable[Document]:
    try:
        with zipfile.ZipFile(path, "r") as z:
            shared: dict[int, str] = {}
            if "xl/sharedStrings.xml" in z.namelist():
                with z.open("xl/sharedStrings.xml") as f:
                    root = ET.parse(f).getroot()
                    for si in root.findall(".//{*}si"):
                        parts = [t.text or "" for t in si.findall(".//{*}t")]
                        shared[len(shared)] = "".join(parts)
            base_meta = {"source": rel_source, "filename": path.name}
            for name in sorted(
                n for n in z.namelist()
                if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
            ):
                sheet_label = name.split("/")[-1].replace(".xml", "")
                with z.open(name) as f:
                    root = ET.parse(f).getroot()
                rows_text: list[str] = []
                for row in root.findall(".//{*}row"):
                    cell_values: list[str] = []
                    for c in row.findall("{*}c"):
                        ttype = c.get("t")
                        v_el = c.find("{*}v")
                        is_el = c.find("{*}is")
                        value = ""
                        if v_el is not None and v_el.text:
                            if ttype == "s":
                                value = shared.get(int(v_el.text), "")
                            elif ttype == "b":
                                value = "TRUE" if v_el.text == "1" else "FALSE"
                            else:
                                value = v_el.text
                        elif is_el is not None:
                            value = "".join(t.text or "" for t in is_el.findall(".//{*}t"))
                        if value.strip():
                            cell_values.append(value.strip())
                    if cell_values:
                        rows_text.append(", ".join(cell_values))
                full_text = clean_text("\n".join(rows_text))
                if not full_text:
                    continue
                for idx, chunk in enumerate(chunk_text(full_text, max_chars), start=1):
                    if len(chunk) < min_chars:
                        continue
                    yield Document(
                        page_content=chunk,
                        metadata={**base_meta, "sheet": sheet_label, "chunk_index": idx},
                    )
    except Exception as e:
        print(f"  [WARN] Cannot read XLSX {rel_source}: {e}")
        return


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------

def load_csv_file(
    path: Path, rel_source: str, max_chars: int, min_chars: int
) -> Iterable[Document]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
            rows = list(csv.reader(f))
    except Exception as e:
        print(f"  [WARN] Cannot read CSV {rel_source}: {e}")
        return
    if not rows:
        return
    base_meta = {"source": rel_source, "filename": path.name}
    flat = " ".join(c.strip() for r in rows[:6] for c in r if c.strip())
    is_monthly_platform = bool(re.search(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\s+Sales',
        flat, re.I
    ))
    is_sales_report = "DD SALES REPORT" in flat.upper() or (
        "DAPUR" in flat.upper() and "CATEGORIES" in flat.upper()
    )
    if is_monthly_platform:
        title = ""
        locations: list[str] = []
        data_rows: list[list[str]] = []
        PLATFORMS = {"GrabFood","ShopeeFood","FoodPanda","Catering","Total",
                     "Walk-in","Direct","Bungkus","WhatsApp","Others","Lain-lain"}
        for row in rows:
            joined = " ".join(c.strip() for c in row if c.strip())
            if not joined:
                continue
            m = re.search(
                r'(January|February|March|April|May|June|July|August|'
                r'September|October|November|December)\s+\d{4}.*',
                joined, re.I
            )
            if m and not title:
                title = joined
            elif any(kw in joined for kw in
                     ["Wangsa","Intan","Kampung","Kerinchi","Setapak","Hiliran","PPR"]):
                locs = [c.strip() for c in row if c.strip()]
                if len(locs) >= 2:
                    locations = locs
            elif row and row[0].strip() in PLATFORMS:
                data_rows.append(row)
        if not title:
            title = path.stem
        lines = [f"Dapur Digital Monthly Sales Report: {title}"]
        if locations:
            lines.append(f"Kitchens: {', '.join(locations)}")
        totals: dict[str, float] = {}
        for row in data_rows:
            platform = row[0].strip()
            vals = [c.strip() for c in row[1:]]
            paired = []
            for i, loc in enumerate(locations):
                if i < len(vals) and vals[i] and vals[i] not in ("","0","RM0.00","0.00","-"):
                    paired.append(f"{loc}: {vals[i]}")
                    num = re.sub(r"[^\d.]", "", vals[i])
                    try:
                        totals[loc] = totals.get(loc, 0) + float(num)
                    except Exception:
                        pass
            if paired:
                lines.append(f"{platform}: " + " | ".join(paired))
        if totals:
            lines.append("Total per Kitchen: " +
                         " | ".join(f"{loc}: RM{v:,.2f}" for loc, v in totals.items()))
            lines.append(f"Grand Total: RM{sum(totals.values()):,.2f}")
        text = "\n".join(lines)
        if len(text) >= min_chars:
            yield Document(
                page_content=text,
                metadata={**base_meta, "report_type": "monthly_sales", "chunk_index": 1},
            )
        return
    if is_sales_report:
        month_row_idx = None
        month_col_map: dict[str, int] = {}
        for i, row in enumerate(rows[:8]):
            found = [(re.search(
                r'(January|February|March|April|May|June|July|August|'
                r'September|October|November|December)\s*\d{4}', c, re.I
            ), ci) for ci, c in enumerate(row)]
            hits = [(m.group(0).strip(), ci) for m, ci in found if m]
            if len(hits) >= 2:
                month_row_idx = i
                month_col_map = {month: ci for month, ci in hits}
                break
        if month_row_idx is not None:
            dapur_data: dict[str, dict[str, dict[str, float]]] = {}
            for row in rows[month_row_idx + 2:]:
                if len(row) < 3:
                    continue
                dapur_name = row[1].strip() if len(row) > 1 else ""
                category   = row[2].strip() if len(row) > 2 else "Sales"
                if not dapur_name or dapur_name.upper() in ("DAPUR","NO","","CATEGORIES"):
                    continue
                if dapur_name not in dapur_data:
                    dapur_data[dapur_name] = {}
                for month, ci in month_col_map.items():
                    if ci < len(row):
                        num = re.sub(r"[^\d.]", "", row[ci].strip())
                        if num:
                            try:
                                v = float(num)
                                if month not in dapur_data[dapur_name]:
                                    dapur_data[dapur_name][month] = {}
                                k = category or "Sales"
                                dapur_data[dapur_name][month][k] = (
                                    dapur_data[dapur_name][month].get(k, 0) + v
                                )
                            except Exception:
                                pass
            for dapur, month_data in dapur_data.items():
                lines = [f"Dapur Digital Sales Report: {dapur}"]
                for month in sorted(month_data.keys()):
                    cats = month_data[month]
                    total = sum(cats.values())
                    cat_str = " | ".join(f"{k}: RM{v:,.2f}" for k, v in cats.items())
                    lines.append(f"{month}: Total RM{total:,.2f} ({cat_str})")
                text = "\n".join(lines)
                if len(text) >= min_chars:
                    yield Document(
                        page_content=text,
                        metadata={**base_meta, "dapur": dapur,
                                  "report_type": "dd_sales", "chunk_index": 1},
                    )
            return
    try:
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",\t|;")
            except csv.Error:
                dialect = csv.excel
            all_rows = list(csv.reader(f, dialect))
    except Exception as e:
        print(f"  [WARN] Generic CSV read failed {rel_source}: {e}")
        return
    headers: list[str] = []
    text_rows: list[str] = []
    inferred_title: str = ""
    for ri, row in enumerate(all_rows):
        if not any(c.strip() for c in row):
            continue
        if ri == 0 and not inferred_title:
            candidate = " ".join(c.strip() for c in row if c.strip())
            numeric_count = sum(
                1 for c in row
                if re.match(r"^[\d,.\s]+$", c.strip()) and c.strip()
            )
            if numeric_count <= len(row) // 3:
                inferred_title = candidate
                headers = [c.strip() for c in row]
                continue
        if ri == 0:
            headers = [c.strip() for c in row]
            continue
        if headers:
            pairs = [f"{h}: {v.strip()}" for h, v in zip(headers, row) if v.strip()]
            if pairs:
                text_rows.append(" | ".join(pairs))
        else:
            vals = [c.strip() for c in row if c.strip()]
            if vals:
                text_rows.append(", ".join(vals))
    full_text = clean_text("\n".join(text_rows))
    if not full_text:
        return
    for idx, chunk in enumerate(chunk_text(full_text, max_chars), start=1):
        if len(chunk) < min_chars:
            continue
        enriched = f"{inferred_title}\n{chunk}" if inferred_title else chunk
        yield Document(
            page_content=enriched,
            metadata={
                **base_meta,
                **({"report_title": inferred_title} if inferred_title else {}),
                "chunk_index": idx,
            },
        )


# ---------------------------------------------------------------------------
# TXT / Markdown loader
# ---------------------------------------------------------------------------

def load_text_file(
    path: Path, rel_source: str, max_chars: int, min_chars: int
) -> Iterable[Document]:
    try:
        text = clean_text(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:
        print(f"  [WARN] Cannot read text file {rel_source}: {e}")
        return
    if not text:
        return
    base_meta = {"source": rel_source, "filename": path.name}
    for idx, chunk in enumerate(chunk_text(text, max_chars), start=1):
        if len(chunk) < min_chars:
            continue
        yield Document(page_content=chunk, metadata={**base_meta, "chunk_index": idx})


# ---------------------------------------------------------------------------
# Placeholder
# ---------------------------------------------------------------------------

def make_placeholder(path: Path, rel_source: str, reason: str) -> Document:
    return Document(
        page_content=(
            f"[File indexed but not extracted: {rel_source}] "
            f"Reason: {reason}"
        ),
        metadata={
            "source": rel_source,
            "filename": path.name,
            "extraction_status": "failed",
            "reason": reason,
        },
    )


# ---------------------------------------------------------------------------
# Master iterator
# ---------------------------------------------------------------------------

LOADERS = {
    ".jsonl": load_jsonl_file,
    ".json":  load_json_file,
    ".pdf":   load_pdf_file,
    ".docx":  load_docx_file,
    ".xlsx":  load_xlsx_file,
    ".xls":   load_xlsx_file,
    ".csv":   load_csv_file,
    ".tsv":   load_csv_file,
    ".txt":   load_text_file,
    ".md":    load_text_file,
}

SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".exe", ".dll", ".so", ".bin",
    ".pyc", ".pyo", "__pycache__",
    ".DS_Store", ".gitignore", ".env",
}

SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules", ".mypy_cache"}


def iter_documents(
    input_path: Path,
    max_chars: int,
    min_chars: int,
    include_dictionary: bool = False,
    include_pdf: bool = True,
    verbose: bool = False,
) -> Iterable[Document]:
    paths = [input_path] if input_path.is_file() else sorted(input_path.rglob("*"))
    for path in paths:
        if not path.is_file():
            continue
        if any(part.startswith(".") or part in SKIP_DIRS for part in path.parts):
            continue
        rel_source = (
            path.relative_to(ROOT_DIR).as_posix()
            if path.is_relative_to(ROOT_DIR)
            else path.as_posix()
        )
        if not include_dictionary and "/dictionary/" in f"/{rel_source}":
            continue
        suffix = path.suffix.lower()
        if suffix in SKIP_EXTENSIONS:
            continue
        if suffix == ".pdf" and not include_pdf:
            if verbose:
                print(f"  [SKIP] PDF (use --include-pdf): {rel_source}")
            continue
        loader_fn = LOADERS.get(suffix)
        if loader_fn is None:
            if verbose:
                print(f"  [SKIP] Unsupported extension '{suffix}': {rel_source}")
            continue
        if verbose:
            print(f"  [LOAD] {rel_source}")
        yielded = False
        try:
            for doc in loader_fn(path, rel_source, max_chars, min_chars):
                yielded = True
                yield doc
        except Exception as e:
            print(f"  [ERROR] Unhandled exception loading {rel_source}: {e}")
        if not yielded:
            print(f"  [WARN] No text extracted from, skipping: {rel_source}")


# ---------------------------------------------------------------------------
# Ingest with progress reporting
# ---------------------------------------------------------------------------

def ingest_with_progress(
    docs: Iterable[Document],
    vectorstore: Any,
    batch_size: int,
) -> int:
    batch: list[Document] = []
    seen_hashes: set[str] = set()
    total = 0
    batch_num = 0
    skipped_empty = 0
    skipped_duplicates = 0
    for doc in docs:
        if not doc.page_content or not doc.page_content.strip():
            skipped_empty += 1
            continue

        dedupe_text = re.sub(r"\s+", " ", doc.page_content).strip().lower()
        content_hash = hashlib.sha256(dedupe_text.encode("utf-8")).hexdigest()
        if content_hash in seen_hashes:
            skipped_duplicates += 1
            continue
        seen_hashes.add(content_hash)

        batch.append(doc)
        if len(batch) >= batch_size:
            batch_num += 1
            vectorstore.add_documents(batch)
            total += len(batch)
            print(f"  Batch {batch_num:>4}: +{len(batch):>5} chunks  (total {total:>7})")
            batch.clear()
    if batch:
        batch_num += 1
        vectorstore.add_documents(batch)
        total += len(batch)
        print(f"  Batch {batch_num:>4}: +{len(batch):>5} chunks  (total {total:>7})")
    if skipped_empty:
        print(f"  [INFO] Skipped {skipped_empty} empty documents")
    if skipped_duplicates:
        print(f"  [INFO] Skipped {skipped_duplicates} duplicate chunks before embedding")
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Finetune files into PGVector RAG collection."
    )
    parser.add_argument("--input", default="Finetune")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--batch-size",  type=int, default=64)
    parser.add_argument("--max-chars",   type=int, default=1800)
    parser.add_argument("--min-chars",   type=int, default=30)
    parser.add_argument("--limit",       type=int, default=0)
    parser.add_argument("--no-pdf",      action="store_true")
    parser.add_argument("--include-dictionary", action="store_true")
    parser.add_argument("--verbose",     action="store_true")
    args = parser.parse_args()

    input_path = (
        Path(args.input)
        if Path(args.input).is_absolute()
        else (ROOT_DIR / args.input).resolve()
    )
    if not input_path.exists():
        sys.exit(f"[ERROR] Input not found: {input_path}")

    if args.reset:
        print(f"Resetting collection: {knowledge_agent.COLLECTION_NAME}")
        knowledge_agent.reset_knowledge_collection()

    supported: dict[str, int] = collections.Counter()
    skipped:   dict[str, int] = collections.Counter()
    file_paths = [input_path] if input_path.is_file() else sorted(input_path.rglob("*"))
    for fp in file_paths:
        if not fp.is_file():
            continue
        if any(p.startswith(".") or p in SKIP_DIRS for p in fp.parts):
            continue
        rel = fp.relative_to(ROOT_DIR).as_posix() if fp.is_relative_to(ROOT_DIR) else fp.as_posix()
        if not args.include_dictionary and "/dictionary/" in f"/{rel}":
            continue
        sfx = fp.suffix.lower()
        if sfx in SKIP_EXTENSIONS:
            skipped[sfx or "<no suffix>"] += 1
        elif sfx == ".pdf" and args.no_pdf:
            skipped[".pdf (skipped)"] += 1
        elif sfx in LOADERS:
            supported[sfx] += 1
        else:
            skipped[sfx or "<no suffix>"] += 1

    print("\nFiles queued for ingestion:")
    for ext, count in sorted(supported.items()):
        print(f"  {ext:10s} {count:>5} file(s)")
    if skipped:
        print("Files skipped:")
        for ext, count in sorted(skipped.items()):
            print(f"  {ext:20s} {count:>5} file(s)")
    print()

    def limited_docs() -> Iterable[Document]:
        gen = iter_documents(
            input_path,
            max_chars=args.max_chars,
            min_chars=args.min_chars,
            include_dictionary=args.include_dictionary,
            include_pdf=not args.no_pdf,
            verbose=args.verbose,
        )
        for n, doc in enumerate(gen, start=1):
            if args.limit and n > args.limit:
                print(f"[DEBUG] Stopped at limit={args.limit}")
                break
            yield doc

    vectorstore = knowledge_agent.get_vectorstore()
    total = ingest_with_progress(limited_docs(), vectorstore, args.batch_size)
    print(f"\nDone. {total} chunks ingested into: {knowledge_agent.COLLECTION_NAME}")


if __name__ == "__main__":
    main()
