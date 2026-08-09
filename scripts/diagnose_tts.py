import asyncio
from pathlib import Path
import sys

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.services.line_messaging.reply.tts_service import TTSService


def _mask(value: str) -> str:
    if not value:
        return "MISSING"
    if len(value) <= 12:
        return "SET"
    return f"{value[:8]}...{value[-4:]}"


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    print("TTS diagnostics")
    print(f"Project root: {PROJECT_ROOT}")
    print(f".env exists: {(PROJECT_ROOT / '.env').exists()}")
    print(f"PUBLIC_BASE_URL: {_mask(settings.PUBLIC_BASE_URL)}")
    print(f"TTS_AUDIO_URL_PATH: {settings.TTS_AUDIO_URL_PATH!r}")

    service = TTSService()
    print(f"TTS available: {service.available()}")

    if not settings.PUBLIC_BASE_URL.strip():
        print("ERROR: PUBLIC_BASE_URL is empty. LINE audio replies will be skipped.")

    try:
        _data, path, duration_ms = asyncio.run(
            service.synthesize("這是 CARE 語音回覆診斷測試。", language="zh-TW")
        )
    except Exception as exc:
        print(f"ERROR: TTS synthesis failed: {exc}")
        return

    audio_path = Path(path)
    print(f"Generated file: {audio_path}")
    print(f"File exists: {audio_path.exists()}")
    print(f"Duration ms: {duration_ms}")

    if settings.PUBLIC_BASE_URL.strip():
        base_url = settings.PUBLIC_BASE_URL.rstrip("/")
        audio_url_path = settings.TTS_AUDIO_URL_PATH.strip("/") or "tts"
        audio_url = f"{base_url}/{audio_url_path}/{audio_path.name}"
        print(f"Public audio URL: {audio_url}")

        try:
            response = requests.get(audio_url, timeout=10)
            print(f"Public URL status: {response.status_code}")
            print(f"Public URL content-type: {response.headers.get('content-type')}")
            print(f"Public URL bytes: {len(response.content)}")
        except Exception as exc:
            print(f"ERROR: Could not fetch public audio URL: {exc}")


if __name__ == "__main__":
    main()
