# EMBEDDING_MODEL 需與建 Mongo 向量庫時相同
import os
from typing import List, Optional

import httpx


async def embed_text(
    text: str,
    *,
    task_type: str = "RETRIEVAL_QUERY",
    model_name: Optional[str] = None,
) -> List[float]:
    try:
        from app.core.config import settings

        api_key = settings.GEMINI_API_KEY
    except Exception:
        api_key = os.getenv("GEMINI_API_KEY")

    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("缺少 GEMINI_API_KEY，請在 .env 設定。")

    # v1beta embedContent 請用 gemini-embedding-001（可對齊 3072 維）；text-embedding-004 常出現 404
    model_name = model_name or os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:embedContent"
    )
    payload = {
        "content": {"parts": [{"text": text}]},
        "taskType": task_type,
    }
    dim_raw = os.getenv("MONGODB_VECTOR_DIM", "").strip()
    if dim_raw.isdigit():
        payload["outputDimensionality"] = int(dim_raw)

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, params={"key": api_key}, json=payload)

    if resp.status_code != 200:
        raise ValueError(
            f"Gemini embedding 失敗: status={resp.status_code}, body={resp.text}"
        )

    data = resp.json()

    emb = data.get("embedding")
    if isinstance(emb, dict) and isinstance(emb.get("values"), list):
        return [float(x) for x in emb["values"]]

    embeddings = data.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        if isinstance(first, dict) and isinstance(first.get("values"), list):
            return [float(x) for x in first["values"]]

    raise ValueError(f"無法解析 embedding 回應: {data}")
