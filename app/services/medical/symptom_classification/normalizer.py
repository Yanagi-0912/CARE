#把使用者的口語症狀說法對應到對照表裡實際存在的條目。


from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable, Protocol, Sequence

from langchain_core.messages import HumanMessage

from app.services.gemini import GeminiService
from app.services.medical.symptom_classification.vector_index import (
    AUTO_ACCEPT_SCORE,
    MIN_MATCH_SCORE,
    TOP_K,
    Match,
    SymptomVectorIndex,
)

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Services:SymptomNormalizer]"

UNKNOWN = "UNKNOWN"

# 超過這個長度幾乎不可能是單一症狀說法，多半是整段病史。讓模型硬答只會提高
# 亂猜的機率，不如省下呼叫、直接走保底。
DEFAULT_MAX_INPUT_CHARS = 60

DEFAULT_CACHE_SIZE = 512


# 孩童指涉。用途只有一個：成人帳號幫小孩問時，年齡欄位是家長的，光看年齡會把
# 兒科濾掉。
#
# 為什麼這一條字面比對留著、症狀比對那一整層卻拆掉：兩者的失敗代價差一個量級。
# 這裡漏收只是少一個候選科別，症狀比對漏收則是整張卡答非所問；而且孩童指涉的
# 說法是封閉的一小組（小孩、寶寶、兒子、孫女…），不像症狀說法是開放集合。
_CHILD_REFERENCE_RE = re.compile(
    r"小孩|孩子|小朋友|寶寶|嬰兒|幼兒|兒子|女兒|孫子|孫女|新生兒|小baby|"
    r"我家(?:弟弟|妹妹)|讀幼稚園|讀國小"
)


def mentions_child(text: str) -> bool:
    """訊息是否在講一個孩童。用於決定要不要保留兒科候選。"""
    return bool(text and _CHILD_REFERENCE_RE.search(text))


class SymptomResolver(Protocol):
    async def resolve(self, text: str) -> str | None: ...


