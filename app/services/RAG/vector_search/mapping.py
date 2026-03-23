from typing import Any, Mapping

from .types import ChunkHit
#mapping 就是拿來轉換格式
#mongodb 查完 vectorsearch的結果會是dict 轉換成ChunkHit 的型別
def mongo_document_to_chunk_hit(
    doc: Mapping[str, Any],
    *,
    text_field: str,
) -> ChunkHit:
    # 將 aggregation 結果文件轉成對外契約 ChunkHit。
    return {
        "id": str(doc.get("_id")),
        "text": doc.get(text_field),
        "score": doc.get("score"),
    }
