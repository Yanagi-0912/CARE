"""判斷一則搜尋結果是不是真的在講某個藥，並產出中性摘要。

形狀刻意逐條比照 `app/services/rag/retrieval_grader.py`：SCHEMA 常數、Protocol、
`invoke_*` 注入點三件套。同一個專案裡兩個做同一類事情的元件長得一樣，維護的人
只需要理解一次。

**行為上有一處與 `retrieval_grader` 刻意相反**：那支的呼叫端在失敗時降級為
「照常生成」，因為使用者正在等答案；這支的呼叫端必須 fail closed，因為主動推播
沒有人在等，沒推遠比推錯好（design.md 決策 4）。為了讓呼叫端有能力 fail closed，
本模組 SHALL NOT 吞掉任何例外、SHALL NOT 在輸出不合法時回一個預設值。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, NamedTuple, Protocol

from langchain_core.messages import HumanMessage

from app.services.gemini import GeminiService

logger = logging.getLogger(__name__)

CONCERN_KINDS: tuple[str, ...] = ("recall", "safety", "supply", "education", "none")

NEWS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_about_this_drug": {"type": "boolean"},
        "concern_kind": {"type": "string", "enum": list(CONCERN_KINDS)},
        "summary": {"type": "string"},
    },
    "required": ["is_about_this_drug", "concern_kind", "summary"],
}

# 送進模型的內文上限。公告全文可能上萬字，而判定「這在不在講這個藥」與寫一句
# 摘要，用不到那麼多——多送的部分是純成本。
DEFAULT_MAX_CHARS = 2000


class NewsJudgement(NamedTuple):
    is_about_this_drug: bool
    concern_kind: str
    summary: str


class NewsGrader(Protocol):
    async def judge(self, drug_key: str, title: str, text: str) -> NewsJudgement: ...


def _build_prompt(drug_key: str, title: str, text: str) -> str:
    return (
        "你是藥品消息判定器。根據「藥品名稱」與「消息內容」，判斷這則消息是否\n"
        "確實在講這個藥品，並寫一句摘要。\n\n"
        "欄位規則：\n"
        "- is_about_this_drug：這則消息是否確實在講這個藥品本身。名稱相近但\n"
        "  實為另一種藥時為 false。不確定時為 false。\n"
        "- concern_kind：recall（回收／下架）、safety（安全警訊、副作用新訊）、\n"
        "  supply（供應短缺）、education（一般衛教）、none（不構成消息）。\n"
        "- summary：一句話摘要。\n\n"
        "摘要的硬性規則：\n"
        "1. 必須是中性的第三人稱敘述（例如「食藥署公告某批號回收」）。\n"
        "2. 不得使用「您」「你」「你的藥」等第二人稱或個人化說法。\n"
        "3. 不得包含任何行動建議，尤其不得出現停藥、換藥、改吃、自行調整\n"
        "   劑量、減量、加量這類內容。\n"
        "4. 寫給高齡讀者，用詞平白，不使用專業縮寫。\n"
        "5. 內容必須來自消息本身，不得補充消息裡沒有的資訊。\n\n"
        f"藥品名稱：{drug_key}\n\n"
        f"消息標題：{title}\n\n"
        f"消息內容：\n{text or '(無)'}"
    )


def parse_judgement(raw: Any) -> NewsJudgement:
    """把模型輸出轉成 `NewsJudgement`；任何不合法之處一律拋 `ValueError`。

    不合法的輸出與「判定為不相關」是兩件不同的事，必須分得開：前者代表這次判定
    沒有發生（呼叫端要 fail closed），後者是一個有效的判定結果。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"unexpected judgement payload: {type(raw)}")

    missing = [key for key in NEWS_SCHEMA["required"] if key not in raw]
    if missing:
        raise ValueError(f"judgement missing fields: {missing}")

    concern_kind = str(raw["concern_kind"]).strip().lower()
    if concern_kind not in CONCERN_KINDS:
        raise ValueError(f"unknown concern_kind: {raw['concern_kind']!r}")

    return NewsJudgement(
        is_about_this_drug=bool(raw["is_about_this_drug"]),
        concern_kind=concern_kind,
        summary=str(raw["summary"] or "").strip(),
    )


class GeminiNewsGrader:
    """以 Gemini structured output 判定消息與藥品的相關性。"""

    def __init__(
        self,
        gemini_service: GeminiService | None = None,
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        invoke_judge: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._gemini = gemini_service
        self._max_chars = max_chars
        self._invoke_judge = invoke_judge

    async def judge(self, drug_key: str, title: str, text: str) -> NewsJudgement:
        body = (text or "")[: self._max_chars]
        raw = await self._call(_build_prompt(drug_key, title, body))
        return parse_judgement(raw)

    async def _call(self, prompt: str) -> Any:
        if self._invoke_judge is not None:
            return await self._invoke_judge(prompt)
        if self._gemini is None:
            raise RuntimeError(
                "GeminiNewsGrader requires gemini_service or invoke_judge"
            )
        structured = self._gemini.chat_model.with_structured_output(
            NEWS_SCHEMA,
            method="json_schema",
        )
        return await structured.ainvoke([HumanMessage(content=prompt)])
