import os
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def get_cors_origins() -> List[str]:
    """
    取得允許的 CORS 來源清單（支援以逗號分隔的多個來源）
    """
    raw = os.getenv("CORS_ALLOW_ORIGINS", "")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if origins:
        return origins
    return [
        "http://localhost:5173", 
        "http://127.0.0.1:5173"
    ]


def add_cors_middleware(app: FastAPI) -> None:
    """設定 CORS 中間件"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
