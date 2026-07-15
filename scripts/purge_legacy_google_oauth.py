"""Remove legacy plaintext Google OAuth credentials from MongoDB.

Run with the production profile after rotating the Google OAuth client secret.
The default is a dry run; pass --apply only after confirming the count.
Affected users must reconnect Google Workspace afterwards.
"""

from __future__ import annotations

import argparse
import os
import sys

from pymongo import MongoClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config_loader import cfg


LEGACY_TOP_LEVEL_FIELDS = (
    "google_token",
    "google_refresh_token",
    "google_access_token",
    "google_client_secret",
    "google_token_uri",
)
SERVICE_IDS = ("drive", "gmail", "docs", "calendar", "meet", "sheets", "slides")


def _is_plaintext(value: object) -> bool:
    return bool(value) and not str(value).startswith("enc:v1:")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the irreversible credential removal")
    args = parser.parse_args()

    users = MongoClient(cfg.mongo_uri)[cfg.mongo_database]["users"]
    affected = 0
    for user in users.find({}, {field: 1 for field in LEGACY_TOP_LEVEL_FIELDS} | {
        f"google_creds_{service}": 1 for service in SERVICE_IDS
    }):
        unset_fields = {
            field: ""
            for field in LEGACY_TOP_LEVEL_FIELDS
            if _is_plaintext(user.get(field))
        }
        for service in SERVICE_IDS:
            credentials = user.get(f"google_creds_{service}") or {}
            if any(_is_plaintext(credentials.get(key)) for key in ("google_token", "google_refresh_token")):
                # Keep no partial legacy state: the user will authorize this service again.
                unset_fields[f"google_creds_{service}"] = ""

        if not unset_fields:
            continue
        affected += 1
        if args.apply:
            users.update_one({"_id": user["_id"]}, {"$unset": unset_fields})

    action = "Removed" if args.apply else "Would remove"
    print(f"{action} legacy Google OAuth credentials for {affected} user account(s).")
    if not args.apply:
        print("Dry run only. Re-run with --apply after rotating credentials and confirming the count.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
