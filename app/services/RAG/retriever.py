from typing import List

from .vector_search import ChunkHits, MongoVectorSearchReader


async def search_similar_chunks(
    query_embedding: List[float],
    reader: MongoVectorSearchReader,
) -> ChunkHits:
    # 這個 function 現在是被 script 的那邊呼叫
    # query_embedding：已算好的問題向量。取幾筆由 reader 內讀注入的 cfg.default_top_k。
    # Mongo 使用 Motor 非同步查詢，不阻塞 FastAPI / asyncio 事件迴圈。
    return await reader.search_by_embedding(query_embedding=query_embedding)
