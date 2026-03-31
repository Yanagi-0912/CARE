from typing import Any, Mapping

from .types import ChunkHit


def mongo_document_to_chunk_hit(
    doc: Mapping[str, Any],
    *,
    text_field: str,
) -> ChunkHit:
    return {
        "id": str(doc.get("_id")),
        "text": doc.get(text_field),
        "score": doc.get("score"),
    }
