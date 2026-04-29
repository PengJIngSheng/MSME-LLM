"""
prepare_training_data.py
========================
Merges all Finetune/ data sources into a single training JSONL.

Sources (with their ACTUAL field structures):
  knowledge/final_ai_training_data.cleaned.jsonl  → {agency, filename, text, dictionary_tags}
  knowledge/knowledge_base.cleaned.jsonl           → {source_file, source_type, title, agency, content, tags}
  msme/bank.json                                   → {dataset, accounts:[{bank_name, account_name, ...}]}
  msme/msmelatest.json                             → {startup_roadmap, ssm_registration_sole_prop, ...}
  dictionary/kamus_dewan_cleaned.jsonl             → {word, lang_source, definitions}  ← from process_dictionary.py

OUTPUT:
  Finetune/training_output/combined_training.jsonl
  Finetune/training_output/combined_training_stats.json

USAGE:
    cd ~/MSME-LLM
    python Finetune/process_dictionary.py          # first time only
    python Finetune/prepare_training_data.py

INSTALL DEPS (once):
    pip install tqdm
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw):
        return it

# ── paths ─────────────────────────────────────────────────────────────────────
FINETUNE_DIR = Path(__file__).resolve().parent
OUT_DIR      = FINETUNE_DIR / "training_output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SOURCES = {
    "knowledge_training": FINETUNE_DIR / "knowledge" / "final_ai_training_data.cleaned.jsonl",
    "knowledge_base":     FINETUNE_DIR / "knowledge" / "knowledge_base.cleaned.jsonl",
    "bank":               FINETUNE_DIR / "msme" / "bank.json",
    "msme":               FINETUNE_DIR / "msme" / "msmelatest.json",
    "dictionary":         FINETUNE_DIR / "dictionary" / "kamus_dewan_cleaned.jsonl",
}

OUT_JSONL = OUT_DIR / "combined_training.jsonl"
OUT_STATS = OUT_DIR / "combined_training_stats.json"

SYSTEM_PROMPT = (
    "Anda adalah pembantu AI yang pakar dalam perniagaan MSME (Micro, Small and Medium "
    "Enterprises) Malaysia dan mahir dalam Bahasa Malaysia serta Bahasa Inggeris. "
    "Jawab dengan tepat, berguna, dan dalam bahasa yang jelas."
)

# ── helpers ───────────────────────────────────────────────────────────────────
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str) -> str:
    if text is None:
        return ""
    text = str(text)
    text = CONTROL_CHAR_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  [WARN] {path.name}:{i} – {e}")
    return records

def load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)

def make_record(user: str, assistant: str) -> dict:
    user = sanitize_text(user)
    assistant = sanitize_text(assistant)
    if not user or not assistant:
        return None
    return {"messages": [
        {"role": "system",    "content": SYSTEM_PROMPT},
        {"role": "user",      "content": user},
        {"role": "assistant", "content": assistant},
    ]}

def truncate(text: str, max_chars: int = 2000) -> str:
    """Keep the first max_chars chars to avoid oversized examples."""
    text = sanitize_text(text)
    return text[:max_chars].rsplit(" ", 1)[0] if len(text) > max_chars else text


def discover_sources() -> list[tuple[str, Path, str]]:
    """
    Auto-discover supported sources inside Finetune/{knowledge,msme,dictionary}.
    Returns tuples of (stats_key, path, converter_name).
    """
    discovered = []

    knowledge_dir = FINETUNE_DIR / "knowledge"
    if knowledge_dir.exists():
        for path in sorted(knowledge_dir.glob("*.jsonl")):
            converter = detect_jsonl_converter(path)
            if converter:
                discovered.append((f"{converter}:{path.name}", path, converter))

    msme_dir = FINETUNE_DIR / "msme"
    if msme_dir.exists():
        for path in sorted(msme_dir.glob("*.json")):
            converter = detect_json_converter(path)
            if converter:
                discovered.append((f"{converter}:{path.name}", path, converter))

    dict_dir = FINETUNE_DIR / "dictionary"
    preferred = dict_dir / "kamus_dewan_cleaned.jsonl"
    if preferred.exists():
        discovered.append((f"dictionary:{preferred.name}", preferred, "dictionary"))
    elif dict_dir.exists():
        for path in sorted(dict_dir.glob("*.jsonl")):
            if detect_jsonl_converter(path) == "dictionary":
                discovered.append((f"dictionary:{path.name}", path, "dictionary"))

    return discovered


def detect_jsonl_converter(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    continue
                keys = set(obj.keys())
                if {"agency", "filename", "text"} <= keys:
                    return "knowledge_training"
                if {"source_file", "content"} <= keys:
                    return "knowledge_base"
                if {"word", "definitions"} <= keys:
                    return "dictionary"
                return None
    except Exception:
        return None
    return None


def detect_json_converter(path: Path) -> str | None:
    try:
        obj = load_json(path)
    except Exception:
        return None
    if isinstance(obj, dict) and isinstance(obj.get("accounts"), list):
        return "bank"
    if isinstance(obj, dict):
        return "msme"
    return None


# ── converters ────────────────────────────────────────────────────────────────

def convert_knowledge_training(path: Path) -> list[dict]:
    """
    Format: {agency, filename, text, dictionary_tags}
    text is the raw document content (often multi-page).
    We create Q&A pairs asking the model to summarise or explain each doc.
    """
    records = load_jsonl(path)
    out = []
    for r in records:
        agency   = r.get("agency", "")
        filename = r.get("filename", "")
        text     = truncate(r.get("text", ""))
        tags     = r.get("dictionary_tags", [])

        if not text:
            continue

        # Pair 1 – summarise document
        rec = make_record(
            f"Ringkaskan kandungan dokumen '{filename}' daripada {agency}.",
            f"Dokumen ini daripada {agency} bertajuk '{filename}'.\n\n{text}"
        )
        if rec:
            out.append(rec)

        # Pair 2 – tag-based Q&A (if tags exist)
        if tags:
            tag_str = ", ".join(str(t) for t in tags[:10])
            rec = make_record(
                f"Apakah topik-topik utama dalam dokumen '{filename}'?",
                f"Topik utama dalam dokumen tersebut ialah: {tag_str}."
            )
            if rec:
                out.append(rec)

    return out


def convert_knowledge_base(path: Path) -> list[dict]:
    """
    Format: {source_file, source_type, title, agency, content, tags}
    content is long document text.
    """
    records = load_jsonl(path)
    out = []
    for r in records:
        title   = r.get("title", r.get("source_file", "dokumen"))
        agency  = r.get("agency", "")
        content = truncate(r.get("content", ""))
        tags    = r.get("tags", [])

        if not content:
            continue

        agency_note = f" daripada {agency}" if agency else ""

        rec = make_record(
            f"Terangkan kandungan dokumen '{title}'{agency_note}.",
            f"Dokumen '{title}'{agency_note} mengandungi maklumat berikut:\n\n{content}"
        )
        if rec:
            out.append(rec)

        if tags:
            tag_str = ", ".join(str(t) for t in tags[:10])
            rec = make_record(
                f"Apakah kata kunci untuk dokumen '{title}'?",
                f"Kata kunci bagi '{title}': {tag_str}."
            )
            if rec:
                out.append(rec)

    return out


def convert_bank(path: Path) -> list[dict]:
    """
    Format: {dataset, last_reviewed, source, documents_required_general, accounts:[...]}
    Each account: {id, bank_name, account_name, best_for, key_features:{...}, documents_required:{...}}
    """
    data     = load_json(path)
    accounts = data.get("accounts", [])
    gen_docs = data.get("documents_required_general", {})
    out      = []

    # General documents Q&A
    if gen_docs:
        rec = make_record(
            "Apakah dokumen am yang diperlukan untuk membuka akaun perniagaan SME di Malaysia?",
            "Dokumen am yang diperlukan:\n" +
            json.dumps(gen_docs, ensure_ascii=False, indent=2)[:1500]
        )
        if rec:
            out.append(rec)

    for acc in accounts:
        bank    = acc.get("bank_name", "")
        name    = acc.get("account_name", "")
        best    = acc.get("best_for", "")
        feats   = acc.get("key_features", {})
        docs    = acc.get("documents_required", {})

        # Feature summary
        feat_lines = []
        for k, v in feats.items():
            if isinstance(v, list):
                feat_lines.append(f"• {k}: " + "; ".join(str(i) for i in v))
            else:
                feat_lines.append(f"• {k}: {v}")
        feat_text = "\n".join(feat_lines)

        # Q1: What is this account?
        rec = make_record(
            f"Apakah {name} daripada {bank}?",
            f"{name} ({bank}) adalah sesuai untuk: {best}\n\nCiri-ciri utama:\n{feat_text}"
        )
        if rec:
            out.append(rec)

        # Q2: Specific feature questions
        deposit = feats.get("initial_deposit", "")
        if deposit:
            rec = make_record(
                f"Berapakah deposit awal untuk {name} di {bank}?",
                f"Deposit awal untuk {name} di {bank} ialah {deposit}."
            )
            if rec:
                out.append(rec)

        # Q3: Documents needed
        if docs:
            steps = docs.get("account_opening_steps", [])
            if steps:
                steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
                rec = make_record(
                    f"Bagaimana cara membuka {name} di {bank}?",
                    f"Langkah-langkah membuka {name} di {bank}:\n{steps_text}"
                )
                if rec:
                    out.append(rec)

        # Q4: Best for
        if best:
            rec = make_record(
                f"Untuk siapakah {name} di {bank} paling sesuai?",
                f"{name} di {bank} paling sesuai untuk: {best}"
            )
            if rec:
                out.append(rec)

    return out


def convert_msme(path: Path) -> list[dict]:
    """
    Format: top-level dict of topic dicts.
    Topics: startup_roadmap, financing_guide, ssm_registration_sole_prop,
            employer_compliance_malaysia, hrd_corp_registration_guide,
            myipo_patent_process, myipo_trademark_master,
            copyright_voluntary_notification_malaysia
    Each topic has a 'title' and 'steps' or other structured fields.
    """
    data = load_json(path)
    out  = []

    TOPIC_QUESTIONS = {
        "startup_roadmap":                    "Apakah langkah-langkah memulakan perniagaan SME di Malaysia?",
        "financing_guide":                    "Bagaimana cara mendapatkan pembiayaan untuk SME di Malaysia?",
        "ssm_registration_sole_prop":         "Bagaimana cara mendaftar perniagaan milikan tunggal dengan SSM?",
        "employer_compliance_malaysia":       "Apakah tanggungjawab pematuhan majikan di Malaysia?",
        "hrd_corp_registration_guide":        "Bagaimana cara mendaftar dengan HRD Corp?",
        "myipo_patent_process":               "Bagaimana cara memohon paten melalui MyIPO?",
        "myipo_trademark_master":             "Bagaimana cara mendaftarkan tanda dagangan melalui MyIPO?",
        "copyright_voluntary_notification_malaysia": "Bagaimana cara membuat notifikasi hak cipta sukarela di Malaysia?",
    }

    for key, topic_data in data.items():
        if not isinstance(topic_data, dict):
            continue

        title    = topic_data.get("title", key.replace("_", " ").title())
        question = TOPIC_QUESTIONS.get(key, f"Terangkan tentang {title}.")

        # Build answer from steps or fields
        steps = topic_data.get("steps", [])
        if steps:
            answer_lines = [f"{title}:"]
            for s in steps:
                if isinstance(s, dict):
                    step_id   = s.get("id", "")
                    step_name = s.get("step", s.get("title", ""))
                    step_desc = s.get("description", s.get("details", ""))
                    if step_name:
                        answer_lines.append(f"\n{step_id}. {step_name}")
                    if step_desc:
                        answer_lines.append(f"   {step_desc}")
                else:
                    answer_lines.append(f"• {s}")
            answer = "\n".join(answer_lines)
        else:
            # Flatten all other fields
            parts = [f"{title}:"]
            for k, v in topic_data.items():
                if k == "title":
                    continue
                if isinstance(v, list):
                    parts.append(f"\n{k.replace('_',' ').title()}:")
                    for item in v:
                        if isinstance(item, dict):
                            parts.append("  " + "; ".join(f"{ik}: {iv}" for ik, iv in item.items() if iv))
                        else:
                            parts.append(f"  • {item}")
                elif isinstance(v, dict):
                    parts.append(f"\n{k.replace('_',' ').title()}: " +
                                 "; ".join(f"{ik}: {iv}" for ik, iv in v.items() if iv))
                else:
                    parts.append(f"{k.replace('_',' ').title()}: {v}")
            answer = "\n".join(parts)

        answer = truncate(answer, 2500)
        rec = make_record(question, answer)
        if rec:
            out.append(rec)

        # Extra: fee-specific Q&A for SSM
        if key == "ssm_registration_sole_prop":
            fee_struct = topic_data.get("fee_structure", [])
            if fee_struct:
                fee_text = "\n".join(
                    f"• {f['type']}: RM{f['fee_annually']}/tahun — {f.get('note','')}"
                    for f in fee_struct if isinstance(f, dict)
                )
                rec = make_record(
                    "Berapakah yuran pendaftaran SSM untuk milikan tunggal?",
                    f"Yuran pendaftaran SSM (EzBiz):\n{fee_text}"
                )
                if rec:
                    out.append(rec)

        # Extra: eligibility Q&A
        eligibility = topic_data.get("eligibility", topic_data.get("who_can_apply", ""))
        if eligibility:
            rec = make_record(
                f"Siapakah yang layak untuk {title}?",
                f"Kelayakan untuk {title}: {eligibility}"
            )
            if rec:
                out.append(rec)

    return out


def convert_dictionary(path: Path) -> list[dict]:
    """
    Format: {word, lang_source, definitions:[str,...]}
    Create vocabulary Q&A pairs.
    """
    records = load_jsonl(path)
    out     = []
    for r in records:
        word  = r.get("word", "").strip()
        defs  = r.get("definitions", [])
        lang  = r.get("lang_source", "")

        if not word or not defs:
            continue

        lang_note = f" (dari bahasa {lang})" if lang else ""
        def_text  = "; ".join(defs[:3])  # max 3 senses

        rec = make_record(
            f"Apakah maksud perkataan '{word}'{lang_note} dalam Bahasa Malaysia?",
            f"Maksud '{word}'{lang_note}: {def_text}"
        )
        if rec:
            out.append(rec)

    return out


# ── validation ────────────────────────────────────────────────────────────────
def validate(r: dict) -> bool:
    msgs = r.get("messages", [])
    if not isinstance(msgs, list) or len(msgs) < 2:
        return False
    for m in msgs:
        if not isinstance(m, dict):
            return False
        if m.get("role") not in ("system", "user", "assistant"):
            return False
        if not str(m.get("content", "")).strip():
            return False
    return True


CONVERTERS = {
    "knowledge_training": convert_knowledge_training,
    "knowledge_base":     convert_knowledge_base,
    "bank":               convert_bank,
    "msme":               convert_msme,
    "dictionary":         convert_dictionary,
}

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    all_records = []
    stats = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sources": {},
    }

    sources = discover_sources()
    if not sources:
        print("[ERROR] No supported source files found in Finetune/{knowledge,msme,dictionary}")
        print("Expected JSONL in knowledge/, JSON in msme/, and optional dictionary JSONL in dictionary/")
        return

    for stats_key, path, name in sources:
        if not path.exists():
            print(f"[SKIP] {name}: not found → {path}")
            stats["sources"][stats_key] = {"status": "missing", "count": 0}
            continue

        print(f"[LOAD] {name}: {path.name}")
        try:
            records = CONVERTERS[name](path)
        except Exception as exc:
            import traceback
            print(f"  [ERROR] {exc}")
            traceback.print_exc()
            stats["sources"][stats_key] = {"status": "error", "error": str(exc), "count": 0}
            continue

        valid   = [r for r in records if validate(r)]
        dropped = len(records) - len(valid)
        if dropped:
            print(f"  [WARN] {dropped} invalid records dropped")

        all_records.extend(valid)
        stats["sources"][stats_key] = {"status": "ok", "count": len(valid)}
        print(f"  → {len(valid)} training records")

    # Deduplicate on first user message
    seen, deduped = set(), []
    for r in all_records:
        user_msgs = [m["content"] for m in r["messages"] if m["role"] == "user"]
        key = user_msgs[0][:100] if user_msgs else str(id(r))
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    removed = len(all_records) - len(deduped)
    print(f"\nDeduplicated: removed {removed} duplicates")
    print(f"Final records: {len(deduped)}")

    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for r in deduped:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"✅  Saved → {OUT_JSONL}")

    stats["total"] = len(deduped)
    stats["duplicates_removed"] = removed
    with OUT_STATS.open("w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"✅  Stats → {OUT_STATS}")


if __name__ == "__main__":
    main()