#!/usr/bin/env python3
"""Safely backfill split-name and international-phone fields in MongoDB users.

Run without --apply first. It reports documents that need human review and
duplicate E.164 numbers; it never guesses a missing family name or a country.
Only run --create-phone-index after the dry-run reports zero duplicate numbers.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat
from pymongo import ASCENDING, MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config_loader import cfg


def clean_name(value: object) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def split_name(value: object) -> tuple[str, str, bool]:
    """Return first, last and whether the legacy value needs human review."""
    cleaned = clean_name(value)
    if not cleaned:
        return "", "", True
    parts = cleaned.split(" ", 1)
    if len(parts) == 1:
        return parts[0], "", True
    return parts[0], parts[1], False


def phone_fields(user: dict) -> tuple[dict, str | None]:
    raw_e164 = clean_name(user.get("phone_e164") or user.get("phone"))
    if not raw_e164:
        return {}, None
    country_iso = clean_name(user.get("country_iso")).upper() or None
    try:
        parsed = phonenumbers.parse(raw_e164, country_iso)
    except NumberParseException:
        return {}, "phone could not be parsed"
    if not phonenumbers.is_valid_number(parsed):
        return {}, "phone is not a valid assigned number range"
    resolved_iso = phonenumbers.region_code_for_number(parsed) or country_iso
    if not resolved_iso:
        return {}, "country could not be resolved from phone"
    national = re.sub(
        r"\D",
        "",
        phonenumbers.format_number(parsed, PhoneNumberFormat.NATIONAL),
    )
    return {
        "phone": phonenumbers.format_number(parsed, PhoneNumberFormat.E164),
        "phone_e164": phonenumbers.format_number(parsed, PhoneNumberFormat.E164),
        "phone_number": user.get("phone_number") or national,
        "country_code": f"+{parsed.country_code}",
        "country_iso": resolved_iso,
        # Existing email OTP verification is not phone ownership verification.
        "phone_verified_at": user.get("phone_verified_at"),
    }, None


def proposed_update(user: dict) -> tuple[dict, list[str]]:
    update: dict = {"updated_at": datetime.utcnow()}
    reviews: list[str] = []
    existing_notes = [str(note) for note in (user.get("identity_migration_notes") or [])]
    # A previous --apply may intentionally have left a duplicate legacy phone
    # without phone_e164.  Do not rediscover and re-propose it on a later run:
    # that would make the follow-up unique-index command impossible forever.
    deferred_duplicate_notes = [
        note for note in existing_notes if note.startswith("duplicate phone_e164 ")
    ]

    first_name = clean_name(user.get("first_name"))
    last_name = clean_name(user.get("last_name"))
    if not (first_name and last_name):
        first_name, last_name, needs_name_review = split_name(user.get("display_name") or user.get("name"))
        if needs_name_review:
            reviews.append("name needs first/last-name review")
        update["first_name"] = first_name
        update["last_name"] = last_name
        if first_name and last_name:
            update["name"] = f"{first_name} {last_name}"
            update["display_name"] = f"{first_name} {last_name}"

    if deferred_duplicate_notes:
        reviews.extend(deferred_duplicate_notes)
    else:
        phone_update, phone_review = phone_fields(user)
        if phone_review:
            reviews.append(phone_review)
        elif phone_update:
            update.update(phone_update)

    update["identity_migration_status"] = "needs_review" if reviews else "migrated"
    if reviews:
        update["identity_migration_notes"] = reviews
    return update, reviews


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the proposed updates to MongoDB")
    parser.add_argument(
        "--create-phone-index",
        action="store_true",
        help="Create the sparse unique phone_e164 index after a clean duplicate report",
    )
    parser.add_argument("--limit", type=int, default=0, help="Only inspect this many users (0 means all)")
    args = parser.parse_args()

    users = MongoClient(cfg.mongo_uri)[cfg.mongo_database]["users"]
    cursor = users.find({})
    if args.limit:
        cursor = cursor.limit(args.limit)

    inspected = migrated = 0
    review_rows: list[tuple[str, list[str]]] = []
    duplicate_candidates: dict[str, list[str]] = defaultdict(list)
    proposed: list[tuple[dict, dict, list[str]]] = []
    for user in cursor:
        inspected += 1
        update, reviews = proposed_update(user)
        e164 = update.get("phone_e164")
        if e164:
            duplicate_candidates[e164].append(str(user["_id"]))
        proposed.append((user, update, reviews))

    duplicates = {number: ids for number, ids in duplicate_candidates.items() if len(ids) > 1}
    for user, update, reviews in proposed:
        if update.get("phone_e164") in duplicates:
            reviews = [*reviews, f"duplicate phone_e164 {update['phone_e164']}"]
            update["identity_migration_status"] = "needs_review"
            update["identity_migration_notes"] = reviews
            # Do not write a duplicate into a future unique index.
            update.pop("phone_e164", None)
        if reviews:
            review_rows.append((str(user["_id"]), reviews))
        if args.apply:
            users.update_one({"_id": user["_id"]}, {"$set": update})
            migrated += 1

    print(f"Inspected: {inspected}")
    print(f"Would update: {len(proposed)}" if not args.apply else f"Updated: {migrated}")
    print(f"Needs review: {len(review_rows)}")
    print(f"Duplicate E.164 numbers: {len(duplicates)}")
    for user_id, notes in review_rows[:50]:
        print(f"  {user_id}: {'; '.join(notes)}")
    if len(review_rows) > 50:
        print(f"  ... and {len(review_rows) - 50} more")

    if args.create_phone_index:
        if not args.apply:
            raise SystemExit("--create-phone-index requires --apply")
        if duplicates:
            raise SystemExit("Refusing to create phone_e164 index until duplicate numbers are resolved")
        users.create_index(
            [("phone_e164", ASCENDING)],
            name="phone_e164_unique",
            unique=True,
            sparse=True,
        )
        print("Created sparse unique index: phone_e164_unique")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
