import asyncio
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from app.dependencies import get_health_classifier


async def main() -> None:
    argv = sys.argv[1:]
    question = " ".join(argv).strip() if argv else input("請輸入要分類的句子: ").strip()
    if not question:
        print("問題不能為空。", file=sys.stderr)
        sys.exit(1)

    classifier = get_health_classifier()
    result = await classifier.classify(question)

    output = {
        "question": question,
        "is_health_related": result.is_health_related,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
