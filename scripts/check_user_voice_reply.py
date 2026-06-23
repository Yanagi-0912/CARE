import argparse
import os
from dataclasses import dataclass
from typing import Any

import pymongo
from dotenv import load_dotenv
from pymongo import UpdateOne


DEFAULT_DB_NAME = "CARE_database"
DEFAULT_COLLECTION_NAME = "users"


@dataclass
class AuditResult:
    total: int
    valid: int
    missing: int
    invalid: int
    fixed: int


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("default value must be true or false")


def _get_mongodb_url() -> str:
    url = os.getenv("MONGODB_URL") or os.getenv("MONGODB_URI")
    if not url:
        raise RuntimeError("Missing MONGODB_URL or MONGODB_URI")
    return url


def _is_valid_voice_value(value: Any) -> bool:
    return isinstance(value, bool)


def audit_users_collection(
    collection,
    *,
    fix: bool = False,
    default_value: bool = True,
    sample_limit: int = 20,
) -> AuditResult:
    total = collection.count_documents({})
    cursor = collection.find(
        {},
        {
            "_id": 1,
            "line_id": 1,
            "name": 1,
            "voice_reply_enabled": 1,
        },
    )

    valid = 0
    missing = 0
    invalid = 0
    operations = []
    samples = []

    for doc in cursor:
        has_field = "voice_reply_enabled" in doc
        current_value = doc.get("voice_reply_enabled")
        is_valid = has_field and _is_valid_voice_value(current_value)

        if is_valid:
            valid += 1
            continue

        if has_field:
            invalid += 1
            reason = f"invalid value: {current_value!r}"
        else:
            missing += 1
            reason = "missing field"

        if len(samples) < sample_limit:
            samples.append(
                {
                    "_id": doc.get("_id"),
                    "line_id": doc.get("line_id"),
                    "name": doc.get("name"),
                    "reason": reason,
                }
            )

        if fix:
            operations.append(
                UpdateOne(
                    {"_id": doc["_id"]},
                    {"$set": {"voice_reply_enabled": default_value}},
                )
            )

    fixed = 0
    if operations:
        result = collection.bulk_write(operations, ordered=False)
        fixed = result.modified_count + len(getattr(result, "upserted_ids", {}) or {})

    print("User voice_reply_enabled audit")
    print(f"Total users: {total}")
    print(f"Valid: {valid}")
    print(f"Missing voice_reply_enabled: {missing}")
    print(f"Invalid voice_reply_enabled type/value: {invalid}")

    if samples:
        print("\nSample affected users:")
        for sample in samples:
            print(
                "- "
                f"line_id={sample['line_id']!r}, "
                f"name={sample['name']!r}, "
                f"_id={sample['_id']!r}, "
                f"reason={sample['reason']}"
            )

    if fix:
        print(f"\nFixed users: {fixed}")
        print(f"Default value applied: {default_value}")
    else:
        print("\nDry run only. Re-run with --fix to update affected users.")

    return AuditResult(
        total=total,
        valid=valid,
        missing=missing,
        invalid=invalid,
        fixed=fixed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit and optionally backfill users.voice_reply_enabled."
    )
    parser.add_argument("--fix", action="store_true", help="Apply missing/invalid fixes.")
    parser.add_argument(
        "--default",
        type=_parse_bool,
        default=True,
        help="Default value to write when fixing records. Default: true.",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_NAME,
        help=f"MongoDB database name. Default: {DEFAULT_DB_NAME}.",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
        help=f"MongoDB users collection name. Default: {DEFAULT_COLLECTION_NAME}.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=20,
        help="Number of affected users to print as samples. Default: 20.",
    )
    args = parser.parse_args()

    load_dotenv()
    mongodb_url = _get_mongodb_url()

    client = pymongo.MongoClient(mongodb_url)
    collection = client[args.db][args.collection]

    try:
        audit_users_collection(
            collection,
            fix=args.fix,
            default_value=args.default,
            sample_limit=max(args.sample_limit, 0),
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
