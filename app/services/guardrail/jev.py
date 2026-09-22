"""Guardrail 的 LLM 那一段改問 TypeSafe Jev；Jev 失敗時才退回 Gemini。

只有本地模型沒把握的訊息會走到這裡（cascade.py）。以前這一步是 Gemini，
線上 p50 2,036ms；Jev 是只回機率、不生成文字的分類模型，2026-09-22 從本機
依序送 40 則量到中位數 259ms、P90 303ms、最慢 634ms。

同一天用 evals/guardrail 的 holdout（2,066 則）量「本地＋Jev」：健康問題被
判成不相關（漏判，會讓使用者拿不到知識庫答案）4/1,309，全是外語、中文 0；
非健康被放行 14.5%，放行錯了只是多掛一個工具給 agent。只問 Jev、不用本地
模型時漏判 3.7%，所以本地模型留著。資料集標註是 Gemini 產生的，沒有重跑
Gemini 對照；以 Gemini 為準，換成 Jev 等於多漏這 4 則外語句子。

急迫度沒有換：要做到 0 漏判，門檻得壓到 0.01，一般訊息有 28% 會誤出紅卡、
通報家人；紅卡上的說明句 Jev 也生不出來。

失效方向：沒有金鑰、逾時、HTTP 錯誤一律改問 Gemini（GuardrailService），
Gemini 再失敗才 fail-open。不直接 fail-open 是因為外部服務一掛，所有升級的
訊息都會被掛上 RAG 工具，那是靜默退化、看 log 才知道。
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.core.request_logging import log_stage
from app.services.guardrail.service import GuardrailService, is_location_message

logger = logging.getLogger(__name__)

JEV_URL = "https://api.typesafe.ai/v1/systemone"
# 釘版本而不用 jev-latest：下面的門檻與上面的漏判數字是在 1.13.0 上量的，
# alias 換版時答案可能不同（官方文件 Models → Aliases 的建議）。
JEV_MODEL = "jev-1.13.0"
# 評測時用的就是 0.5；沒有另外調，因為 holdout 是唯一的量測集，拿它調門檻
# 再拿它報數字會高估。
ALLOW_THRESHOLD = 0.5
# 實測最慢 634ms；2 秒約是它的 3 倍。逾時後還要再等 Gemini（約 2 秒），
# 所以不設更長——更長只會讓 Jev 卡住時整體更慢。
TIMEOUT_SECONDS = 2.0

# 英文寫：官方文件說英文是它準確度最好的語言，訊息本身可以是任何語言。
# 評測（2026-09-22）用的就是這一段，改字要重跑評測。
QUESTION = {
    "health": {
        "type": "noul",
        "instructions": (
            "Is `message` about health or medicine, or about a healthcare-related scam? "
            "The message may be in any language."
        ),
        "criteria": {
            "true": (
                "The message is about health, medical care, the body, symptoms, illness, "
                "medication, nutrition, exercise/fitness, mental health, or which clinic or "
                "department to visit; or it is about a medical scam (fake drugs, fake doctors "
                "or hospitals, fake national-health-insurance texts, miracle-cure supplements, "
                "requests for money or link clicks in the name of medical tests, insurance "
                "claims or health insurance)."
            ),
            "false": (
                "The message is about something else: small talk, greetings, shopping, travel, "
                "money, technology, work, school, weather, entertainment. A passing mention of "
                "a hospital or the body does not make it a health topic when the question is "
                "about something else."
            ),
        },
    }
}


class JevGuardrailService:
    """與 `GuardrailService` 同介面，放在 CascadeGuardrailService 的 fallback 位置。"""

    def __init__(
        self,
        *,
        api_key: str,
        fallback: GuardrailService,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._fallback = fallback
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def allow_rag_tool(self, user_text: str) -> bool:
        if is_location_message(user_text):
            return False
        if not self._api_key:
            return await self._fallback.allow_rag_tool(user_text)

        t0 = time.perf_counter()
        try:
            probability = await asyncio.wait_for(self._ask(user_text), timeout=TIMEOUT_SECONDS)
        except Exception as e:  # noqa: BLE001
            log_stage(
                logger, "guardrail_jev", outcome="fallback", error=type(e).__name__,
                ms=int((time.perf_counter() - t0) * 1000),
            )
            return await self._fallback.allow_rag_tool(user_text)

        allow = probability >= ALLOW_THRESHOLD
        log_stage(
            logger, "guardrail_jev", outcome="allow" if allow else "deny",
            p=round(probability, 4), ms=int((time.perf_counter() - t0) * 1000),
        )
        return allow

    async def _ask(self, user_text: str) -> float:
        response = await self._client.post(
            JEV_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"state": {"message": user_text}, "model": JEV_MODEL, "questions": QUESTION},
        )
        response.raise_for_status()
        return float(response.json()["answers"]["health"]["noul"])
