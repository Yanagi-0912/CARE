"""把 Atlas 上新寫入的向量搬到 pgvector，然後從 Atlas 清掉。

背景
----
2026-09-19 向量從 Atlas 搬到 PostgreSQL（見 `app/services/rag/pgvector_retriever.py`
的模組註解）。但 CARE-data 的 ETL 跑在 GitHub Actions 上，而 PG 的 service 是
ClusterIP、叢集外連不到，所以 ETL 沒辦法直接寫 PG——它仍然照舊把 embedding
寫進 Atlas。放著不管的話，以每天約 1,000 筆、42 MB 的速度，十天左右 Atlas 又會
撞到免費層的 512 MB 上限，寫入被鎖、後端的 ensure_indexes() 失敗、新版 pod 起不來
（2026-09-19 就是這樣壞的）。

這支腳本是那個缺口的橋：在叢集內定時執行，把 ETL 剛寫進 Atlas 的向量搬進 PG，
再把 Atlas 上的 embedding 欄位清掉。ETL 一行都不用改。

這是過渡方案。要收乾淨的話，該把 ETL 本身搬進叢集直接寫 PG，那時這支就可以退役。

為什麼要以明確的 id 清單來清除
------------------------------
2026-09-19 手動搬移時踩過：流程是「查出有 embedding 的 → 匯出 → 寫 PG → 清掉有
embedding 的」，但 ETL 在這中間還在寫，最後那步用 `{embedding: {$exists: true}}`
重新查了一次，於是把匯出之後新寫進來、還沒進 PG 的那批也一起清掉了——204 筆
向量就這樣沒了（內文還在，但語意檢索命中不了）。

所以這裡每一批都記住自己讀到的 `_id`，清除時只清那些 id，絕不重新查詢條件。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
)
logger = logging.getLogger("sync_vectors_to_pg")

# 一批的大小。取 200 是在兩件事之間折衷：批次太小則來回次數多（每批都有一次
# Atlas 查詢、一次 PG 寫入、一次 Atlas 更新），太大則單批的記憶體佔用高——
# 一筆 3072 維 float 約 42 KB，200 筆就是 8.4 MB，而 backend 的記憶體上限是
# 1536 Mi，同一個 image 起的 pod 不該為了搬資料把自己推向 OOM。
BATCH_SIZE = 200


async def main() -> int:
    mongo_uri = os.getenv("MONGODB_URI", "")
    dsn = os.getenv("PGVECTOR_SYNC_DSN", "")
    db_name = os.getenv("MONGODB_DB", "")
    collection_name = os.getenv("MONGODB_COLLECTION", "")
    table_name = os.getenv("PGVECTOR_TABLE", "health_articles_chunks")

    missing = [
        name
        for name, value in (
            ("MONGODB_URI", mongo_uri),
            ("PGVECTOR_SYNC_DSN", dsn),
            ("MONGODB_DB", db_name),
            ("MONGODB_COLLECTION", collection_name),
        )
        if not value
    ]
    if missing:
        logger.error("缺少設定：%s", ", ".join(missing))
        return 2

    import asyncpg
    from motor.motor_asyncio import AsyncIOMotorClient

    mongo = AsyncIOMotorClient(mongo_uri)
    collection = mongo[db_name][collection_name]
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, command_timeout=60)

    moved = 0
    verdict_synced = 0
    failed_batches = 0
    try:
        while True:
            batch = await collection.find(
                {"embedding": {"$exists": True}},
                {"_id": 1, "embedding": 1, "verdict": 1},
            ).limit(BATCH_SIZE).to_list(length=BATCH_SIZE)
            if not batch:
                break

            rows = []
            for doc in batch:
                vector = doc.get("embedding")
                if not vector:
                    continue
                literal = "[" + ",".join(repr(float(v)) for v in vector) + "]"
                rows.append((str(doc["_id"]), literal, doc.get("verdict")))

            if not rows:
                # 這批全是空向量——不搬，但也要清掉，否則下一圈會拿到同一批、
                # 變成無窮迴圈。
                await collection.update_many(
                    {"_id": {"$in": [d["_id"] for d in batch]}},
                    {"$unset": {"embedding": ""}},
                )
                continue

            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.executemany(
                            f"INSERT INTO {table_name} (id, embedding, embedding_half, verdict) "
                            "VALUES ($1, $2::vector, $2::vector::halfvec, $3) "
                            "ON CONFLICT (id) DO UPDATE SET "
                            "  embedding = EXCLUDED.embedding, "
                            "  embedding_half = EXCLUDED.embedding_half, "
                            "  verdict = EXCLUDED.verdict",
                            rows,
                        )
            except Exception:
                # 寫 PG 失敗就**不要**清 Atlas：向量還在 Atlas 就還救得回來，
                # 清掉才是真的沒了。這一批留到下次執行重試。
                logger.exception("PG 寫入失敗，這批不清除 Atlas，留待下次重試")
                failed_batches += 1
                break

            # 只清這一批讀到的 id。不重新查 {embedding: {$exists: true}}——
            # 見模組註解裡那 204 筆的教訓。
            synced_ids = [doc["_id"] for doc in batch]
            await collection.update_many(
                {"_id": {"$in": synced_ids}}, {"$unset": {"embedding": ""}}
            )
            moved += len(rows)
            verdict_synced += sum(1 for r in rows if r[2])
            logger.info("已搬移 %d 筆（本批 %d）", moved, len(rows))

        logger.info(
            "完成：搬移 %d 筆向量（其中 %d 筆帶 verdict），失敗批次 %d",
            moved,
            verdict_synced,
            failed_batches,
        )
        return 1 if failed_batches else 0
    finally:
        await pool.close()
        mongo.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
