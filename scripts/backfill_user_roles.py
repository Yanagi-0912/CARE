#!/usr/bin/env python3
"""Set role=admin on all existing user documents."""

from __future__ import annotations

import os

import pymongo
from dotenv import load_dotenv


DEFAULT_DB_NAME = "CARE_database"
DEFAULT_COLLECTION_NAME = "users"


def _get_mongodb_uri() -> str:
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGODB_URL")
    if not uri:
        raise RuntimeError("Missing MONGODB_URI or MONGODB_URL")
    return uri


def main() -> None:
    load_dotenv()
    uri = _get_mongodb_uri()
    db_name = os.getenv("MONGODB_DB", DEFAULT_DB_NAME)
    collection_name = os.getenv("MONGODB_USERS_COLLECTION", DEFAULT_COLLECTION_NAME)

    client = pymongo.MongoClient(uri)
    collection = client[db_name][collection_name]

    total = collection.count_documents({})
    result = collection.update_many({}, {"$set": {"role": "admin"}})

    print(f"total_users={total}")
    print(f"matched={result.matched_count}")
    print(f"modified={result.modified_count}")


if __name__ == "__main__":
    main()
