"""
upload_finetune.py
==================
Uploads your training data to Amazon S3 and submits a Claude fine-tuning
job on Amazon Bedrock — the ONLY supported way to fine-tune Claude models.

NOTE: Anthropic does NOT offer a direct fine-tuning API.
      Fine-tuning Claude is only available through Amazon Bedrock.

PRE-REQUISITES:
  1. AWS account with Bedrock access (us-west-2 recommended)
  2. Claude 3 Haiku fine-tuning access approved by AWS/Anthropic
  3. An S3 bucket in the SAME region as your Bedrock service
  4. IAM role with S3 read + Bedrock permissions (see README)

INSTALL DEPS (once):
    pip install boto3 python-dotenv

USAGE:
    cd ~/MSME-LLM
    python Finetune/prepare_training_data.py   # build dataset first
    python Finetune/upload_finetune.py

ENV vars — add to your .env file:
    AWS_ACCESS_KEY_ID=...
    AWS_SECRET_ACCESS_KEY=...
    AWS_DEFAULT_REGION=us-west-2
    BEDROCK_S3_BUCKET=your-bucket-name
    BEDROCK_ROLE_ARN=arn:aws:iam::ACCOUNT:role/BedrockFineTuningRole
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("❌  Missing: pip install boto3 python-dotenv")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── paths ─────────────────────────────────────────────────────────────────────
FINETUNE_DIR  = Path(__file__).resolve().parent
TRAINING_FILE = FINETUNE_DIR / "training_output" / "combined_training.jsonl"
OUT_DIR       = FINETUNE_DIR / "training_output"
JOB_FILE      = OUT_DIR / "finetune_job.json"

# ── config ────────────────────────────────────────────────────────────────────
BASE_MODEL    = "anthropic.claude-3-haiku-20240307-v1:0"   # only model supporting fine-tuning
REGION        = os.getenv("AWS_DEFAULT_REGION", "us-west-2")
S3_BUCKET     = os.getenv("BEDROCK_S3_BUCKET", "")
ROLE_ARN      = os.getenv("BEDROCK_ROLE_ARN", "")
JOB_NAME      = f"msme-malay-assistant-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
CUSTOM_MODEL  = f"msme-malay-haiku-{datetime.now().strftime('%Y%m%d')}"

# Hyperparameters (adjust as needed)
EPOCHS        = 3
BATCH_SIZE    = 8
LEARNING_RATE = 0.00001   # 1e-5


# ── helpers ───────────────────────────────────────────────────────────────────
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str) -> str:
    if text is None:
        return ""
    text = str(text)
    text = CONTROL_CHAR_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def valid_chat_sequence(chat_msgs: list[dict]) -> bool:
    if not chat_msgs:
        return False
    if chat_msgs[0].get("role") != "user":
        return False
    expected = "user"
    for m in chat_msgs:
        if m.get("role") != expected:
            return False
        content = sanitize_text(m.get("content", ""))
        if not content:
            return False
        expected = "assistant" if expected == "user" else "user"
    return chat_msgs[-1].get("role") == "assistant"


def check_env():
    missing = []
    if not S3_BUCKET:
        missing.append("BEDROCK_S3_BUCKET")
    if not ROLE_ARN:
        missing.append("BEDROCK_ROLE_ARN")
    if missing:
        sys.exit(
            f"❌  Missing env vars: {', '.join(missing)}\n"
            "Add them to your .env file — see the script header for details."
        )


def check_training_file() -> int:
    if not TRAINING_FILE.exists():
        sys.exit(
            f"❌  Training file not found: {TRAINING_FILE}\n"
            "Run prepare_training_data.py first."
        )
    line_count = 0
    with TRAINING_FILE.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                line_count += 1
    print(f"Training records: {line_count}")
    if line_count < 32:
        sys.exit("❌  Bedrock requires at least 32 training examples.")
    return line_count


def convert_to_bedrock_format(src: Path, dst: Path):
    """
    Bedrock expects:
        {"system": "...", "messages": [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}]}
    Our format already has messages[0].role == "system", so we just restructure.
    """
    converted = 0
    skipped = 0
    with src.open(encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            msgs = r.get("messages", [])
            system_content = sanitize_text(
                next((m.get("content", "") for m in msgs if m.get("role") == "system"), "")
            )
            chat_msgs = []
            for m in msgs:
                role = m.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = sanitize_text(m.get("content", ""))
                if not content:
                    continue
                chat_msgs.append({"role": role, "content": content})

            if not valid_chat_sequence(chat_msgs):
                skipped += 1
                continue

            bedrock_record = {
                "system": system_content,
                "messages": chat_msgs,
            }
            fout.write(json.dumps(bedrock_record, ensure_ascii=False) + "\n")
            converted += 1
    print(f"Converted {converted} records to Bedrock format → {dst.name}")
    if skipped:
        print(f"Skipped {skipped} invalid/non-alternating records")
    return dst


def upload_to_s3(local_path: Path, s3_key: str) -> str:
    s3 = boto3.client("s3", region_name=REGION)
    print(f"Uploading {local_path.name} → s3://{S3_BUCKET}/{s3_key} …")
    s3.upload_file(str(local_path), S3_BUCKET, s3_key)
    s3_uri = f"s3://{S3_BUCKET}/{s3_key}"
    print(f"  Upload complete: {s3_uri}")
    return s3_uri


def create_finetune_job(training_s3_uri: str) -> dict:
    bedrock = boto3.client("bedrock", region_name=REGION)
    print(f"Creating fine-tuning job: {JOB_NAME}")

    response = bedrock.create_model_customization_job(
        jobName=JOB_NAME,
        customModelName=CUSTOM_MODEL,
        roleArn=ROLE_ARN,
        baseModelIdentifier=BASE_MODEL,
        customizationType="FINE_TUNING",
        trainingDataConfig={"s3Uri": training_s3_uri},
        outputDataConfig={"s3Uri": f"s3://{S3_BUCKET}/finetune-output/{JOB_NAME}/"},
        hyperParameters={
            "epochCount":    str(EPOCHS),
            "batchSize":     str(BATCH_SIZE),
            "learningRate":  str(LEARNING_RATE),
        },
    )
    job_arn = response["jobArn"]
    print(f"Job ARN: {job_arn}")
    return {"job_arn": job_arn, "job_name": JOB_NAME, "custom_model": CUSTOM_MODEL}


def poll_job(job_arn: str, interval: int = 60):
    bedrock = boto3.client("bedrock", region_name=REGION)
    terminal = {"Completed", "Failed", "Stopped"}
    print(f"\nPolling every {interval}s … (Ctrl-C to stop)")
    try:
        while True:
            resp = bedrock.get_model_customization_job(jobIdentifier=job_arn)
            status = resp.get("status", "Unknown")
            ts = time.strftime("%H:%M:%S")
            print(f"  [{ts}] status={status}")
            if status in terminal:
                return resp
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nPolling stopped. Check status later:")
        print(f"  aws bedrock get-model-customization-job --job-identifier '{job_arn}' --region {REGION}")
        return None


def save_job_info(info: dict, record_count: int):
    info["record_count"] = record_count
    info["base_model"]   = BASE_MODEL
    info["region"]       = REGION
    info["created_at"]   = datetime.utcnow().isoformat() + "Z"
    with JOB_FILE.open("w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    print(f"✅  Job info saved → {JOB_FILE}")


def main():
    check_env()
    record_count = check_training_file()

    # Convert to Bedrock format
    bedrock_file = OUT_DIR / "combined_training_bedrock.jsonl"
    convert_to_bedrock_format(TRAINING_FILE, bedrock_file)

    # Upload to S3
    s3_key = f"finetune-training/{JOB_NAME}/training.jsonl"
    training_s3_uri = upload_to_s3(bedrock_file, s3_key)

    # Submit job
    try:
        job_info = create_finetune_job(training_s3_uri)
    except ClientError as e:
        sys.exit(f"❌  Bedrock error: {e}")

    save_job_info(job_info, record_count)

    answer = input("\nPoll until completion? [y/N] ").strip().lower()
    if answer == "y":
        final = poll_job(job_info["job_arn"])
        if final:
            status = final.get("status")
            model  = final.get("outputModelArn", "")
            print(f"\nFinal status : {status}")
            if model:
                print(f"Model ARN    : {model}")
                print(f"\nTo use your model:")
                print(f"  aws bedrock-runtime invoke-model \\")
                print(f"    --model-id '{model}' \\")
                print(f"    --region {REGION} \\")
                print(f"    --body '{{\"messages\":[{{\"role\":\"user\",\"content\":\"Hello\"}}]}}' output.json")
            job_info["final_status"] = status
            job_info["output_model_arn"] = model
            save_job_info(job_info, record_count)
    else:
        print(f"\nJob ARN: {job_info['job_arn']}")
        print(f"Check status:")
        print(f"  aws bedrock get-model-customization-job \\")
        print(f"    --job-identifier '{job_info['job_arn']}' --region {REGION}")


if __name__ == "__main__":
    main()