"""查核判定卡：命中已查核主張後，再驗證「使用者的主張」與「查核報告查的
主張」是不是同一件事。

向量相似度分不出這件事（design.md 決策 9 的負樣本實測：8 則 TFC 未查核的
手寫謠言＋12 題衛教型問句，門檻 0.86 時未查核謠言誤配 88%、衛教問句誤配
50%，合計 65%）。「網傳把洋蔥放在枕頭邊可以降血壓」能拿到 0.918 分配到一篇
不相干的查核報告，因為這些句子與查核報告在向量空間裡都是「食物與健康的
網路傳言」，相似度分不開。拉高門檻到 0.92 可以把誤配壓到 0%，但正樣本只
剩約三成過得了，功能形同虛設。所以門檻維持 0.86 保住召回，改為在命中後
多一道 LLM 呼叫驗證同一性，把誤配擋掉。

這道驗證 SHALL NOT 判斷主張的真偽，只回答「這兩句話講的是不是同一件事」。
`is_same_claim` 的回傳型別是 `bool`，不是字串——這不只是慣例，是刻意的
型別設計：把「這個函式可能被誤用來輸出判定值」在型別層級排除掉，不必依賴
程式碼審查才能發現誤用。

## fail-closed，與 normalizer／matcher／service 的 fail-open 相反

這是刻意的，不是疏漏。其餘元件失敗時續行（fail-open）是因為它們的產出是
錦上添花：正規化失敗還有原問句可用、相關資訊抓不到留白即可，續行的下檔
風險很小。這裡不同：一旦誤判「同一主張」，後果是把別篇文章、看似權威的
查核結論，貼到使用者真正問的主張上——張冠李戴的錯誤判定，代價遠高於
「答不出來」。所以任何讓「是否同一主張」無法確定的狀況（例外、逾時、
回應解析不出 bool），一律當作「不是同一主張」，不採用該篇判定，讓上層
（service.py）降級為「證據不足」。

## 唯一的例外：兩個依賴都沒注入時不 fail-closed，而是直接 raise

`is_same_claim` 對「呼叫失敗」fail-closed，但對「根本沒有辦法呼叫」
（`gemini_service` 與 `invoke_identity` 都沒給）刻意不吞：這種情況不是
執行期的意外，是接線疏漏（dependencies.py 組裝時必須實際注入，理由與
Task 3 review 記錄的 gemini_service 疏漏風險相同）。若也吞成 False，這支
驗證器會「看起來正常運作」——每次都平靜地回答「不是同一主張」，實際上是
完全沒有能力判斷，而且沒有任何錯誤訊息會提示這件事，等於把整條同一性
防線悄悄拔掉卻無人發現。寧可讓它在送出 LLM 請求前就大聲失敗，逼出忘記
接線的錯誤，也不要讓它偽裝成一個保守但正常運作的驗證器。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Protocol

from langchain_core.messages import HumanMessage

from app.services.gemini import GeminiService

logger = logging.getLogger(__name__)

IDENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same": {"type": "boolean"},
    },
    "required": ["same"],
}


class ClaimIdentityVerifier(Protocol):
    async def is_same_claim(self, user_claim: str, checked_claim: str) -> bool: ...


class GeminiClaimIdentityVerifier:
    """以 Gemini structured output 判斷兩句話是否為同一主張（不判斷真偽）。"""

    def __init__(
        self,
        gemini_service: GeminiService | None = None,
        *,
        invoke_identity: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._gemini = gemini_service
        self._invoke_identity = invoke_identity

    async def is_same_claim(self, user_claim: str, checked_claim: str) -> bool:
        if self._invoke_identity is None and self._gemini is None:
            # 見模組 docstring「唯一的例外」一節：刻意不併入下面的
            # try/except，讓接線疏漏以例外形式直接暴露，不偽裝成一次
            # 正常的「不同主張」判斷。
            raise RuntimeError(
                "GeminiClaimIdentityVerifier requires gemini_service or invoke_identity"
            )

        prompt = (
            "以下兩句話都是在描述某個流傳的說法，請判斷它們講的是不是同一個"
            "主張（即使用字、語氣不同，只要指涉的是同一件事就算相同），"
            "完全不要判斷這個主張本身正不正確。\n"
            "主題或情境相關、但實際主張不同時，仍要判斷為不同，例如：\n"
            "「吃鳳梨心可以溶解血栓」與「鳳梨酵素可以抗病毒」是不同主張"
            "（同食材、不同宣稱）；\n"
            "「洋蔥放在枕頭邊可以降血壓」與「洋蔥水可以治療感冒」是不同主張；\n"
            "「蛋洗過反而讓細菌跑進去」與「洗蛋會破壞蛋殼保護層、增加沙門氏"
            "菌風險」是同一主張的不同說法。\n\n"
            f"使用者的說法：{user_claim}\n"
            f"已查核報告針對的主張：{checked_claim}"
        )
        try:
            raw = await self._call(prompt)
        except Exception as exc:  # noqa: BLE001
            # fail-closed（見模組 docstring）：呼叫失敗一律視為「不是同一
            # 主張」，不採用該篇判定，讓上層降級為證據不足。
            logger.warning(
                "claim identity verification failed, fail-closed to not-same: %s",
                exc,
                exc_info=True,
            )
            return False

        same = raw.get("same") if isinstance(raw, dict) else None
        if not isinstance(same, bool):
            # 非 dict、缺 key、或型別不是 bool 都算「無法解析」，同樣
            # fail-closed——不能因為拿不到明確答案就預設當作 True。
            logger.warning(
                "claim identity verification returned unparsable payload: %r", raw
            )
            return False
        return same

    async def _call(self, prompt: str) -> dict[str, Any]:
        if self._invoke_identity is not None:
            return await self._invoke_identity(prompt)
        if self._gemini is None:
            raise RuntimeError(
                "GeminiClaimIdentityVerifier requires gemini_service or invoke_identity"
            )
        structured = self._gemini.chat_model.with_structured_output(
            IDENTITY_SCHEMA,
            method="json_schema",
        )
        result = await structured.ainvoke([HumanMessage(content=prompt)])
        if not isinstance(result, dict):
            raise ValueError(f"unexpected identity payload: {type(result)}")
        return result