class SymptomNormalizer:
    """口語症狀 → 對照表條目。目前唯一路徑是 LLM，enum 約束在封閉集合上。"""

    def __init__(
        self,
        *,
        table_terms: tuple[str, ...],
        vector_index: SymptomVectorIndex | None = None,
        embed_query: Callable[[str], Awaitable[list[float]]] | None = None,
        gemini_service: GeminiService | None = None,
        invoke: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        cache_size: int = DEFAULT_CACHE_SIZE,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        auto_accept_score: float = AUTO_ACCEPT_SCORE,
        min_match_score: float = MIN_MATCH_SCORE,
        top_k: int = TOP_K,
    ) -> None:
        self._terms = tuple(table_terms)
        self._term_set = frozenset(self._terms)
        self._index = vector_index
        self._embed_query = embed_query
        self._gemini = gemini_service
        self._invoke = invoke
        self._cache_size = cache_size
        self._max_input_chars = max_input_chars
        self._auto_accept_score = auto_accept_score
        self._min_match_score = min_match_score
        self._top_k = top_k
        self._cache: dict[str, str | None] = {}
        if vector_index is None or embed_query is None:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 未提供向量索引，比對層退回 LLM 全表兜底"
            )

    def _build_schema(self, candidates: Sequence[str] | None = None) -> dict[str, Any]:
        """輸出結構裡沒有科別欄位——模型沒有那個輸出通道。

        candidates 是向量召回的結果；省略時退回全表。無論哪一種，enum 都是
        封閉集合，模型不可能輸出集合以外的東西（決策 5）。
        """
        allowed = self._terms if candidates is None else tuple(candidates)
        return {
            "type": "object",
            "properties": {
                "symptom": {"type": "string", "enum": [*allowed, UNKNOWN]}
            },
            "required": ["symptom"],
        }

    async def resolve(self, text: str) -> str | None:
        cleaned = (text or "").strip()
        if not cleaned:
            return None

        if len(cleaned) > self._max_input_chars:
            logger.info(
                f"{LOGGER_HEADER_TEXT} 說法過長（%d 字），略過比對", len(cleaned)
            )
            return None

        if cleaned in self._cache:
            return self._cache[cleaned]

        value = await self._resolve_uncached(cleaned)
        self._remember(cleaned, value)
        return value

    async def _resolve_uncached(self, text: str) -> str | None:
        matches = await self._vector_matches(text)

        if not matches:
            # 沒有索引或取向量失敗。全表 enum 交 LLM——降級，不是中斷。
            return await self._classify(text, self._terms)

        top = matches[0]
        if top.score >= self._auto_accept_score:
            logger.info(
                f"{LOGGER_HEADER_TEXT} 向量直接命中，%r → %r（%.4f）",
                text,
                top.term,
                top.score,
            )
            return top.term

        if top.score < self._min_match_score:
            logger.info(
                f"{LOGGER_HEADER_TEXT} 最高分 %.4f 低於門檻 %.2f，視為未命中，text=%r",
                top.score,
                self._min_match_score,
                text,
            )
            return None

        # 中間帶。向量說「像這幾個」，由 LLM 在這個封閉小集合裡決選——
        # 「眼壓高」正是靠這條路徑從 top1「高血壓」救回「青光眼」。
        candidates = tuple(m.term for m in matches)
        logger.info(
            f"{LOGGER_HEADER_TEXT} 最高分 %.4f 落在中間帶，交 LLM 決選：%s",
            top.score,
            "、".join(candidates),
        )
        return await self._classify(text, candidates)

    async def _vector_matches(self, text: str) -> tuple[Match, ...]:
        if self._index is None or self._embed_query is None:
            return ()
        try:
            vector = await self._embed_query(text)
        except Exception:  # noqa: BLE001
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 取查詢向量失敗，降級為全表 LLM 兜底，text=%r",
                text,
                exc_info=True,
            )
            return ()
        if not vector:
            return ()
        try:
            return self._index.search(vector, k=self._top_k)
        except ValueError:
            logger.error(
                f"{LOGGER_HEADER_TEXT} 查詢向量與索引維度不符，降級為全表兜底",
                exc_info=True,
            )
            return ()

    async def _classify(self, text: str, candidates: Sequence[str]) -> str | None:
        prompt = (
            "你的任務：把使用者描述的身體不適，對應到候選清單裡的一個症狀條目。\n\n"
            "規則（必須嚴格遵守）：\n"
            "1. 只能從候選清單裡挑一個，不可自創清單以外的答案。\n"
            "2. 只做「同一個症狀的不同說法」的對應，例如「肚子痛」→「腹痛」。\n"
            "3. 不可做推論或診斷。使用者說「肚子痛」時，不可對應到「腸胃炎」"
            "或任何疾病名稱——那是醫療判斷，不在你的任務範圍內。\n"
            "4. 描述含糊、同時提到多個不相關症狀、或你無法確定時，一律回 "
            f"{UNKNOWN}。回 {UNKNOWN} 永遠比猜錯好。\n\n"
            f"候選清單：{'、'.join(candidates)}\n\n"
            f"使用者描述：{text}"
        )
        try:
            raw = await self._call(prompt, candidates)
        except Exception:
            # 兜底層失效不該讓整條流程斷掉，降級成「表也查不到」，由服務層走保底。
            logger.warning(
                f"{LOGGER_HEADER_TEXT} LLM 兜底呼叫失敗，降級為解析失敗，text=%r",
                text,
                exc_info=True,
            )
            return None

        value = raw.get("symptom") if isinstance(raw, dict) else raw
        value = (str(value) if value is not None else "").strip()

        if not value or value == UNKNOWN:
            return None
        # enum 的強制力取決於模型與 SDK 實作，這裡再驗一次：放行清單外的值會讓
        # 後續查表靜默落空，退化成「系統說查過了但沒有結果」。
        if value not in set(candidates):
            logger.warning(
                f"{LOGGER_HEADER_TEXT} LLM 回傳候選清單以外的值 %r，視為解析失敗",
                value,
            )
            return None

        logger.info(f"{LOGGER_HEADER_TEXT} LLM 兜底成功，%r → %r", text, value)
        return value

    async def _call(self, prompt: str, candidates: Sequence[str]) -> dict[str, Any]:
        if self._invoke is not None:
            return await self._invoke(prompt)
        if self._gemini is None:
            raise RuntimeError("SymptomNormalizer requires gemini_service or invoke")
        structured = self._gemini.chat_model.with_structured_output(
            self._build_schema(candidates),
            method="json_schema",
        )
        result = await structured.ainvoke([HumanMessage(content=prompt)])
        if not isinstance(result, dict):
            raise ValueError(f"unexpected normalizer payload: {type(result)}")
        return result

    def _remember(self, key: str, value: str | None) -> None:
        if self._cache_size <= 0:
            return
        if len(self._cache) >= self._cache_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = value
