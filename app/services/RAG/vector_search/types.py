from typing import List, Optional, TypedDict


class ChunkHit(TypedDict):
    # 與 vector search 投影欄位一致：Mongo _id、chunk 文字、相似度分數
    id: str
    text: Optional[str]
    score: Optional[float]


ChunkHits = List[ChunkHit]
