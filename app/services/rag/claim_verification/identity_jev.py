"""同一性驗證改問 TypeSafe Jev；Jev 不能用時才退回 Gemini。

問的還是同一件事：使用者的說法與命中的查核報告，講的是不是同一個主張
（不判真偽，見 identity.py）。換的只是誰來答。

## 為什麼換

Gemini（gemini-3.8-flash）這一步 2026-09-22 從本機依序量 209 題：中位數
2.1 秒、P90 3.8 秒、最慢 10.7 秒；Jev 是只回機率、不生成文字的分類模型，
340 題中位數 0.27 秒、P90 0.31 秒、最慢 0.75 秒。
線上跑到 identity 的查核卡整段要 6～18 秒，這一步是其中固定的一塊。

## 評測（2026-09-22，jev-1.13.0，題目與門檻就是下面這兩個常數）

三批手寫題（evals/claim_identity/dataset.jsonl，附兩邊當次的分數），報告端都是正式庫裡帶 verdict 的真實標題，送法與線上
`_checked_claim` 相同（title｜claim）：

- 主集 202 題（TFC 報告 105 正／97 負，含線上量到的 3 個真實案例）：
  負樣本刻意做成「同一篇報告、換一個字就不同」——換食物／藥／疫苗、換
  效果、方向相反、換族群、或使用者講的其實是正確衛教。
- 保留集 68 題（36／32）：門檻挑定之後才寫、換一批 TFC 報告。
- 其他來源 70 題（36／34）：Cofacts 整段轉傳原文、食藥署與國健署的問句
  標題——正式庫可命中的 verdict 文件有一大半是這兩種格式，不只 TFC。

「說成同一主張但其實不同」（誤配，會把別篇的判定貼到使用者的話上）與
「其實相同卻說不同」（誤殺，退成證據不足＋相關衛教）：

                 誤配            誤殺
  主集  Jev      0/97            5/105
        Gemini   0/97            8/105
  保留集 Jev     0/32            0/36
  其他來源 Jev   0/34            0/36
        Gemini   0/7（只跑 Jev 分數最高的 7 題負樣本，控制 Gemini 呼叫量）

兩邊錯的地方不一樣。Gemini 的 8 題誤殺有 7 題是結論句標題（「吃雞蛋不會
導致高膽固醇」對上「吃雞蛋會膽固醇太高」）：它把否定句當成方向相反的另一
個主張；這 7 題 Jev 對了 6 題。Jev 的 5 題誤殺有 4 題是報告標題很長、含多
個主張，使用者只轉述其中一段（例如症狀清單簡化過），這 4 題 Gemini 對了 3 題。

誤配 0 不代表真的是 0：163 題全對，95% 上界約 1.8%；Gemini 97 題約 3%，
這個樣本量分不出兩者誰好，只能說 Jev 沒有比較差。

## 門檻 0.8

負樣本最高分 0.70（同一題重送三次會差到 ±0.05），正樣本在 0.8 以下的
5 題全在主集。0.8 留約 0.1 的距離；再往上拉到 0.93 誤殺會跳到約兩成，
因為 Jev 對明確相同的題目多半也只給 0.9 上下。錯的方向是刻意偏向誤殺：
誤殺的代價是答不出來，誤配的代價是張冠李戴的錯誤判定（identity.py）。

## 題目用英文寫

官方文件說英文是它最準的語言；兩句話本身是中文，放在 state 裡。第二段
false 條件（只重述謠言裡的背景事實）是看到「冬天血壓會比較高」對上
「天氣一冷血壓本來就會上升，可以不必理會？」被判 0.86 之後加的——那句
是正確衛教，配上「錯誤」判定就是把對的說成錯。改字要重跑評測。

## 失效方向

沒有金鑰、逾時、HTTP 錯誤、回應缺欄位，一律改問 Gemini
（`GeminiClaimIdentityVerifier`），它自己再 fail-closed。不直接 fail-closed
成「不同」：外部服務一掛，所有命中都會變成證據不足，看起來只是「防線
攔截率上升」，要看 log 才知道。

log：Jev 判定記 `stage=claim_identity_jev outcome=same|different p= ms=`；
退回時記 `outcome=fallback error=`，接著 Gemini 照舊記 `stage=claim_identity`。
所以「這次根本沒判成」仍然只會出現在 `stage=claim_identity` 的
error／unparsable，service.py 那段扣除方式不用改。
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.core.request_logging import log_stage
from app.services.rag.claim_verification.identity import ClaimIdentityVerifier

logger = logging.getLogger(__name__)

JEV_URL = "https://api.typesafe.ai/v1/systemone"
# 釘版本而不用 jev-latest：門檻與上面的數字是在 1.13.0 上量的。
JEV_MODEL = "jev-1.13.0"
SAME_THRESHOLD = 0.8
# 實測最慢 0.75 秒；2 秒約是它的 3 倍。逾時之後還要再等 Gemini（中位數 2 秒），
# 設更長只會讓 Jev 卡住時整體更慢。
TIMEOUT_SECONDS = 2.0

QUESTION = {
    "same": {
        "type": "noul",
        "instructions": (
            "`report_claim` is the headline of a fact-check report. It either quotes the "
            "rumor(s) the report checked (often inside 「」, sometimes several rumors in one "
            "headline) or states the report's conclusion about that rumor. Did this report "
            "check the same claim that `user_claim` makes? Do not judge whether either claim "
            "is true. Both texts are in Traditional Chinese."
        ),
        "criteria": {
            "true": (
                "The report checked exactly what `user_claim` asserts: the same subject (same "
                "food, drug, vaccine, practice, device or product), the same asserted effect "
                "or outcome, and the same direction (e.g. both say X causes Y, or both say X "
                "cures Y). Wording, tone and level of detail may differ. If `report_claim` "
                "lists several rumors, it is enough that one of them is the same claim as "
                "`user_claim`. If `report_claim` is a conclusion such as 'no evidence that X "
                "causes Y', a user saying 'X causes Y' is the same claim."
            ),
            "false": (
                "The two share a topic but assert different things: a different food, drug, "
                "vaccine, body part, disease or outcome; a different target population or "
                "dose; the opposite direction (e.g. prevents vs. causes); or `user_claim` is "
                "a separate statement that the report did not check, even if it is about the "
                "same subject. Also false when `user_claim` only repeats a background fact or "
                "premise mentioned in the rumor but not the rumor's actual assertion (e.g. the "
                "rumor says 'X is normal, so it can be ignored' and the user only says 'X is "
                "normal')."
            ),
        },
    }
}


class JevClaimIdentityVerifier:
    """與 `GeminiClaimIdentityVerifier` 同介面，Gemini 版放在 fallback。"""

    def __init__(
        self,
        *,
        api_key: str,
        fallback: ClaimIdentityVerifier,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._fallback = fallback
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def is_same_claim(self, user_claim: str, checked_claim: str) -> bool:
        if not self._api_key:
            return await self._fallback.is_same_claim(user_claim, checked_claim)

        t0 = time.perf_counter()
        try:
            probability = await asyncio.wait_for(
                self._ask(user_claim, checked_claim), timeout=TIMEOUT_SECONDS
            )
        except Exception as e:  # noqa: BLE001
            log_stage(
                logger, "claim_identity_jev", outcome="fallback", error=type(e).__name__,
                ms=int((time.perf_counter() - t0) * 1000),
            )
            return await self._fallback.is_same_claim(user_claim, checked_claim)

        same = probability >= SAME_THRESHOLD
        log_stage(
            logger, "claim_identity_jev", outcome="same" if same else "different",
            p=round(probability, 4), ms=int((time.perf_counter() - t0) * 1000),
        )
        return same

    async def _ask(self, user_claim: str, checked_claim: str) -> float:
        response = await self._client.post(
            JEV_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "state": {"user_claim": user_claim, "report_claim": checked_claim},
                "model": JEV_MODEL,
                "questions": QUESTION,
            },
        )
        response.raise_for_status()
        return float(response.json()["answers"]["same"]["noul"])
