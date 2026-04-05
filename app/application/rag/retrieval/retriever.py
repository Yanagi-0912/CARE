from typing import List

from app.infrastructure.vector_search import ChunkHits, MongoVectorSearchReader


async def search_similar_chunks(
    query_embedding: List[float],
    reader: MongoVectorSearchReader,
) -> ChunkHits:
    return await reader.search_by_embedding(query_embedding=query_embedding)
