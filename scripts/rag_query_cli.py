import argparse
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


async def _run(question: str, k: int) -> None:
    if not question.strip():
        print("問題不能為空。", file=sys.stderr)
        sys.exit(1)

    vec = await embed_query(question.strip())
    results = search_similar_chunks(vec, k=k)
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*")
    parser.add_argument("-k", "--top-k", type=int, default=10)
    args = parser.parse_args()

    if args.question:
        q = " ".join(args.question).strip()
    else:
        q = input("請輸入問題: ").strip()

    asyncio.run(_run(q, k=args.top_k))


if __name__ == "__main__":
    main()
