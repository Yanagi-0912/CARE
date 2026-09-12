"""查詢改寫：一次呼叫產出知識庫重查與網搜兩路要用的查詢。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from app.services.gemini import GeminiService

# 改寫呼叫用的 thinking 等級。gemini-3.8-flash 關不掉 thinking：官方文件只
# 列 low／medium／high（預設 medium），`thinking_level="minimal"` 會回 400；
# `thinking_budget` 已不在 Gemini 3 的文件裡，實測行為也不一致。low 是有文件
# 保證的最低檔。
#
# 2026-09-12 實測（結構化輸出、同一組 prompt）：預設 2.1-4.6 秒，low 1.2-3.3
# 秒（共 9 次），6 題的改寫內容兩者幾乎相同。支援 minimal 的 3.6-flash／
# 3.5-flash-lite 更快（0.9-1.7 秒），但中文病名不標準（「持續性性興奮亢進症」
# 「持續性性喚起症候群」），而 gov.tw 搜尋吃的就是標準病名，所以不換模型。
REWRITE_THINKING_LEVEL = "low"

REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kb_query": {"type": "string"},
        "zh_terms": {"type": "string"},
        "en_terms": {"type": "string"},
    },
    "required": ["kb_query", "zh_terms", "en_terms"],
}

_MAX_ZH_TERMS = 3
_TERM_SEPARATORS = re.compile(r"[,，、;；\s]+")
_ASCII_TOKEN = re.compile(r"^[A-Za-z0-9.\-]+$")


@dataclass(frozen=True)
class RewrittenQuery:
    """一次改寫的三種用途。

    - kb_query：CRAG 判 ambiguous 時重查知識庫的問句
    - zh_terms：網搜中文那一路（gov.tw）的關鍵字；空字串＝沿用原句
    - en_terms：網搜英文那一路（nih.gov 等）的關鍵字；空字串＝不搜英文
    """

    kb_query: str
    zh_terms: str = ""
    en_terms: str = ""


class QueryRewriter(Protocol):
    async def rewrite(self, query: str, docs: list[Document]) -> RewrittenQuery: ...


def normalize_zh_terms(raw: str) -> str:
    """最多三個關鍵字、以空白分隔，並拿掉純英數的字詞。

    拿掉英數字詞不能只靠 prompt 規則：實測「持續性性興奮症候群 PGAD」在
    gov.tw 回 0 筆、拿掉 PGAD 後回 5 筆；縮寫在中文站也容易撞到無關字串
    （「PGAD」命中疾管署 PDF 裡的質體名 pGAD-HAX-1）。縮寫留給英文那一路。
    """
    tokens = [
        token
        for token in _TERM_SEPARATORS.split(raw or "")
        if token and not _ASCII_TOKEN.match(token)
    ]
    return " ".join(tokens[:_MAX_ZH_TERMS])


def normalize_en_terms(raw: str) -> str:
    """把各種分隔符統一成空白。

    不像中文那樣切詞計數：英文醫學名詞常是多字詞（knee osteoarthritis），
    以空白切開再截斷會把一個名詞切成兩半。
    """
    return " ".join(token for token in _TERM_SEPARATORS.split(raw or "") if token)


class GeminiQueryRewriter:
    def __init__(
        self,
        gemini_service: GeminiService | None = None,
        *,
        max_chars_per_doc: int = 200,
        invoke_rewrite: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._gemini = gemini_service
        self._max_chars = max_chars_per_doc
        self._invoke_rewrite = invoke_rewrite

    async def rewrite(self, query: str, docs: list[Document]) -> RewrittenQuery:
        snippets: list[str] = []
        for doc in docs[:3]:
            text = (doc.page_content or "").strip().replace("\n", " ")
            snippets.append(text[: self._max_chars])
        # 規則 1、2 是量出來的失誤：沒有規則 1 時「長輩喘、腳腫」被改寫成
        # 「心臟衰竭」（替使用者下診斷），沒有規則 2 時查證型問題的主體
        # （某種偏方）被丟掉、只剩病名。舉例刻意不用評測題裡的題目。
        prompt = (
            "把使用者的健康問題改寫成搜尋用的查詢，不要回答問題。\n"
            "規則：\n"
            "1. 只能使用使用者訊息裡出現的疾病、症狀、食物、藥物或說法；"
            "禁止自行推論或補上病名（例如使用者只說頭暈、耳鳴，"
            "就不能寫成梅尼爾氏症）。\n"
            "2. 使用者在查證某個說法時，關鍵字必須保留該說法的主體"
            "（例如「喝醋可以軟化血管」要保留「喝醋」與「血管」）。\n"
            "3. 縮寫必須展開成全名（例如 COPD → 慢性阻塞性肺病／"
            "chronic obstructive pulmonary disease）；zh_terms 不可包含英文縮寫。\n"
            "kb_query：一句更具體、利於向量檢索的繁體中文問句。\n"
            "zh_terms：最多三個台灣衛教常用的繁體中文關鍵字，以空白分隔。\n"
            "en_terms：對應的英文醫學關鍵字，最多三個，以空白分隔。\n\n"
            f"原始問題：{query}\n\n"
            "知識庫目前檢索到的片段（僅供理解問題，可能不相關）：\n"
            + ("\n".join(snippets) if snippets else "(無)")
        )
        raw = await self._call(prompt)
        if not isinstance(raw, dict):
            raw = {}
        return RewrittenQuery(
            kb_query=str(raw.get("kb_query") or "").strip() or query,
            zh_terms=normalize_zh_terms(str(raw.get("zh_terms") or "")),
            en_terms=normalize_en_terms(str(raw.get("en_terms") or "")),
        )

    async def _call(self, prompt: str) -> dict[str, Any]:
        if self._invoke_rewrite is not None:
            return await self._invoke_rewrite(prompt)
        if self._gemini is None:
            raise RuntimeError("GeminiQueryRewriter requires gemini_service or invoke_rewrite")
        structured = self._gemini.chat_model.with_structured_output(
            REWRITE_SCHEMA,
            method="json_schema",
        )
        result = await structured.ainvoke([HumanMessage(content=prompt)])
        if not isinstance(result, dict):
            raise ValueError(f"unexpected rewrite payload: {type(result)}")
        return result
