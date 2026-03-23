# 命令列：問題 → Gemini embedding → Mongo 向量檢索（取回筆數見 vector_search/config.py 的 default_top_k）
import asyncio
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from app.services.RAG.embedding_gemini import embed_query
from app.services.RAG.retriever import search_similar_chunks


async def main() -> None:
    argv = sys.argv[1:]
    question = " ".join(argv).strip() if argv else input("請輸入問題: ").strip()
    if not question:
        print("問題不能為空。", file=sys.stderr)
        sys.exit(1)

    vec = await embed_query(question)
    out = await search_similar_chunks(vec)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
