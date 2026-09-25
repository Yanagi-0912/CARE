from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.rag.pgvector_retriever import PgVectorRetriever


class _FakeConn:
    def __init__(self):
        self.executed: list[str] = []

    async def execute(self, sql):
        self.executed.append(sql)

    async def fetch(self, sql, *args):
        return []


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _make_retriever(k):
    emb = MagicMock()
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    retriever = PgVectorRetriever(
        embeddings=emb,
        dsn="postgresql://unused",
        mongo_uri="mongodb://unused",
        db_name="db",
        collection_name="coll",
        vector_dim=3,
        k=k,
    )
    conn = _FakeConn()
    retriever._pool = _FakePool(conn)
    return retriever, conn


@pytest.mark.parametrize(
    ("k", "expected"),
    [
        (1, 40),  # 下限：pgvector 預設值
        (10, 300),
        (33, 990),
        (40, 1000),  # 線上的 RAG_RETRIEVE_CANDIDATES；k×30=1200 會被 PG 拒絕
        (100, 1000),
    ],
)
async def test_ef_search_stays_within_pgvector_range(k, expected):
    retriever, conn = _make_retriever(k)

    await retriever.ainvoke("高血壓可以喝咖啡嗎")

    assert conn.executed == [f"SET hnsw.ef_search = {expected}"]
