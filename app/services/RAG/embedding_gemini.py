from typing import List
import httpx
from app.core.config import settings


async def embed_query(text: str) -> List[float]:
    return await _embed_text(text, "RETRIEVAL_QUERY")


async def embed_document(text: str) -> List[float]:
    return await _embed_text(text, "RETRIEVAL_DOCUMENT")


async def _embed_text(text: str, task_type: str) -> List[float]:#內部使用，不對外暴露 所以前面會有個  
    #這叫做helper function
    api_key = settings.GEMINI_API_KEY 
    if not api_key:
        raise ValueError("缺少 GEMINI_API_KEY，請在 .env 設定")

    model_name = settings.EMBEDDING_MODEL

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_name}:embedContent"
    )
    payload = {#送給給gemini的json
        "content": {"parts": [{"text": text}]},# embed 函式會把你要問的問提放到這里來
        "taskType": task_type,#看前面那一個是呼叫document 還是query
    }
    if settings.MONGODB_VECTOR_DIM > 0:
        payload["outputDimensionality"] = settings.MONGODB_VECTOR_DIM #確定mongodb

    async with httpx.AsyncClient(timeout=120.0) as client:#發http 請求
        resp = await client.post(url, params={"key": api_key}, json=payload)#等api 回應

    if resp.status_code != 200:#沒有回傳就報錯
        raise ValueError(
            f"Gemini embedding 失敗: status={resp.status_code}, body={resp.text}"
        )

    data = resp.json()#把回傳的json 轉換成python 的dict

    emb = data.get("embedding")#取api 回傳的embedding 的值
    if isinstance(emb, dict) and isinstance(emb.get("values"), list):
        return [float(x) for x in emb["values"]]

    raise ValueError(f"無法解析 embedding 回應: {data}")


