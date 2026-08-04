from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.db.mongodb import MongoDBManager

logger = logging.getLogger(__name__)


async def ensure_user_docs_indexes(collection: Any) -> None:
    """確保 user-docs 集合有 expires_at TTL index。"""
    await collection.create_index(
        [("expires_at", 1)],
        name="user_docs_expires_at_ttl",
        expireAfterSeconds=0,
    )


async def ensure_user_docs_indexes_on_startup() -> None:
    """啟動時建立 TTL index；Mongo 未設定時只 log，不阻斷啟動。"""
    if not settings.MONGODB_USER_DOCS_COLLECTION:
        return

    if not settings.MONGODB_URI or not settings.MONGODB_DB:
        logger.warning(
            "MONGODB_USER_DOCS_COLLECTION is set but MongoDB is not configured; "
            "skipping user docs index setup"
        )
        return

    try:
        collection = MongoDBManager.get_database()[settings.MONGODB_USER_DOCS_COLLECTION]
        await ensure_user_docs_indexes(collection)
        logger.info(
            "Ensured user docs TTL index on collection %s",
            settings.MONGODB_USER_DOCS_COLLECTION,
        )
    except Exception as exc:
        logger.warning("Failed to ensure user docs indexes: %s", exc)
