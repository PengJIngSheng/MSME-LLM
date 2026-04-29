"""
process_dictionary.py
=====================
Extracts and cleans Kamus Dewan Bahasa Edisi Keempat (PDF) into a
structured JSONL file ready for training.

USAGE — run from ANY directory:
    cd ~/MSME-LLM
    python Finetune/process_dictionary.py

OUTPUT:
    Finetune/dictionary/kamus_dewan_cleaned.jsonl
    Finetune/dictionary/kamus_dewan_cleaned.json

INSTALL DEPS (once):
    pip install pdfplumber tqdm
"""

import json
import re
import sys
from pathlib import Path

# ── dependency check ──────────────────────────────────────────────────────────
try:
    import pdfplumber
except ImportError:
    sys.exit("❌  Missing: pip install pdfplumber tqdm")

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw):
        return it

# ── paths — always relative to THIS file, never cwd ──────────────────────────
DICT_DIR  = Path(__file__).resolve().parent / "dictionary"
OUT_JSONL = DICT_DIR / "kamus_dewan_cleaned.jsonl"
OUT_JSON  = DICT_DIR / "kamus_dewan_cleaned.json"

def pick_pdf_path() -> Path:
    pdfs = sorted({*DICT_DIR.glob("*.pdf"), *DICT_DIR.glob("*.PDF")})
    if not pdfs:
        found = sorted(p.name for p in DICT_DIR.glob("*")) if DICT_DIR.exists() else []
        details = ", ".join(found[:10]) if found else "no files"
        sys.exit(f"❌  No PDF found in {DICT_DIR} (found: {details})")
    if len(pdfs) > 1:
        print(f"⚠️  Multiple PDFs found; using first alphabetically: {pdfs[0].name}")
    return pdfs[0]


# ── Kamus Dewan entry parser ──────────────────────────────────────────────────
# The dictionary uses a compact format, e.g.:
#   abad  jangka masa seratus tahun ...
#   abadi Ar 1. ada permulaan ... 2. wujud ...
#
# We detect headwords as the first token(s) that are all lowercase Malay chars
# followed by an optional language-source marker (Ar, Ing, Cn …) and the rest.

HEADWORD_RE = re.compile(
    r"^([a-zA-ZÀ-öø-ÿ\-]+(?:\s+[IVX]+)?)"   # word (may have Roman numeral suffix)
    r"(?:\s+(Ar|Ing|Cn|Sk|Jw|Jav|Pt|Hol|Jap|Per|Tamil|Arab|Mal)(?=\s))?"  # language tag
    r"\s+(.+)$",                               # definition body
    re.DOTALL | re.UNICODE,
)

# strip abbreviation noise: dlm, drpd, dsb, dll, sr, utk, etc.
def clean_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def parse_entry(raw: str) -> dict | None:
    raw = clean_text(raw)
    if len(raw) < 6:
        return None

    m = HEADWORD_RE.match(raw)
    if not m:
        return None

    headword = m.group(1).strip().lower()
    lang_src  = m.group(2) or ""
    body      = m.group(3).strip()

    # Split numbered senses: "1. def one; 2. def two"
    senses = re.split(r"\s+\d+\.\s+", body)
    senses = [s.strip().rstrip(";, ") for s in senses if len(s.strip()) > 2]

    return {
        "word":        headword,
        "lang_source": lang_src,
        "definitions": senses,
    }


# ── multi-line entry accumulator ──────────────────────────────────────────────
# Dictionary entries often wrap across lines. We join continuation lines
# (those that don't start a new headword) to the previous entry.

def is_new_entry(line: str) -> bool:
    """True if the line looks like the start of a new dictionary entry."""
    # Must start with a lowercase Malay word (1-30 chars), NOT a digit or space
    return bool(re.match(r"^[a-zA-Z][a-zA-ZÀ-öø-ÿ\-]{0,29}\s", line))

def group_lines(lines: list[str]) -> list[str]:
    entries = []
    buf = ""
    for line in lines:
        line = line.strip()
        if not line:
            if buf:
                entries.append(buf)
                buf = ""
            continue
        if is_new_entry(line):
            if buf:
                entries.append(buf)
            buf = line
        else:
            buf = (buf + " " + line).strip() if buf else line
    if buf:
        entries.append(buf)
    return entries


# ── main extraction ───────────────────────────────────────────────────────────
def extract(pdf_path: Path) -> list[dict]:
    seen    = set()
    entries = []
    errors  = 0

    with pdfplumber.open(pdf_path) as pdf:
        pages = pdf.pages
        print(
            f"📖  Reading: {pdf_path.name}  "
            f"({pdf_path.stat().st_size // 1_048_576} MB, {len(pages)} pages)"
        )
        for page in tqdm(pages, desc="Pages", unit="pg", ncols=70):
            try:
                raw_text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            except Exception:
                errors += 1
                continue

            lines  = raw_text.splitlines()
            chunks = group_lines(lines)

            for chunk in chunks:
                entry = parse_entry(chunk)
                if entry and entry["word"] not in seen:
                    seen.add(entry["word"])
                    entries.append(entry)

    print(f"   Pages with errors : {errors}")
    print(f"   Unique entries     : {len(entries)}")
    return entries


def main():
    DICT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = pick_pdf_path()
    entries = extract(pdf_path)

    # JSONL — one JSON object per line
    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"✅  JSONL → {OUT_JSONL}  ({OUT_JSONL.stat().st_size // 1024} KB)")

    # Pretty JSON for inspection
    with OUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print(f"✅  JSON  → {OUT_JSON}")


if __name__ == "__main__":
    main()