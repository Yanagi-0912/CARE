"""判斷一篇知識庫文章適不適合當成 Tier 2 消息卡推給高齡使用者。

形狀逐條比照 `app/services/medical_news/grader.py`（那支又比照
`app/services/rag/retrieval_grader.py`）：SCHEMA 常數、Protocol、`invoke_*`
注入點三件套。同一個專案裡三個做同一類事情的元件長得一樣，維護的人只需要
理解一次。

**與 `grader.py` 的分工**：那支問「這則消息是不是在講這個藥」（Tier 1，
per-drug）；這支問「這篇對一般高齡讀者有沒有用」（Tier 2，per-article）。
兩者的成本模型差很多——Tier 1 是 O(不重複藥數 × 搜尋結果數)，這支是
O(每日候選數)，每天上限 `MEDICAL_NEWS_TIER2_GRADE_MAX_CALLS` 次、**與使用者
人數無關**（池子是全體共用的，選材一天只跑一次）。

**與 `relevance.is_policy_announcement` 的分工**：字串黑名單先擋掉一望即知
的活動與政績新聞稿（不花額度），剩下的才送進來。這支要擋的是黑名單擋不掉的
那種——標題完全像衛教、內容也是真衛教，但對象不是高齡讀者（嬰幼兒篩檢、
青少年菸害、孕產補助）。

**失敗時 fail closed**（沿用 `grader.py` 的理由）：主動推播沒有人在等，沒推
遠比推錯好。本模組 SHALL NOT 吞掉任何例外、SHALL NOT 在輸出不合法時回一個
預設值——呼叫端要有能力分辨「判定為不適合」與「判定沒有發生」。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, NamedTuple, Protocol

from langchain_core.messages import HumanMessage

from app.services.gemini import GeminiService

logger = logging.getLogger(__name__)

ARTICLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_useful_for_elderly": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["is_useful_for_elderly", "reason"],
}

# 送進模型的內文上限。這裡收到的本來就只是卡片摘錄（`_EXCERPT_CHARS` 個字），
# 這個上限是防禦性的第二道，防的是呼叫端哪天改成送全文卻沒注意到成本。
DEFAULT_MAX_CHARS = 600


class ArticleJudgement(NamedTuple):
    is_useful_for_elderly: bool
    reason: str


class KbArticleGrader(Protocol):
    async def judge_article(self, title: str, excerpt: str) -> ArticleJudgement: ...


def _build_prompt(title: str, excerpt: str) -> str:
    return (
        "你是衛教內容篩選器。判斷這篇政府或查核機構的文章，適不適合當成每日\n"
        "健康小知識推播給**一般高齡使用者**（65 歲以上，多半有慢性病與用藥）。\n\n"
        "判為適合（true）的例子：\n"
        "- 疾病、症狀、用藥、飲食、運動、跌倒、天氣與身體的衛教知識\n"
        "- 破除健康謠言、澄清錯誤的偏方或療效宣稱\n"
        "- 高齡者用得上的檢查、篩檢或自我照護方法\n\n"
        "判為不適合（false）的例子：\n"
        "- 政策宣傳、活動報導、頒獎典禮、國際交流、機關政績\n"
        "- 法規修正進度、行政公告、統計數字發布\n"
        "- 內容雖是衛教，但對象明顯不是高齡者（嬰幼兒、孕產、不孕、青少年、\n"
        "  學童、軍人、職場新制）\n"
        "- 讀完之後高齡讀者無法據以改變任何日常行為\n\n"
        "只依據下方提供的標題與摘錄判斷，不要補充你自己知道的背景。\n"
        "不確定時判為 false——這是主動推播，沒有人在等，寧可不推。\n\n"
        "reason：一句話說明理由，繁體中文。\n\n"
        f"標題：{title}\n\n"
        f"摘錄：\n{excerpt or '(無)'}"
    )


def parse_article_judgement(raw: Any) -> ArticleJudgement:
    """把模型輸出轉成 `ArticleJudgement`；任何不合法之處一律拋 `ValueError`。

    不合法的輸出與「判定為不適合」是兩件不同的事，必須分得開：前者代表這次
    判定沒有發生，呼叫端要能把它記成錯誤而不是一次正常的淘汰。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"unexpected judgement payload: {type(raw)}")

    missing = [key for key in ARTICLE_SCHEMA["required"] if key not in raw]
    if missing:
        raise ValueError(f"article judgement missing fields: {missing}")

    verdict = raw["is_useful_for_elderly"]
    if not isinstance(verdict, bool):
        # 不用 `bool(verdict)`：字串 "false" 會變成 True，那是最糟的失敗方向
        # ——它會安靜地把所有東西都放行，等於這道防線不存在。
        raise ValueError(
            f"is_useful_for_elderly must be a bool, got {type(verdict)}"
        )

    return ArticleJudgement(
        is_useful_for_elderly=verdict,
        reason=str(raw["reason"] or "").strip(),
    )


class GeminiKbArticleGrader:
    """以 Gemini structured output 判定知識庫文章是否適合推給高齡使用者。"""

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

    async def judge_article(self, title: str, excerpt: str) -> ArticleJudgement:
        body = (excerpt or "")[: self._max_chars]
        raw = await self._call(_build_prompt(title, body))
        return parse_article_judgement(raw)

    async def _call(self, prompt: str) -> Any:
        if self._invoke_judge is not None:
            return await self._invoke_judge(prompt)
        if self._gemini is None:
            raise RuntimeError(
                "GeminiKbArticleGrader requires gemini_service or invoke_judge"
            )
        structured = self._gemini.chat_model.with_structured_output(
            ARTICLE_SCHEMA,
            method="json_schema",
        )
        return await structured.ainvoke([HumanMessage(content=prompt)])
