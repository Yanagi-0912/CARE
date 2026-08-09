#!/usr/bin/env python3
"""RAG 入庫 CLI：白名單 URL → Firecrawl scrape → chunk → embed → Mongo replace-by-url."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from langchain_google_genai import GoogleGenerativeAIEmbeddings
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.services.rag.chunking import split_text_to_chunks
from app.services.rag.firecrawl_client import FirecrawlClient
from app.services.rag.ingest_service import IngestResult, IngestService
from app.services.rag.whitelist import is_allowed_url, normalize_url


def _build_embeddings() -> GoogleGenerativeAIEmbeddings:
    kwargs: dict = {
        "model": settings.EMBEDDING_MODEL,
        "google_api_key": settings.GEMINI_API_KEY,
        "task_type": "RETRIEVAL_DOCUMENT",
    }
    if settings.MONGODB_VECTOR_DIM > 0:
        kwargs["output_dimensionality"] = settings.MONGODB_VECTOR_DIM
    return GoogleGenerativeAIEmbeddings(**kwargs)


def _require_firecrawl() -> FirecrawlClient:
    if not settings.FIRECRAWL_API_KEY:
        print("ERROR: FIRECRAWL_API_KEY is not configured.", file=sys.stderr)
        sys.exit(1)
    return FirecrawlClient(api_key=settings.FIRECRAWL_API_KEY)


def _require_mongo_collection() -> tuple[AsyncIOMotorClient, object]:
    if not settings.MONGODB_URI:
        print("ERROR: MONGODB_URI is not configured.", file=sys.stderr)
        sys.exit(1)
    if not settings.MONGODB_DB or not settings.MONGODB_COLLECTION:
        print(
            "ERROR: MONGODB_DB / MONGODB_COLLECTION is not configured.",
            file=sys.stderr,
        )
        sys.exit(1)
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    collection = client[settings.MONGODB_DB][settings.MONGODB_COLLECTION]
    return client, collection


def _print_result(result: IngestResult) -> None:
    print(f"status={result.status}")
    print(f"url={result.url}")
    print(f"chunk_count={result.chunk_count}")
    if result.message:
        print(f"message={result.message}")


def _exit_for_result(result: IngestResult) -> None:
    _print_result(result)
    if result.status != "ok":
        sys.exit(1)


async def _dry_run(url: str) -> None:
    if not is_allowed_url(url):
        # is_allowed_url 只回布林值；normalize_url 回 None 表示格式本身就
        # 剖析不出安全的 host（reason=malformed），非 None 則是格式沒問題、
        # 純粹網域不在白名單內（reason=not_allowed）。呼叫端（這支 CLI）
        # 自己判斷原因碼，whitelist.py 不需要為此多開一個介面。
        reason = "malformed" if normalize_url(url) is None else "not_allowed"
        print("status=rejected")
        print(f"url={url}")
        print(f"message=url_not_allowed reason={reason}")
        sys.exit(1)

    web_client = _require_firecrawl()
    try:
        scraped = await web_client.scrape(url)
    except Exception as exc:
        print("status=error")
        print(f"url={url}")
        print(f"message={exc}")
        sys.exit(1)

    if not scraped or not scraped.strip():
        print("status=empty")
        print(f"url={url}")
        print("message=Scrape returned empty content")
        sys.exit(1)

    chunks = split_text_to_chunks(scraped)
    if not chunks:
        print("status=empty")
        print(f"url={url}")
        print("message=No chunks after splitting")
        sys.exit(1)

    print("status=dry-run")
    print(f"url={url}")
    print(f"chunk_count={len(chunks)}")
    print("chunk_lengths:")
    for index, chunk in enumerate(chunks):
        print(f"  [{index}] {len(chunk)} chars")
    print("chunk_previews:")
    for index, chunk in enumerate(chunks):
        preview = " ".join(chunk.split())
        if len(preview) > 160:
            preview = preview[:160] + "…"
        print(f"  [{index}] {preview}")


async def _ingest(url: str, source_name: str | None) -> None:
    if not settings.GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY is not configured.", file=sys.stderr)
        sys.exit(1)

    web_client = _require_firecrawl()
    embeddings = _build_embeddings()
    client, collection = _require_mongo_collection()
    try:
        service = IngestService(
            web_client=web_client,
            embeddings=embeddings,
            collection=collection,
            text_field=settings.MONGODB_TEXT_FIELD,
            vector_field=settings.MONGODB_VECTOR_FIELD,
            vector_dim=(
                settings.MONGODB_VECTOR_DIM if settings.MONGODB_VECTOR_DIM > 0 else None
            ),
        )
        result = await service.ingest_url(url, source_name=source_name)
    finally:
        client.close()
    _exit_for_result(result)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "RAG 入庫：白名單 URL 經 Firecrawl scrape → chunk → embed → "
            "Mongo replace-by-url。"
        ),
    )
    parser.add_argument("--url", required=True, help="要入庫的 URL（須在白名單）")
    parser.add_argument(
        "--source-name",
        default=None,
        help="來源名稱（寫入 source_name 欄位）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="僅白名單檢查 + scrape + chunk，印長度與文字預覽，不寫入 Mongo",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    url = args.url.strip()
    if not url:
        print("ERROR: --url cannot be empty.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        await _dry_run(url)
    else:
        await _ingest(url, args.source_name)


if __name__ == "__main__":
    asyncio.run(main())
