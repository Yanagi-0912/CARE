# 命令列：問題 → LangChain retriever（embed + Mongo $vectorSearch）
import asyncio
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from app.dependencies import get_rag_retriever


async def main() -> None:
    argv = sys.argv[1:]
    question = " ".join(argv).strip() if argv else input("請輸入問題: ").strip()
    if not question:
        print("問題不能為空。", file=sys.stderr)
        sys.exit(1)

    docs = await get_rag_retriever().ainvoke(question)
    out = [
        {
            "text": doc.page_content,
            **doc.metadata,
        }
        for doc in docs
    ]
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
