#!/usr/bin/env python3
"""
Ingest Finetune source files into the dedicated PGVector knowledge collection.

This is the lightweight alternative to fine-tuning:
  Finetune/*.json, *.jsonl, *.pdf -> chunks -> Ollama embeddings -> PGVector
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from langchain_core.documents import Document

ROOT_DIR = Path(__file__).resolve().parents[1]
KNOWLEDGE_AGENT_PATH = ROOT_DIR / "AI agent" / "knowledge_agent.py"
spec = importlib.util.spec_from_file_location("knowledge_agent", KNOWLEDGE_AGENT_PATH)
knowledge_agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(knowledge_agent)


TEXT_KEYS = {
    "title", "name", "agency", "filename", "question", "instruction",
    "description", "text", "answer", "content", "summary", "category",
}


def clean_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u0007", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def chunk_text(text: str, max_chars: int = 1800, overlap: int = 180) -> Iterable[str]:
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
            cut = max(text.rfind(".", start, end), text.rfind("。", start, end), text.rfind("\n", start, end))
            if cut > start + max_chars // 2:
                end = cut + 1
        chunk = text[start:end].strip()
        if chunk:
            yield chunk
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)


def flatten_json(value: Any, prefix: str = "") -> Iterable[tuple[str, str, dict[str, Any]]]:
    if isinstance(value, dict):
        fields = []
        metadata = {}
        for key, child in value.items():
            if key in TEXT_KEYS and isinstance(child, (str, int, float)):
                fields.append(f"{key}: {clean_text(child)}")
                if key in {"title", "name", "agency", "filename", "category"}:
                    metadata[key] = clean_text(child)
        if fields:
            yield prefix or "record", " | ".join(fields), metadata
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from flatten_json(child, child_prefix)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            child_prefix = f"{prefix}[{idx}]"
            yield from flatten_json(child, child_prefix)
    elif isinstance(value, (str, int, float)):
        text = clean_text(value)
        if text:
            yield prefix or "value", text, {}


def load_json_file(path: Path, rel_source: str, max_chars: int, min_chars: int) -> Iterable[Document]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return

    for section, text, meta in flatten_json(data, path.stem):
        for idx, chunk in enumerate(chunk_text(text, max_chars), start=1):
            if len(chunk) < min_chars:
                continue
            yield Document(
                page_content=chunk,
                metadata={
                    "source": rel_source,
                    "section": section,
                    "chunk_index": idx,
                    **meta,
                },
            )


def load_jsonl_file(path: Path, rel_source: str, max_chars: int, min_chars: int) -> Iterable[Document]:
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue

            text = clean_text(record.get("text") or record.get("content") or record.get("answer") or "")
            metadata = {
                "source": rel_source,
                "line": line_no,
                "agency": clean_text(record.get("agency", "")),
                "filename": clean_text(record.get("filename", "")),
                "title": clean_text(record.get("title", "")),
            }
            if text:
                for idx, chunk in enumerate(chunk_text(text, max_chars), start=1):
                    if len(chunk) < min_chars:
                        continue
                    yield Document(page_content=chunk, metadata={**metadata, "chunk_index": idx})
            else:
                for section, flat_text, meta in flatten_json(record, f"line[{line_no}]"):
                    for idx, chunk in enumerate(chunk_text(flat_text, max_chars), start=1):
                        if len(chunk) < min_chars:
                            continue
                        yield Document(
                            page_content=chunk,
                            metadata={**metadata, **meta, "section": section, "chunk_index": idx},
                        )


def load_pdf_file(path: Path, rel_source: str, max_chars: int, min_chars: int) -> Iterable[Document]:
    try:
        import pymupdf
    except Exception:
        print(f"Skipping PDF because pymupdf is unavailable: {rel_source}")
        return

    doc = pymupdf.open(path)
    for page_no, page in enumerate(doc, start=1):
        text = clean_text(page.get_text())
        for idx, chunk in enumerate(chunk_text(text, max_chars), start=1):
            if len(chunk) < min_chars:
                continue
            yield Document(
                page_content=chunk,
                metadata={"source": rel_source, "page": page_no, "chunk_index": idx},
            )


def iter_documents(
    input_path: Path,
    max_chars: int,
    min_chars: int,
    include_pdf: bool = False,
    include_dictionary: bool = False,
) -> Iterable[Document]:
    paths = [input_path] if input_path.is_file() else sorted(input_path.rglob("*"))
    for path in paths:
        if not path.is_file():
            continue
        rel_source = path.relative_to(ROOT_DIR).as_posix() if path.is_relative_to(ROOT_DIR) else path.as_posix()
        if not include_dictionary and "/dictionary/" in f"/{rel_source}":
            continue
        suffix = path.suffix.lower()
        if suffix == ".json":
            yield from load_json_file(path, rel_source, max_chars, min_chars)
        elif suffix == ".jsonl":
            yield from load_jsonl_file(path, rel_source, max_chars, min_chars)
        elif suffix == ".pdf" and include_pdf:
            yield from load_pdf_file(path, rel_source, max_chars, min_chars)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="Finetune", help="Finetune file or folder to ingest.")
    parser.add_argument("--reset", action="store_true", help="Clear the RAG collection before ingesting.")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--max-chars", type=int, default=1800)
    parser.add_argument("--min-chars", type=int, default=80)
    parser.add_argument("--limit", type=int, default=0, help="Debug limit; 0 means no limit.")
    parser.add_argument("--include-pdf", action="store_true", help="Also ingest PDF files. Off by default to keep RAG precise.")
    parser.add_argument("--include-dictionary", action="store_true", help="Also ingest Finetune/dictionary. Off by default to avoid dictionary noise.")
    args = parser.parse_args()

    input_path = (ROOT_DIR / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")

    if args.reset:
        print(f"Resetting collection: {knowledge_agent.COLLECTION_NAME}")
        knowledge_agent.reset_knowledge_collection()

    def limited_docs() -> Iterable[Document]:
        docs = iter_documents(
            input_path,
            args.max_chars,
            args.min_chars,
            include_pdf=args.include_pdf,
            include_dictionary=args.include_dictionary,
        )
        for count, doc in enumerate(docs, start=1):
            if args.limit and count > args.limit:
                break
            yield doc

    total = knowledge_agent.add_knowledge_documents(limited_docs(), batch_size=args.batch_size)
    print(f"Ingested {total} chunks into collection: {knowledge_agent.COLLECTION_NAME}")


if __name__ == "__main__":
    main()
