"""
建立症狀對照表的語意向量檔。

對照表改過（新增條目、人工審定改寫 term）就必須重跑，否則向量會靜默地對到
錯誤的條目——載入時的 hash 檢查會擋下來並退回 LLM 兜底，功能不會壞，但命中率
會掉回沒有向量的水準，而且只有日誌會說。

    python scripts/build_symptom_vectors.py

沿用 scripts/ingest_url.py 的模式：取向量是離線批次，不在啟動時做。
391 條要打一次 API，約數十秒；放在啟動會讓每次重啟都付這個代價，
也讓沒有網路的環境起不來。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_google_genai import GoogleGenerativeAIEmbeddings  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.medical.symptom_classification.symptom_table import (  # noqa: E402
    load_symptom_table,
)
from app.services.medical.symptom_classification.vector_index import (  # noqa: E402
    DEFAULT_VECTOR_PATH,
    EMBEDDING_TASK_TYPE,
    VECTOR_DIM,
    build_index,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("build_symptom_vectors")

BATCH_SIZE = 100


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_VECTOR_PATH)
    args = parser.parse_args()

    table = load_symptom_table()
    terms = list(table.terms)
    logger.info("對照表 %d 條，維度 %d，task_type=%s", len(terms), VECTOR_DIM, EMBEDDING_TASK_TYPE)

    embeddings = GoogleGenerativeAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        # 對稱比對。不可沿用 RAG 的 RETRIEVAL_DOCUMENT／RETRIEVAL_QUERY 配對，
        # 那是為「問句 → 文件段落」設計的（design 決策 12）。
        task_type=EMBEDDING_TASK_TYPE,
        output_dimensionality=VECTOR_DIM,
    )

    vectors: list[list[float]] = []
    for start in range(0, len(terms), BATCH_SIZE):
        batch = terms[start : start + BATCH_SIZE]
        vectors.extend(await embeddings.aembed_documents(batch))
        logger.info("  %d/%d", len(vectors), len(terms))

    index = build_index(terms, vectors)
    index.save(args.out)
    logger.info("完成。table_hash=%s", index.table_hash)
    logger.info("對照表若再更動，必須重跑本腳本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
