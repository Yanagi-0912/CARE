from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_NUM_CANDIDATES_K_MULTIPLIER = 30


@dataclass(frozen=True)
class VectorSearchConfig:
    mongo_uri: str
    db_name: str
    collection_name: str
    vector_index: str
    vector_field: str
    text_field: str
    vector_dim: Optional[int] = None
    default_top_k: int = 10

    def resolve_num_candidates(self, k: int) -> int:
        return k * _NUM_CANDIDATES_K_MULTIPLIER

    @classmethod
    def from_settings(cls) -> "VectorSearchConfig":
        from app.core.config import settings

        dim = settings.MONGODB_VECTOR_DIM
        return cls(
            mongo_uri=settings.MONGODB_URI,
            db_name=settings.MONGODB_DB,
            collection_name=settings.MONGODB_COLLECTION,
            vector_index=settings.MONGODB_VECTOR_INDEX,
            vector_field=settings.MONGODB_VECTOR_FIELD,
            text_field=settings.MONGODB_TEXT_FIELD,
            vector_dim=dim if dim > 0 else None,
        )
