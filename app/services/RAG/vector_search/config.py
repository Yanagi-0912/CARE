# 向量搜尋設定：連線由 app.settings 注入；其餘用欄位預設值即可調整。

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# 向量搜尋所需設定（Store 只依賴此物件）。
# - 連線／欄位名：由 from_settings 從 app.core.config 填入
# - 檢索行為：改下面欄位預設值即可（不必另建模組常數）
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
    num_candidates_override: Optional[int] = None  # None = 用倍率公式；設正整數則固定
    num_candidates_multiplier: int = 20
    num_candidates_min: int = 100
    num_candidates_max: int = 10000

    # 決定 $vectorSearch 的 numCandidates。
    # per_call：單次查詢覆寫；否則先看 num_candidates_override，再用倍率公式。
    # 結果會夾在 [k, num_candidates_max]（Atlas 要求 nc >= limit）。
    def resolve_num_candidates(self, k: int, per_call: Optional[int] = None) -> int:
        if per_call is not None:
            nc = per_call
        elif self.num_candidates_override is not None:
            nc = self.num_candidates_override
        else:
            nc = max(k * self.num_candidates_multiplier, self.num_candidates_min)
        nc = max(nc, k)
        nc = min(nc, self.num_candidates_max)
        return nc

    # Mongo 連線／欄位取自 app.settings；其餘用 dataclass 預設值。
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
