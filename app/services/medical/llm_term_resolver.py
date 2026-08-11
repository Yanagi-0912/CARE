"""
科別／院所類型的 LLM 兜底解析：關鍵字表查不到時才會用到的第二層。

為什麼不整個改用 LLM、而是只當兜底：

1. 輸出是封閉集合。departments 只有 55 個部定專科值、type 只有 17 個，模型並不
   知道這兩份清單裡有什麼。放它自由作答會得到「大腸直腸外科」「消化內科」這種
   真實世界存在、但本資料庫必定查 0 筆的答案——失敗模式從「系統說看不懂」惡化成
   「系統說查過了但附近沒有」，後者更難察覺也更誤導使用者。因此這裡把候選值直接
   當成 JSON Schema 的 enum，模型只能選，不能造。
2. 確定性。表命中的路徑佔絕大多數流量，維持 0 延遲、可被單元測試逐條斷言；只有
   長尾說法才付一次 LLM 呼叫的成本，且結果會被快取，同一個新俗稱一輩子只呼叫一次。

安全性質沿用 department_matcher 的紅線：只做「科別名稱的別稱／身體部位」的同義
對應，遇到症狀描述一律回 UNKNOWN。由症狀推科別是醫療判斷，猜錯的代價是把可能
需要急診的人導去一般門診——這條線不因為改用 LLM 就放寬，而是寫進 prompt 並在
下方 _SAFETY_RULES 集中維護。

失敗一律降級成 None（＝維持原本「我看不懂這個科別」的回覆）。兜底層本身不該有
能力讓找院所的主流程壞掉，因此這裡吞掉所有例外，只留 log。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Protocol, Sequence

from langchain_core.messages import HumanMessage

from app.services.gemini import GeminiService
from app.services.medical.department_matcher import CANONICAL_DEPARTMENTS
from app.services.medical.facility_type_matcher import FACILITY_TYPE_CATEGORIES

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Services:LLMTermResolver]"

# 模型表達「我判斷不出來」的專用值。刻意放進 enum 而不是靠空字串或漏欄位，
# 這樣「不確定」是模型可以正大光明選擇的選項，而非一種輸出格式錯誤。
UNKNOWN = "UNKNOWN"

# 送進 LLM 的說法長度上限。超過這個長度的字串幾乎不可能是一個科別俗稱，
# 多半是模型把整句話塞進 department 參數了；這種輸入讓模型硬答只會提高
# 亂猜的機率（而且照 prompt 規則多半也該回 UNKNOWN），不如直接省下呼叫。
DEFAULT_MAX_INPUT_CHARS = 40

# 快取上限。俗稱的長尾再大也遠小於這個數字，設上限只是防止被惡意輸入灌爆記憶體。
DEFAULT_CACHE_SIZE = 512

_SAFETY_RULES = (
    "規則（必須嚴格遵守）：\n"
    "1. 只能從候選清單裡挑一個，不可自創清單以外的答案。\n"
    "2. 只有當使用者說的是『該項目的別稱、俗稱或所屬的次分類』時才對應。\n"
    "3. 使用者若是在描述症狀、疾病或身體不適（例如「肚子痛」「一直咳嗽」「發燒」"
    "「頭暈」「胸悶」），一律回 UNKNOWN。由症狀推斷該看哪一科屬於醫療判斷，"
    "猜錯會延誤就醫，不在你的任務範圍內。\n"
    "4. 只要無法確定，就回 UNKNOWN。回 UNKNOWN 永遠比猜錯好。\n"
)

_DEPARTMENT_INSTRUCTION = (
    "你的任務：把使用者口中的『科別說法』對應到台灣衛福部部定專科清單裡的一項。\n"
    "例如「大腸科」→「外科」（大腸直腸外科隸屬外科）、"
    "「心臟科」→「內科」（心臟內科隸屬內科）、「牙齒」→「牙科」。"
)

_FACILITY_TYPE_INSTRUCTION = (
    "你的任務：把使用者口中的『醫療院所類型說法』對應到清單裡的一項。\n"
    "例如「藥妝店」→「藥局」、「醫學中心」→「醫院」、「小兒科診所」→「診所」。"
)

# 民眾不會主動掛號的純檢驗科別。別名表刻意不收（見 department_matcher 模組
# 註解），LLM 兜底也一併排除——否則模型有機會把使用者導向一個掛不了號的科別。
_LAB_ONLY_DEPARTMENTS = frozenset({"解剖病理科", "臨床病理科", "病理科"})


class TermResolver(Protocol):
    """把一段自由說法解析成封閉集合裡的一個值；判斷不出來回 None。"""

    async def resolve(self, text: str) -> str | None: ...


def _build_schema(allowed: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "value": {
                "type": "string",
                "enum": [*allowed, UNKNOWN],
            }
        },
        "required": ["value"],
    }


class GeminiEnumTermResolver:
    """
    以 Gemini structured output 把說法對應到一組固定候選值。

    invoke 可注入，測試時不必碰真的 LLM（與 GeminiRetrievalGrader 同一個做法）。
    """

    def __init__(
        self,
        *,
        allowed: Sequence[str],
        instruction: str,
        gemini_service: GeminiService | None = None,
        invoke: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        cache_size: int = DEFAULT_CACHE_SIZE,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        label: str = "term",
    ) -> None:
        self._allowed = tuple(allowed)
        self._allowed_set = frozenset(self._allowed)
        self._instruction = instruction
        self._gemini = gemini_service
        self._invoke = invoke
        self._cache_size = cache_size
        self._max_input_chars = max_input_chars
        self._label = label
        self._schema = _build_schema(self._allowed)
        # 同時快取命中與未命中：「這個詞問過了，模型也答不出來」與「這個詞對應到
        # 外科」一樣值錢，否則同一個看不懂的詞每次都會再花一次呼叫。
        self._cache: dict[str, str | None] = {}

    async def resolve(self, text: str) -> str | None:
        key = (text or "").strip()
        if not key:
            return None
        if len(key) > self._max_input_chars:
            logger.info(
                f"{LOGGER_HEADER_TEXT} %s 說法過長（%s 字），略過 LLM 兜底",
                self._label,
                len(key),
            )
            return None

        if key in self._cache:
            cached = self._cache[key]
            logger.info(
                f"{LOGGER_HEADER_TEXT} %s 快取命中，%r → %r",
                self._label,
                key,
                cached,
            )
            return cached

        value = await self._classify(key)
        self._remember(key, value)
        return value

    async def _classify(self, text: str) -> str | None:
        prompt = (
            f"{self._instruction}\n\n"
            f"{_SAFETY_RULES}\n"
            f"候選清單：{'、'.join(self._allowed)}\n\n"
            f"使用者說法：{text}"
        )
        try:
            raw = await self._call(prompt)
        except Exception:
            # 兜底層失效不該讓找院所整條流程斷掉，降級成「表也查不到」的原本行為。
            logger.warning(
                f"{LOGGER_HEADER_TEXT} %s LLM 兜底呼叫失敗，降級為解析失敗，text=%r",
                self._label,
                text,
                exc_info=True,
            )
            return None

        value = raw.get("value") if isinstance(raw, dict) else raw
        value = (str(value) if value is not None else "").strip()

        if value == UNKNOWN or not value:
            logger.info(
                f"{LOGGER_HEADER_TEXT} %s LLM 判定為 UNKNOWN，text=%r", self._label, text
            )
            return None
        # enum 理論上已經擋掉清單外的值，這裡再驗一次：schema 的強制力取決於
        # 模型與 SDK 實作，而放行一個資料庫沒有的值會讓查詢靜默回 0 筆。
        if value not in self._allowed_set:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} %s LLM 回傳候選清單以外的值 %r，視為解析失敗",
                self._label,
                value,
            )
            return None

        logger.info(
            f"{LOGGER_HEADER_TEXT} %s LLM 兜底成功，%r → %r", self._label, text, value
        )
        return value

    async def _call(self, prompt: str) -> dict[str, Any]:
        if self._invoke is not None:
            return await self._invoke(prompt)
        if self._gemini is None:
            raise RuntimeError(
                "GeminiEnumTermResolver requires gemini_service or invoke"
            )
        structured = self._gemini.chat_model.with_structured_output(
            self._schema,
            method="json_schema",
        )
        result = await structured.ainvoke([HumanMessage(content=prompt)])
        if not isinstance(result, dict):
            raise ValueError(f"unexpected resolver payload: {type(result)}")
        return result

    def _remember(self, key: str, value: str | None) -> None:
        if self._cache_size <= 0:
            return
        if len(self._cache) >= self._cache_size:
            # dict 保留插入順序，淘汰最早寫入的那筆即可（FIFO）。俗稱的分布很穩定，
            # 不值得為了 LRU 的那點命中率差異多引入一個資料結構。
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = value


def build_department_resolver(
    *,
    gemini_service: GeminiService | None = None,
    invoke: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> GeminiEnumTermResolver:
    """科別兜底解析器；候選值即資料庫實際存在的部定專科（扣掉純檢驗科別）。"""
    allowed = sorted(CANONICAL_DEPARTMENTS - _LAB_ONLY_DEPARTMENTS)
    return GeminiEnumTermResolver(
        allowed=allowed,
        instruction=_DEPARTMENT_INSTRUCTION,
        gemini_service=gemini_service,
        invoke=invoke,
        label="科別",
    )


def build_facility_type_resolver(
    *,
    gemini_service: GeminiService | None = None,
    invoke: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> GeminiEnumTermResolver:
    """院所類型兜底解析器；候選值是三個分類，不是資料庫的 17 個 type 值。"""
    allowed = sorted(FACILITY_TYPE_CATEGORIES)
    return GeminiEnumTermResolver(
        allowed=allowed,
        instruction=_FACILITY_TYPE_INSTRUCTION,
        gemini_service=gemini_service,
        invoke=invoke,
        label="院所類型",
    )
