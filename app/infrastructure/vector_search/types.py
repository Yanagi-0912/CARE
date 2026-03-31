from typing import List, Optional, TypedDict


class ChunkHit(TypedDict):
    id: str
    text: Optional[str]
    score: Optional[float]


ChunkHits = List[ChunkHit]
