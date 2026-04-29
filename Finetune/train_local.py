"""
train_local.py
==============
Local Gemma LoRA fine-tuning script that adapts language capability from:
  - Finetune/dictionary
  - Finetune/knowledge
  - Finetune/msme
  - Finetune/training_output

No AWS/Bedrock. No interactive prompts.
"""

from pathlib import Path
import importlib.metadata
import json
import re
import sys

import torch
from datasets import Dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import SFTConfig, SFTTrainer


FINETUNE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = FINETUNE_DIR / "models" / "msme-gemma-lora"

DICTIONARY_FILE = FINETUNE_DIR / "dictionary" / "kamus_dewan_cleaned.jsonl"
KNOWLEDGE_DIR = FINETUNE_DIR / "knowledge"
MSME_DIR = FINETUNE_DIR / "msme"
TRAINING_OUT_DIR = FINETUNE_DIR / "training_output"

# Preferred Gemma models (require HF access), plus open fallback.
BASE_MODEL = "google/gemma-2-9b-it"
CPU_FALLBACK_MODEL = "google/gemma-2-2b-it"
OPEN_FALLBACK_MODEL = "Qwen/Qwen2.5-3B-Instruct"
SYSTEM_PROMPT = (
    "Anda adalah pembantu AI pakar MSME Malaysia, fasih Bahasa Melayu dan Inggeris. "
    "Jawab tepat, praktikal, dan jelas."
)

MAX_SEQ_LENGTH = 1024
NUM_EPOCHS = 2
LEARNING_RATE = 2e-4
PER_DEVICE_BATCH_SIZE = 2
GRADIENT_ACCUMULATION_STEPS = 8
WARMUP_RATIO = 0.03
WEIGHT_DECAY = 0.01
LOGGING_STEPS = 10
SAVE_STEPS = 200

CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str) -> str:
    if text is None:
        return ""
    text = CONTROL_CHAR_RE.sub(" ", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def add_chat_record(records: list[dict], user: str, assistant: str, system: str = SYSTEM_PROMPT):
    user = sanitize_text(user)
    assistant = sanitize_text(assistant)
    if not user or not assistant:
        return
    records.append(
        {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ]
        }
    )


def load_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def build_records_from_training_output(records: list[dict]):
    combined = TRAINING_OUT_DIR / "combined_training.jsonl"
    if combined.exists():
        for row in load_jsonl(combined):
            msgs = row.get("messages", [])
            if not isinstance(msgs, list):
                continue
            clean_msgs = []
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                role = m.get("role")
                content = sanitize_text(m.get("content", ""))
                if role in ("system", "user", "assistant") and content:
                    clean_msgs.append({"role": role, "content": content})
            if len(clean_msgs) >= 3:
                records.append({"messages": clean_msgs})

    sharegpt = TRAINING_OUT_DIR / "combined_training_sharegpt.jsonl"
    if sharegpt.exists():
        for row in load_jsonl(sharegpt):
            conv = row.get("conversations", [])
            if not isinstance(conv, list):
                continue
            system = SYSTEM_PROMPT
            pending_user = None
            for turn in conv:
                if not isinstance(turn, dict):
                    continue
                src = str(turn.get("from", "")).strip().lower()
                val = sanitize_text(turn.get("value", ""))
                if not val:
                    continue
                if src == "system":
                    system = val
                elif src == "human":
                    pending_user = val
                elif src == "gpt" and pending_user:
                    add_chat_record(records, pending_user, val, system=system)
                    pending_user = None


def build_records_from_dictionary(records: list[dict]):
    for row in load_jsonl(DICTIONARY_FILE):
        word = sanitize_text(row.get("word", ""))
        defs = row.get("definitions", [])
        if not word or not isinstance(defs, list) or not defs:
            continue
        defs_text = "; ".join(sanitize_text(d) for d in defs[:3] if sanitize_text(d))
        if defs_text:
            add_chat_record(
                records,
                f"Apakah maksud perkataan '{word}' dalam Bahasa Malaysia?",
                f"Maksud '{word}' ialah: {defs_text}",
            )


def build_records_from_knowledge(records: list[dict]):
    for path in sorted(KNOWLEDGE_DIR.glob("*.jsonl")):
        for row in load_jsonl(path):
            if {"agency", "filename", "text"} <= set(row.keys()):
                agency = sanitize_text(row.get("agency", "agensi"))
                filename = sanitize_text(row.get("filename", "dokumen"))
                text = sanitize_text(row.get("text", ""))[:2200]
                if text:
                    add_chat_record(
                        records,
                        f"Ringkaskan dokumen '{filename}' daripada {agency}.",
                        text,
                    )
            elif {"title", "content"} <= set(row.keys()):
                title = sanitize_text(row.get("title", row.get("source_file", "dokumen")))
                content = sanitize_text(row.get("content", ""))[:2200]
                if content:
                    add_chat_record(
                        records,
                        f"Terangkan kandungan dokumen '{title}'.",
                        content,
                    )


