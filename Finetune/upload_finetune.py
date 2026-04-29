"""
upload_finetune.py
==================
Local-only finetune exporter.

This script does NOT use AWS/Bedrock and does NOT touch your running bot.
It only reads Finetune/training_output/combined_training.jsonl and writes
cleaned local training files under Finetune/training_output/.

USAGE:
    cd ~/MSME-LLM
    python Finetune/prepare_training_data.py
    python Finetune/upload_finetune.py
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────────
FINETUNE_DIR = Path(__file__).resolve().parent
OUT_DIR = FINETUNE_DIR / "training_output"
TRAINING_FILE = OUT_DIR / "combined_training.jsonl"

OUT_CHATML = OUT_DIR / "combined_training_local_chatml.jsonl"
OUT_SHAREGPT = OUT_DIR / "combined_training_sharegpt.jsonl"
OUT_ALPACA = OUT_DIR / "combined_training_alpaca.jsonl"
OUT_MANIFEST = OUT_DIR / "finetune_local_manifest.json"

# ── helpers ───────────────────────────────────────────────────────────────────
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str) -> str:
    if text is None:
        return ""
    text = str(text)
    text = CONTROL_CHAR_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_record(row: dict) -> dict | None:
    msgs = row.get("messages", [])
    if not isinstance(msgs, list) or not msgs:
        return None

    system = ""
    chat = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = sanitize_text(m.get("content", ""))
        if not content:
            continue
        if role == "system" and not system:
            system = content
        elif role in ("user", "assistant"):
            chat.append({"role": role, "content": content})

    # Require clean user -> assistant alternation and final assistant answer.
    if not chat or chat[0]["role"] != "user" or chat[-1]["role"] != "assistant":
        return None
    expected = "user"
    for m in chat:
        if m["role"] != expected:
            return None
        expected = "assistant" if expected == "user" else "user"

    return {"system": system, "messages": chat}


def to_alpaca_pairs(normalized: dict) -> list[dict]:
    """Convert one multi-turn conversation into alpaca-style turn pairs."""
    system = normalized.get("system", "")
    messages = normalized["messages"]
    out = []
    for i in range(0, len(messages), 2):
        user_msg = messages[i]["content"]
        asst_msg = messages[i + 1]["content"]
        instruction = user_msg
        if system:
            instruction = f"{system}\n\n{user_msg}"
        out.append(
            {
                "instruction": instruction,
                "input": "",
                "output": asst_msg,
            }
        )
    return out


def main():
    if not TRAINING_FILE.exists():
        sys.exit(
            f"❌ Training file not found: {TRAINING_FILE}\n"
            "Run: python Finetune/prepare_training_data.py"
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    total = 0
    kept = 0
    dropped = 0
    alpaca_rows = 0

    with (
        TRAINING_FILE.open(encoding="utf-8") as fin,
        OUT_CHATML.open("w", encoding="utf-8") as f_chatml,
        OUT_SHAREGPT.open("w", encoding="utf-8") as f_sharegpt,
        OUT_ALPACA.open("w", encoding="utf-8") as f_alpaca,
    ):
        for line in fin:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                dropped += 1
                continue

            norm = normalize_record(row)
            if not norm:
                dropped += 1
                continue

            # 1) ChatML-like local format
            f_chatml.write(json.dumps(norm, ensure_ascii=False) + "\n")

            # 2) ShareGPT format
            conversations = []
            if norm["system"]:
                conversations.append({"from": "system", "value": norm["system"]})
            for m in norm["messages"]:
                conversations.append(
                    {"from": "human" if m["role"] == "user" else "gpt", "value": m["content"]}
                )
            f_sharegpt.write(json.dumps({"conversations": conversations}, ensure_ascii=False) + "\n")

            # 3) Alpaca rows (one per user/assistant pair)
            pairs = to_alpaca_pairs(norm)
            for p in pairs:
                f_alpaca.write(json.dumps(p, ensure_ascii=False) + "\n")
            alpaca_rows += len(pairs)
            kept += 1

    manifest = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source_file": str(TRAINING_FILE),
        "records_total": total,
        "records_kept": kept,
        "records_dropped": dropped,
        "alpaca_rows": alpaca_rows,
        "outputs": {
            "chatml_jsonl": str(OUT_CHATML),
            "sharegpt_jsonl": str(OUT_SHAREGPT),
            "alpaca_jsonl": str(OUT_ALPACA),
        },
        "note": (
            "Local export only. No cloud upload and no changes outside Finetune/. "
            "Use the output file that matches your trainer."
        ),
    }
    with OUT_MANIFEST.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"✅ Source read: {TRAINING_FILE.name}")
    print(f"✅ Kept records: {kept} (dropped: {dropped})")
    print(f"✅ ChatML  → {OUT_CHATML.name}")
    print(f"✅ ShareGPT→ {OUT_SHAREGPT.name}")
    print(f"✅ Alpaca  → {OUT_ALPACA.name} ({alpaca_rows} rows)")
    print(f"✅ Manifest→ {OUT_MANIFEST.name}")
    print("\nDone. No AWS/Bedrock used.")


if __name__ == "__main__":
    main()