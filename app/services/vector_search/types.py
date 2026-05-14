from typing import List, Optional, TypedDict


class ChunkHit(TypedDict):
    id: str
    text: Optional[str]
    score: Optional[float]
    source_name: Optional[str]
    url: Optional[str]


ChunkHits = List[ChunkHit]
