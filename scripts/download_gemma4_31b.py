#!/usr/bin/env python3
"""
Download Gemma 4 31B in a way that is safe for Ollama.

Recommended:
  python scripts/download_gemma4_31b.py --mode ollama

Raw Hugging Face Safetensors from google/gemma-4-31B are not directly usable by
Ollama. Ollama needs a registry model or a GGUF file referenced by a Modelfile.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


DEFAULT_OLLAMA_MODEL = "gemma4:31b"
DEFAULT_HF_REPO = "google/gemma-4-31B"


def run(cmd: list[str], dry_run: bool = False) -> None:
    print("+ " + " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)


def require_ollama() -> None:
    if shutil.which("ollama") is None:
        raise SystemExit("Ollama CLI was not found in PATH. Install Ollama first, then retry.")


def pull_ollama(model: str, dry_run: bool) -> None:
    if not dry_run:
        require_ollama()
    run(["ollama", "pull", model], dry_run=dry_run)
    print(f"\nDone. Use this model name in config/server.ubuntu.yaml: {model}")


def download_hf_snapshot(repo_id: str, out_dir: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"+ snapshot_download(repo_id={repo_id!r}, local_dir={str(out_dir)!r})")
        return
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=repo_id,
        local_dir=str(out_dir),
        local_dir_use_symlinks=False,
        resume_download=True,
    )
    print(
        "\nDownloaded the raw Hugging Face snapshot. This is useful for archival or "
        "Transformers, but it is not directly Ollama-compatible. Convert it to GGUF "
        "with llama.cpp first, or use --mode ollama / --mode gguf."
    )


def download_gguf(repo_id: str, filename: str, out_dir: Path, ollama_name: str, create: bool, dry_run: bool) -> None:
    if not repo_id or not filename:
        raise SystemExit("--gguf-repo and --gguf-file are required for --mode gguf.")

    gguf_path = out_dir / filename
    modelfile_path = out_dir / f"Modelfile.{ollama_name.replace(':', '-')}"

    if dry_run:
        print(f"+ hf_hub_download(repo_id={repo_id!r}, filename={filename!r}, local_dir={str(out_dir)!r})")
    else:
        from huggingface_hub import hf_hub_download

        downloaded = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=str(out_dir),
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        gguf_path = Path(downloaded)

    modelfile_text = (
        f"FROM {gguf_path.as_posix()}\n"
        "PARAMETER temperature 0.6\n"
        "PARAMETER top_p 0.95\n"
        "PARAMETER num_ctx 32768\n"
    )
    print(f"+ write {modelfile_path}")
    if not dry_run:
        modelfile_path.parent.mkdir(parents=True, exist_ok=True)
        modelfile_path.write_text(modelfile_text, encoding="utf-8")

    if create:
        if not dry_run:
            require_ollama()
        run(["ollama", "create", ollama_name, "-f", str(modelfile_path)], dry_run=dry_run)

    print(f"\nGGUF path: {gguf_path}")
    print(f"Modelfile: {modelfile_path}")
    print(f"Ollama model name: {ollama_name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Gemma 4 31B for Ollama or local archival.")
    parser.add_argument("--mode", choices=["ollama", "hf", "gguf"], default="ollama")
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--hf-repo", default=DEFAULT_HF_REPO)
    parser.add_argument("--out-dir", default="models/gemma4-31b")
    parser.add_argument("--gguf-repo", default="")
    parser.add_argument("--gguf-file", default="")
    parser.add_argument("--create", action="store_true", help="Run ollama create after downloading a GGUF file.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "ollama":
        pull_ollama(args.ollama_model, args.dry_run)
    elif args.mode == "hf":
        download_hf_snapshot(args.hf_repo, out_dir, args.dry_run)
    else:
        download_gguf(
            repo_id=args.gguf_repo,
            filename=args.gguf_file,
            out_dir=out_dir,
            ollama_name=args.ollama_model,
            create=args.create,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
