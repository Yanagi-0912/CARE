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

from app.services.RAG.embedding_gemini import embed_text
from app.services.RAG.retriever import retrieve_top_k_by_vector


async def _run(question: str, k: int, task_type: str) -> None:
    if not question.strip():
        print("問題不能為空。", file=sys.stderr)
        sys.exit(1)

    vec = await embed_text(question.strip(), task_type=task_type)
    results = retrieve_top_k_by_vector(vec, k=k)
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*")
    parser.add_argument("-k", "--top-k", type=int, default=10)
    parser.add_argument("--task-type", default="RETRIEVAL_QUERY")
    args = parser.parse_args()

    if args.question:
        q = " ".join(args.question).strip()
    else:
        q = input("請輸入問題: ").strip()

    asyncio.run(_run(q, k=args.top_k, task_type=args.task_type))


if __name__ == "__main__":
    main()
