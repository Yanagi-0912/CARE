"""症狀比對中間帶的決選改問 TypeSafe Jev；Jev 失敗時才退回 Gemini。

只有向量最高分落在中間帶（MIN_MATCH_SCORE ≤ 分數 < AUTO_ACCEPT_SCORE）的說法
會走到這裡，從向量召回的 top-5 裡挑一條或回 UNKNOWN。全表兜底（沒有索引、
取向量失敗）仍走 Gemini：那是 882 選 1，沒有量過 Jev。

2026-09-22 的評測：自己寫 517 句口語說法（長輩口吻、台語漢字、英越印泰日），
留下向量分數落在中間帶的 267 句，只看 top-5 候選標註正解（同一症狀的不同
說法才算對，不做推論；沒有對的候選就是 UNKNOWN），標完才跑模型。其中 200 句
兩邊都跑，另 67 句只跑 Jev、拿來選門檻。句子、候選、正解與兩邊的原始回答在
evals/symptom_normalizer/dataset.jsonl（gemini 為 null 的就是那 67 句）：

    Gemini（gemini-3.8-flash，原本的 _classify）  正確 86.5%  錯條目 0.5%  該答卻 UNKNOWN 13.0%
    Jev，p(UNKNOWN) ≥ 0.1 就回 UNKNOWN            正確 86.0–87.0%  錯條目 2.0–2.5%  該答卻 UNKNOWN 11.0–11.5%
    Jev，直接取最高機率                            正確 92.5%  錯條目 7.0%

Jev 同一批送兩次，約 4% 的句子答案會翻，所以列兩次的範圍。錯條目比 UNKNOWN
糟：UNKNOWN 走保底科別，錯條目會把人送到錯的科。錯條目裡換了科的，200 句中
Jev 3–4 句、Gemini 1 句；Jev 最要注意的一句是「嘴巴歪一邊」→「顎顏面不對稱」
（牙科，Gemini 回 UNKNOWN）。急迫度判斷擋在 agent 之前，它的資料集有收「嘴歪
一邊」這類中風句子，但這一句單獨出現會不會被攔下沒有驗證。

為什麼看 p(UNKNOWN) 不看 confidence：同義條目多（關節痛／關節疼痛／關節酸痛），
機率會分散在幾個都對的選項上，confidence 很低但答案是對的；confidence ≥ 0.7
時錯條目降到 2%，正確率卻掉到 69.5%。p(UNKNOWN) 不受同義詞分票影響。門檻 0.1
是在只跑 Jev 的 67 句上挑的（0.05／0.1／0.15 的錯條目 0%／1.5%／3.0%），再到
200 句上報數字，沒有拿報數字的那批調門檻。

延遲：Jev 依序送 267 句中位數 257ms、P90 311ms、最慢 643ms；Gemini 依序 200 句
中位數 2,733ms、P90 5,702ms、最慢 25,390ms。

失效方向：沒有金鑰、逾時、HTTP 錯誤、回了候選以外的值，一律改問 Gemini。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Sequence

import httpx

from app.core.request_logging import log_stage

logger = logging.getLogger(__name__)

UNKNOWN = "UNKNOWN"

JEV_URL = "https://api.typesafe.ai/v1/systemone"
# 釘版本而不用 jev-latest：上面的數字與門檻是在 1.13.0 上量的。
JEV_MODEL = "jev-1.13.0"
# 選法見模組說明。改了要重跑評測。
UNKNOWN_THRESHOLD = 0.1
# 實測最慢 643ms；2 秒約是它的 3 倍。逾時後還要再等 Gemini（中位數約 2.7 秒），
# 設更長只會讓 Jev 卡住時整體更慢。
TIMEOUT_SECONDS = 2.0

# 英文寫：官方文件說英文是它準確度最好的語言；選項是表內的中文條目。
# 評測（2026-09-22）用的就是這一段，改字要重跑評測。
INSTRUCTIONS = (
    "`symptom` is how a user (often an elderly person in Taiwan) describes a health complaint. "
    "It may be colloquial Mandarin, Taiwanese Hokkien written in Chinese characters, or another "
    "language. Pick the option that names the same complaint in different words. Do not diagnose: "
    "do not pick a disease that the complaint might be a sign of, unless the user named that "
    "disease. Pick UNKNOWN if the text is not a health complaint, is vague, lists several "
    "unrelated complaints, or if no option names the same complaint."
)
UNKNOWN_CRITERION = (
    "None of the options names the same complaint as `symptom`, or `symptom` is not a single "
    "health complaint."
)

Fallback = Callable[[str, Sequence[str]], Awaitable[str | None]]


def build_question(candidates: Sequence[str]) -> dict:
    criteria: dict[str, str | None] = {term: None for term in candidates}
    criteria[UNKNOWN] = UNKNOWN_CRITERION
    return {"term": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": criteria}}


class JevSymptomChooser:
    """中間帶決選。回傳候選中的一條，或 None（UNKNOWN，由服務層走保底）。"""

    def __init__(self, *, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def choose(
        self, text: str, candidates: Sequence[str], fallback: Fallback
    ) -> str | None:
        if not self._api_key:
            return await fallback(text, candidates)

        t0 = time.perf_counter()
        try:
            answer = await asyncio.wait_for(self._ask(text, candidates), timeout=TIMEOUT_SECONDS)
            choice = answer["choice"]
            p_unknown = float(answer.get("probabilities", {}).get(UNKNOWN, 0.0))
            if choice != UNKNOWN and choice not in candidates:
                raise ValueError(f"choice outside candidates: {choice!r}")
        except Exception as e:  # noqa: BLE001
            log_stage(
                logger, "symptom_jev", outcome="fallback", error=type(e).__name__,
                ms=int((time.perf_counter() - t0) * 1000),
            )
            return await fallback(text, candidates)

        term = None if choice == UNKNOWN or p_unknown >= UNKNOWN_THRESHOLD else choice
        log_stage(
            logger, "symptom_jev", outcome="term" if term else "unknown", choice=choice,
            p_unknown=round(p_unknown, 4), ms=int((time.perf_counter() - t0) * 1000),
        )
        return term

    async def _ask(self, text: str, candidates: Sequence[str]) -> dict:
        response = await self._client.post(
            JEV_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "state": {"symptom": text},
                "model": JEV_MODEL,
                "questions": build_question(candidates),
            },
        )
        response.raise_for_status()
        return response.json()["answers"]["term"]
