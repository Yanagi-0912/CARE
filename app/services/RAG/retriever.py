from typing import List, Optional

from .vector_search import ChunkHits, MongoVectorSearchReader, VectorSearchConfig

# 模組載入時讀一次設定；查詢時不重複 from_settings()。DI：由此注入 MongoVectorSearchReader。
_cfg = VectorSearchConfig.from_settings()

_reader: Optional[MongoVectorSearchReader] = None


def _get_vector_reader() -> MongoVectorSearchReader:
    global _reader
    if _reader is None:
        _reader = MongoVectorSearchReader(_cfg) # mongoVectorSearchReader 是 reader.py 裡最主要的funcion 可以給你要的topk
    return _reader


def search_similar_chunks(query_embedding: List[float]) -> ChunkHits: #這個 function 現在是被script的那便呼叫
    # query_embedding：已算好的問題向量。取幾筆由 reader 內讀注入的 cfg.default_top_k。
    return _get_vector_reader().search_by_embedding(query_embedding=query_embedding)
