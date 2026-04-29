#!/usr/bin/env python3
"""
Build a simple supervised fine-tuning JSONL dataset from the Finetune folder.

Output format:
  {"messages": [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}], "source": "..."}

This prepares data for LoRA/QLoRA training. It does not train an Ollama
quantized model directly, because Ollama GGUF/Q4 models are inference artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable


SYSTEM_PROMPT = (
    "You are MSME.AI, a professional assistant for Malaysian MSME, finance, "
    "business registration, banking, and government-support knowledge. Answer "
    "accurately, cite the relevant agency or document name when present, and keep "
    "the response practical."
)


def clean_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u0007", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def chunk_text(text: str, max_chars: int) -> Iterable[str]:
    text = clean_text(text)
    if len(text) <= max_chars:
        if text:
            yield text
        return

    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    buf: list[str] = []
    length = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if length + len(sentence) > max_chars and buf:
            yield " ".join(buf).strip()
            buf = []
            length = 0
        buf.append(sentence)
        length += len(sentence) + 1
    if buf:
        yield " ".join(buf).strip()


def flatten_json(value: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        text_fields = []
        for key in ("title", "name", "question", "instruction", "description", "text", "answer", "content"):
            if key in value and isinstance(value[key], (str, int, float)):
                text_fields.append(f"{key}: {clean_text(value[key])}")
        if text_fields:
            yield prefix or "record", " | ".join(text_fields)
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
            yield prefix or "value", text


def record_to_examples(record: dict[str, Any], source: str, max_chars: int, min_chars: int) -> Iterable[dict[str, Any]]:
    agency = clean_text(record.get("agency", ""))
    filename = clean_text(record.get("filename", ""))
    title = clean_text(record.get("title", ""))
    text = clean_text(record.get("text") or record.get("content") or record.get("answer") or record.get("description"))
    tags = record.get("dictionary_tags") or record.get("tags") or []
    tag_text = ", ".join(str(t) for t in tags[:12]) if isinstance(tags, list) else clean_text(tags)

    if not text:
        for path, flat_text in flatten_json(record):
            if len(flat_text) >= min_chars:
                user = f"Explain this Malaysian MSME knowledge item: {path}."
                assistant = flat_text
                yield make_example(user, assistant, source)
        return

    source_label = " / ".join(part for part in (agency, filename, title) if part) or source
    for idx, chunk in enumerate(chunk_text(text, max_chars), start=1):
        if len(chunk) < min_chars:
            continue
        tag_line = f" Relevant tags: {tag_text}." if tag_text else ""
        user = (
            f"Using the source '{source_label}', summarize the useful MSME or business guidance "
            f"from section {idx}.{tag_line}"
        )
        assistant = chunk
        yield make_example(user, assistant, source)


def make_example(user: str, assistant: str, source: str) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": clean_text(user)},
            {"role": "assistant", "content": clean_text(assistant)},
        ],
        "source": source,
    }


def load_file(path: Path, max_chars: int, min_chars: int) -> Iterable[dict[str, Any]]:
    suffix = path.suffix.lower()
    rel = path.as_posix()

    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield from record_to_examples(record, f"{rel}:{line_no}", max_chars, min_chars)
        return

    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return
        for item_path, text in flatten_json(data, path.stem):
            for chunk in chunk_text(text, max_chars):
                if len(chunk) < min_chars:
                    continue
                user = f"Explain the Malaysian MSME knowledge under '{item_path}'."
                yield make_example(user, chunk, rel)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="Finetune", help="Input folder containing json/jsonl source files.")
    parser.add_argument("--output", default="data/finetune/gemma4_mof_sft.jsonl")
    parser.add_argument("--max-chars", type=int, default=2800)
    parser.add_argument("--min-chars", type=int, default=120)
    parser.add_argument("--max-records", type=int, default=0, help="0 means no limit.")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with output.open("w", encoding="utf-8") as out:
        for path in sorted(input_dir.rglob("*")):
            if path.suffix.lower() not in {".json", ".jsonl"}:
                continue
            for example in load_file(path, args.max_chars, args.min_chars):
                out.write(json.dumps(example, ensure_ascii=False) + "\n")
                count += 1
                if args.max_records and count >= args.max_records:
                    print(f"Wrote {count} examples to {output}")
                    return

    print(f"Wrote {count} examples to {output}")


if __name__ == "__main__":
    main()