def build_records_from_msme(records: list[dict]):
    for path in sorted(MSME_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if isinstance(data, dict) and isinstance(data.get("accounts"), list):
            for acc in data["accounts"]:
                bank = sanitize_text(acc.get("bank_name", "bank"))
                name = sanitize_text(acc.get("account_name", "akaun"))
                best = sanitize_text(acc.get("best_for", ""))
                if bank and name and best:
                    add_chat_record(
                        records,
                        f"Untuk siapa akaun {name} di {bank} sesuai?",
                        f"Akaun {name} di {bank} sesuai untuk: {best}",
                    )
        elif isinstance(data, dict):
            for topic, value in data.items():
                if not isinstance(value, dict):
                    continue
                title = sanitize_text(value.get("title", topic.replace("_", " ").title()))
                steps = value.get("steps", [])
                if isinstance(steps, list) and steps:
                    step_lines = []
                    for s in steps[:10]:
                        if isinstance(s, dict):
                            step_name = sanitize_text(s.get("step", s.get("title", "")))
                            desc = sanitize_text(s.get("description", s.get("details", "")))
                            line = f"{step_name}: {desc}".strip(": ")
                            if line:
                                step_lines.append(line)
                    if step_lines:
                        add_chat_record(
                            records,
                            f"Bagaimana proses untuk {title}?",
                            " ; ".join(step_lines),
                        )


def records_to_text_dataset(records: list[dict], tokenizer) -> Dataset:
    rows = []
    for row in records:
        msgs = row.get("messages", [])
        if not isinstance(msgs, list) or not msgs:
            continue
        try:
            text = tokenizer.apply_chat_template(msgs, tokenize=False)
        except Exception:
            continue
        text = sanitize_text(text)
        if text:
            rows.append({"text": text})

    if not rows:
        return Dataset.from_list([{"text": ""}]).filter(lambda x: bool(x["text"]))
    return Dataset.from_list(rows)


def pick_model_name(has_cuda: bool) -> str:
    preferred = BASE_MODEL if has_cuda else CPU_FALLBACK_MODEL
    return preferred


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_records: list[dict] = []
    build_records_from_training_output(all_records)
    build_records_from_dictionary(all_records)
    build_records_from_knowledge(all_records)
    build_records_from_msme(all_records)

    if not all_records:
        sys.exit("No training records found from Finetune sources.")

    has_cuda = torch.cuda.is_available()
    try:
        _ = importlib.metadata.version("bitsandbytes")
        has_bnb = True
    except importlib.metadata.PackageNotFoundError:
        has_bnb = False

    use_4bit = has_cuda and has_bnb
    model_name = pick_model_name(has_cuda)

    if not has_cuda:
        print("⚠️ CUDA unavailable; using CPU fallback Gemma model.")
    elif not has_bnb:
        print("⚠️ bitsandbytes not found; running without 4-bit quantization.")

    print(f"Loading tokenizer/model: {model_name}")
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True)
    except Exception as exc:
        msg = str(exc).lower()
        if "gated repo" in msg or "401" in msg or "access to model" in msg:
            print("⚠️ Gemma model is gated or unavailable in this environment.")
            model_name = OPEN_FALLBACK_MODEL
            print(f"↪ Falling back to open model: {model_name}")
            tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True)
        else:
            raise
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    if use_4bit:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            quantization_config=bnb_config,
            device_map="auto",
            dtype=torch.bfloat16,
        )
    else:
        dtype = torch.bfloat16 if has_cuda else torch.float32
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            device_map="auto",
            dtype=dtype,
        )

    model.config.use_cache = False

    train_ds = records_to_text_dataset(all_records, tokenizer)
    if len(train_ds) == 0:
        sys.exit("No valid text rows after preprocessing.")
    print(f"Prepared {len(train_ds)} training rows from Finetune sources.")

    peft_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )

    per_device_bs = PER_DEVICE_BATCH_SIZE if has_cuda else 1
    grad_accum = GRADIENT_ACCUMULATION_STEPS if has_cuda else 1

    args = SFTConfig(
        output_dir=str(OUTPUT_DIR),
        num_train_epochs=NUM_EPOCHS,
        learning_rate=LEARNING_RATE,
        per_device_train_batch_size=per_device_bs,
        gradient_accumulation_steps=grad_accum,
        warmup_ratio=WARMUP_RATIO,
        weight_decay=WEIGHT_DECAY,
        logging_steps=LOGGING_STEPS,
        save_steps=SAVE_STEPS,
        save_total_limit=2,
        bf16=has_cuda,
        fp16=False,
        optim="paged_adamw_8bit" if use_4bit else "adamw_torch",
        lr_scheduler_type="cosine",
        report_to="none",
        use_cpu=not has_cuda,
        dataset_text_field="text",
        max_length=MAX_SEQ_LENGTH,
    )

    trainer = SFTTrainer(
        model=model,
        train_dataset=train_ds,
        processing_class=tokenizer,
        peft_config=peft_config,
        args=args,
    )

    print(f"Starting Gemma LoRA training on {len(train_ds)} rows...")
    trainer.train()

    print(f"Saving LoRA adapter to: {OUTPUT_DIR}")
    trainer.model.save_pretrained(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    print("Done.")
