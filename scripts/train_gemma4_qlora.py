#!/usr/bin/env python3
"""
QLoRA fine-tuning script for a HuggingFace Gemma-compatible causal LM.

Important:
  - This trains a LoRA adapter from a HuggingFace base model.
  - It does not fine-tune an Ollama Q4/GGUF model directly.
  - After training, keep the adapter for HF/vLLM inference or merge/export later.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch.nn.utils.rnn import pad_sequence

try:
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        Trainer,
        TrainingArguments,
    )
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing fine-tuning dependencies. Install them with:\n"
        "  python -m pip install -r requirements-finetune.txt\n\n"
        f"Original import error: {exc}"
    )


@dataclass
class ChatCollator:
    pad_token_id: int

    def __call__(self, features: list[dict[str, Any]]) -> dict[str, torch.Tensor]:
        input_ids = [torch.tensor(f["input_ids"], dtype=torch.long) for f in features]
        attention_mask = [torch.tensor(f["attention_mask"], dtype=torch.long) for f in features]
        labels = [torch.tensor(f["labels"], dtype=torch.long) for f in features]

        return {
            "input_ids": pad_sequence(input_ids, batch_first=True, padding_value=self.pad_token_id),
            "attention_mask": pad_sequence(attention_mask, batch_first=True, padding_value=0),
            "labels": pad_sequence(labels, batch_first=True, padding_value=-100),
        }


def fallback_chat_template(messages: list[dict[str, str]]) -> str:
    chunks = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        chunks.append(f"<start_of_turn>{role}\n{content}<end_of_turn>\n")
    return "".join(chunks)


def format_chat(tokenizer: AutoTokenizer, messages: list[dict[str, str]], add_generation_prompt: bool = False) -> str:
    if getattr(tokenizer, "chat_template", None):
        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=add_generation_prompt,
        )
    text = fallback_chat_template(messages)
    if add_generation_prompt:
        text += "<start_of_turn>model\n"
    return text


def build_tokenizer_fn(tokenizer: AutoTokenizer, max_seq_length: int):
    def tokenize_row(row: dict[str, Any]) -> dict[str, Any]:
        messages = row["messages"]
        if isinstance(messages, str):
            messages = json.loads(messages)

        prompt_messages = [m for m in messages if m.get("role") != "assistant"]
        full_text = format_chat(tokenizer, messages, add_generation_prompt=False)
        prompt_text = format_chat(tokenizer, prompt_messages, add_generation_prompt=True)

        full = tokenizer(full_text, truncation=True, max_length=max_seq_length, add_special_tokens=False)
        prompt = tokenizer(prompt_text, truncation=True, max_length=max_seq_length, add_special_tokens=False)

        labels = list(full["input_ids"])
        prompt_len = min(len(prompt["input_ids"]), len(labels))
        labels[:prompt_len] = [-100] * prompt_len

        return {
            "input_ids": full["input_ids"],
            "attention_mask": full["attention_mask"],
            "labels": labels,
        }

    return tokenize_row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-model", required=True, help="HuggingFace model id or local base model path.")
    parser.add_argument("--dataset", default="data/finetune/gemma4_mof_sft.jsonl")
    parser.add_argument("--output-dir", default="models/lora/gemma4-msme")
    parser.add_argument("--max-seq-length", type=int, default=4096)
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--save-steps", type=int, default=100)
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--max-steps", type=int, default=-1)
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise SystemExit(
            f"Dataset not found: {dataset_path}\n"
            "Run scripts/prepare_gemma4_finetune_data.py first."
        )

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=bnb_config,
        device_map="auto",
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    raw_dataset = load_dataset("json", data_files=str(dataset_path), split="train")
    tokenized = raw_dataset.map(
        build_tokenizer_fn(tokenizer, args.max_seq_length),
        remove_columns=raw_dataset.column_names,
        desc="Tokenizing",
    )

    training_args = TrainingArguments(
        output_dir=args.output_dir,
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.learning_rate,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        logging_steps=args.logging_steps,
        save_steps=args.save_steps,
        save_total_limit=3,
        bf16=torch.cuda.is_available(),
        fp16=False,
        optim="paged_adamw_8bit",
        gradient_checkpointing=True,
        report_to="none",
        remove_unused_columns=False,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized,
        data_collator=ChatCollator(tokenizer.pad_token_id),
    )
    trainer.train()
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print(f"Saved LoRA adapter to {args.output_dir}")


if __name__ == "__main__":
    main()
