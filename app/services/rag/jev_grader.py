"""CRAG 分級改問 TypeSafe Jev；Jev 失敗時才退回 Gemini。

分級只決定一件事：`correct` 用知識庫答，其餘（ambiguous／incorrect）直接網搜
（見 answer_service._apply_crag）。以前這一步是 Gemini（low thinking），
2026-09-14 量到中位數 1.26 秒；2026-09-22 這批量到中位數 1,385ms、P90 2,675ms、
最慢 10,960ms。Jev 是只回機率、不生成文字的分類模型，同一批（每則 5 段、
每段截 400 字）4 路並行送出，中位數 263ms、P90 320ms、最慢 615ms。

**使用者等待時間幾乎沒變**，別把上面的差距當成省下的秒數：
- 判 correct：分級與投機生成並行，使用者等 max(分級, 生成)，生成比較慢。
- 不是 correct：網搜要等查詢改寫，改寫與分級同時起跑，使用者等
  max(分級, 改寫)。2026-09-22 挑 16 則非 correct 三者同時跑（實測）：改寫中位數
  約 1.9 秒，13/16 則改寫比 Gemini 分級慢，換成 Jev 省下中位數 0 秒、平均
  66ms、最多 482ms。
換的理由是 Gemini 分級偶發的長尾（這批最慢 11 秒，會超過改寫）與少一次
Gemini 呼叫，不是中位數延遲。

2026-09-22 評測（117 組「問題＋精排後 5 段」，Gemini 分級當參考答案）：
- 題目：evals/rag/golden.jsonl 55 題＋evals/rag_route/agent_labels.jsonl 抽
  62 則 agent 實際送出的 rag_query（中文 50、英日越泰印各 4 抽樣，扣掉檢索
  為空的 8 則）。當時本機連不到 pgvector，檢索只有 BM25 那一腿，文件比線上
  雜，但分級器判的是「給定的文件夠不夠」，拿來比兩個分級器仍然成立。
- 只取 p(correct) 的門檻比用 argmax 好：argmax 時 Jev 比 Gemini 寬鬆，
  Gemini 判 ambiguous 的 26 則裡有 13 則 Jev 判 correct，correct／非 correct
  一致率只有 88%（其中「中午頭暈」配到日本護肝食物這類明顯答非所問）。
- 門檻 0.8 在全批：一致 113/117（96.6%）；Jev 判 correct、Gemini 判
  incorrect 0 則；Jev 判 correct、Gemini 判 ambiguous 3 則（中風前兆、
  生香蕉護腎的查核報告、心理補助詐騙簡訊）；Gemini 判 correct、Jev 不判 1 則
  （普拿疼劑量，p=0.72，會多等一次網搜）。
- 門檻是拿同一批挑的，所以另做 2-fold 交叉驗證（500 次隨機對半，一半挑門檻、
  一半量）：挑出的門檻中位數 0.81，測試那半一致率平均 95.1%、最差 5% 為
  89.8%，Jev 判 correct／Gemini 判 incorrect 平均 0%。0.8 取的是這個中位數，
  不是全批最佳值。

問法試過三種（一段一個 noul 取最大、整批一個 noul、整批一個 choice），交叉
驗證下差不多（93.7%／94.7%／95.1%），用 choice 是因為它順便給出 ambiguous 與
incorrect 的區分，log 與舊的 Gemini 分級可以直接對照。

失效方向：沒有金鑰、逾時、HTTP 錯誤一律改問 Gemini，Gemini 再失敗由
answer_service 的降級路徑（精排分數門檻）處理。不直接當成 incorrect 是因為
那等於外部服務一掛，所有問題都去網搜，每題多等 5～10 秒而且只有看 log 才知道。
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from langchain_core.documents import Document

from app.core.request_logging import log_stage
from app.services.rag.retrieval_grader import Grade, RetrievalGrader

logger = logging.getLogger(__name__)

JEV_URL = "https://api.typesafe.ai/v1/systemone"
# 釘版本而不用 jev-latest：上面的一致率與門檻是在 1.13.0 上量的。
JEV_MODEL = "jev-1.13.0"
# p(correct) 門檻，由來見模組說明（交叉驗證挑出的門檻中位數 0.81）。
CORRECT_THRESHOLD = 0.8
# 與 GeminiRetrievalGrader 預設相同，評測時 Jev 看到的也是截 400 字。
MAX_CHARS_PER_DOC = 400
# 實測最慢 615ms；2 秒約是它的 3 倍。逾時後還要再等 Gemini（中位數約 1.3 秒），
# 設更長只會讓 Jev 卡住時整體更慢。
TIMEOUT_SECONDS = 2.0

# 英文寫：官方文件說英文是它準確度最好的語言，問題與文件本身可以是任何語言。
# 評測（2026-09-22）用的就是這一段，改字要重跑評測。
QUESTION = {
    "grade": {
        "type": "choice",
        "instructions": (
            "`query` is a health question from an elderly user in Taiwan (it may be in any language). "
            "`documents` are passages retrieved from a medical knowledge base. "
            "Can an answer to `query` be written from `documents`?"
        ),
        "criteria": {
            "correct": "At least one document directly addresses the question and states enough to answer its main point.",
            "ambiguous": "The documents are on a related topic but do not state what the question asks, or only answer it partially or vaguely.",
            "incorrect": "The documents are about something else and cannot answer the question.",
        },
    }
}


def build_state(query: str, docs: list[Document]) -> dict:
    return {
        "query": query,
        "documents": [
            {
                "title": str(doc.metadata.get("original_title") or ""),
                "text": (doc.page_content or "").strip().replace("\n", " ")[:MAX_CHARS_PER_DOC],
            }
            for doc in docs
        ],
    }


def grade_from_probabilities(probabilities: dict[str, float]) -> Grade:
    if float(probabilities.get("correct", 0.0)) >= CORRECT_THRESHOLD:
        return Grade.CORRECT
    # 兩者都走網搜，區分只為了 log 能和 Gemini 的分級對照。
    if float(probabilities.get("ambiguous", 0.0)) >= float(probabilities.get("incorrect", 0.0)):
        return Grade.AMBIGUOUS
    return Grade.INCORRECT


class JevRetrievalGrader:
    """與 `GeminiRetrievalGrader` 同介面，Gemini 版放在 fallback。"""

    def __init__(
        self,
        *,
        api_key: str,
        fallback: RetrievalGrader,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._fallback = fallback
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def grade(self, query: str, docs: list[Document]) -> Grade:
        if not self._api_key:
            return await self._fallback.grade(query, docs)

        t0 = time.perf_counter()
        try:
            probabilities = await asyncio.wait_for(
                self._ask(query, docs), timeout=TIMEOUT_SECONDS
            )
            grade = grade_from_probabilities(probabilities)
        except Exception as e:  # noqa: BLE001
            log_stage(
                logger, "crag_grade_jev", outcome="fallback", error=type(e).__name__,
                ms=int((time.perf_counter() - t0) * 1000),
            )
            return await self._fallback.grade(query, docs)

        log_stage(
            logger, "crag_grade_jev", outcome=grade.value,
            p=round(float(probabilities.get("correct", 0.0)), 4),
            ms=int((time.perf_counter() - t0) * 1000),
        )
        return grade

    async def _ask(self, query: str, docs: list[Document]) -> dict[str, float]:
        response = await self._client.post(
            JEV_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"state": build_state(query, docs), "model": JEV_MODEL, "questions": QUESTION},
        )
        response.raise_for_status()
        probabilities = response.json()["answers"]["grade"]["probabilities"]
        if not isinstance(probabilities, dict):
            raise ValueError(f"unexpected probabilities: {type(probabilities)}")
        return probabilities
