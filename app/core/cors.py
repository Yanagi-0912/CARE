import os
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


# 解析 CORS 來源字串，支援逗號分隔的多個來源
def _parse_origins(raw: str) -> List[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def get_cors_origins() -> List[str]:
    """Return allowed CORS origins from env or defaults for local dev."""
    # 在環境變數中讀取 CORS 來源設定
    raw = os.getenv("CORS_ALLOW_ORIGINS", "")
    origins = _parse_origins(raw)
    if origins:
        return origins
    # 若環境變數未設定，則提供預設的本地開發來源
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def add_cors_middleware(app: FastAPI) -> None:
    """Attach CORS middleware to the FastAPI app."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
