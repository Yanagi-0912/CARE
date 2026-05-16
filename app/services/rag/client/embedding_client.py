from typing import List

import httpx

from app.core.config import settings


async def embed_query(text: str) -> List[float]:
    return await _embed_text(text, "RETRIEVAL_QUERY")


async def embed_document(text: str) -> List[float]:
    return await _embed_text(text, "RETRIEVAL_DOCUMENT")


async def _embed_text(text: str, task_type: str) -> List[float]:
    if not text or not text.strip():
        raise ValueError("embedding text cannot be empty")

    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise ValueError("缺少 GEMINI_API_KEY，請在 .env 設定")

    model_name = settings.EMBEDDING_MODEL
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:embedContent"
    )
    payload = {
        "content": {"parts": [{"text": text}]},
        "taskType": task_type,
    }
    if settings.MONGODB_VECTOR_DIM > 0:
        payload["outputDimensionality"] = settings.MONGODB_VECTOR_DIM

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, params={"key": api_key}, json=payload)
    except httpx.RequestError as exc:
        raise ValueError(f"Gemini embedding 請求失敗: {exc}") from exc

    if resp.status_code != 200:
        raise ValueError(
            f"Gemini embedding 失敗: status={resp.status_code}, body={resp.text}"
        )

    data = resp.json()
    emb = data.get("embedding")
    if isinstance(emb, dict) and isinstance(emb.get("values"), list):
        values = [float(x) for x in emb["values"]]
        expected_dim = settings.MONGODB_VECTOR_DIM
        if expected_dim > 0 and len(values) != expected_dim:
            raise ValueError(
                "Embedding 維度不一致: "
                f"config={expected_dim}, actual={len(values)}"
            )
        return values

    raise ValueError(f"無法解析 embedding 回應: {data}")
