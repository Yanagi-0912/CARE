"""查核判定卡：串接主張正規化與已查核比對，產出結構化查核結果。

這裡是「正規化 → 比對」這條管線收斂成單一輸出的地方，也是整個查核判定卡
功能中唯一容許「判定值」成形的地方。`openspec/changes/claim-verdict-card/
specs/claim-verification/spec.md` 明文要求判定 SHALL NOT 由語言模型產生：
命中時逐字照抄 `ClaimMatch.verdict`（TFC 記者查核、編輯複核、總編輯審視
後的人工結論），未命中固定為 `NOT_ENOUGH_EVIDENCE`。

這不只是實作慣例，型別上也切斷了模型影響判定的路：`_rewrite_reasoning`
回傳的是 `str`（一段理由文字），`identity_verifier.is_same_claim` 回傳的是
`bool`，`verify()` 裡沒有任何一行把這兩者指派給 `verdict`——`verdict`
只會來自 `match.verdict` 或 `NOT_ENOUGH_EVIDENCE` 這兩個來源，兩者在呼叫
語言模型之前就已經定案，語言模型的輸出無論寫了什麼、`is_same_claim` 回
True 還是 False，都沒有管道能回頭覆寫它——`is_same_claim` 唯一的效果是
決定要不要「採用」`match`（見 `identity.py` 模組 docstring），一旦不採用，
就完全走未命中那條早已定案為 `NOT_ENOUGH_EVIDENCE` 的路徑。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from app.core.rag_sources import SourceRef
from app.core.request_logging import stage_timer
from app.services.gemini import GeminiService
from app.services.gemini.shared.parser import content_to_text
from app.services.rag.claim_verification.identity import ClaimIdentityVerifier
from app.services.rag.claim_verification.matcher import ClaimMatch, ClaimMatcher
from app.services.rag.claim_verification.normalizer import ClaimNormalizer

logger = logging.getLogger(__name__)

NOT_ENOUGH_EVIDENCE = "證據不足"
# NOT_ENOUGH_EVIDENCE 的機器鍵。刻意不沿用 TFC 資料本身可能用的 "unproven"
# ——那個字串代表「TFC 也把某篇報告判定為證據不足」，這裡是 CARE 自己在
# 沒有比對命中時指派的固定值，兩者來源不同，各自留一個 slug 較誠實
# （見 verdict_flex.py 的配色表如何使用這個值）。
NOT_ENOUGH_EVIDENCE_SLUG = "not-enough-evidence"

# 未命中時的固定理由：只說明「TFC 沒查過」這個事實，不得讀起來像任何判定推論。
_NO_MATCH_REASONING = (
    "台灣事實查核中心目前沒有針對這則說法的查核報告，因此無法給出判定。"
    "以下是衛教資料庫查到的相關說明，供你參考，並不是這則說法的查核結論。"
)

# 理由改寫失敗（例外、空回應）時的中性 fallback。刻意不是報告原文的摘要：
# matcher 選到的 chunk 系統性地是 TFC 報告「一、網傳⋯」那段（跟使用者主張
# 最相似的必然是複述謠言本身，不是查核結論，見 matcher.py 的相似度排序），
# 摘要片段可能讀起來像在附和謠言而非駁斥它。改寫失敗時寧可用一句不帶立場
# 的通用句子，把完整說明的責任交給下方的來源連結——matched=True 必有
# source_url（見 VerificationResult 欄位註解），這個連結一定存在。
_REASONING_FALLBACK = "完整查核說明請見下方來源連結。"

# 未命中時的相關衛教資訊最多取檢索結果前幾筆內容，避免卡片過長。
#
# 3 → 2 是大小門檻逼出來的。實測條件：三篇候選、每篇一個滿版 chunk
# （`chunk_size` 上限 500 字），走 `verify()` 的真實路徑量測上線位元組，
# 門檻為 `size_guard.SAFE_BUBBLE_BYTES`（9,216）：
#
#     取 3 筆、無出處（本次變更前）   10,893 bytes  ❌
#     取 3 筆、含出處                12,500 bytes  ❌
#     取 2 筆、含出處                 8,993 bytes  ✅
#
# 注意第一行：**取 3 筆在滿版情況下本來就已經超標**，不是加上出處才壞的。
# 也就是說這個值一直偏大，只是過去沒有人量過——那種卡片一路都在退回純文字，
# 而退回不會有任何錯誤訊息（見 `size_guard` 模組 docstring）。
#
# 取 2 筆換到的是整張卡片留在 Flex 版面。方向是刻意的：退回純文字時使用者
# 失去的是整張卡片的結構（判定標頭、問句區塊、可點來源），比少一段參考資訊
# 多得多。
#
# 餘裕不大（8,993 / 9,216，剩 223 bytes），標題特別長時仍可能超標。那由
# `fits()` 接住、退回純文字，不會讓使用者收不到訊息。調高這個值之前請重跑
# 上面那組量測。
_RELATED_INFO_TOP_K = 2


@dataclass(frozen=True)
class VerificationResult:
    user_question: str  # 使用者原本的問句（正規化前）——判定卡要顯示這個
    verdict: str  # 五種之一；未命中固定為 NOT_ENOUGH_EVIDENCE
    reasoning: str  # 白話理由
    source_title: str  # 命中時為查核報告標題，未命中為空字串
    source_url: str  # 同上
    matched: bool  # 是否命中已查核主張
    related_info: str  # 未命中時的相關衛教資訊；命中時為空字串
    # verdict 的穩定機器鍵（命中時取自 ClaimMatch.verdict_slug，未命中固定
    # 為 NOT_ENOUGH_EVIDENCE_SLUG）。呈現層（verdict_flex.py）配色表以這個
    # 欄位為主要 key，而非 verdict 的中文顯示字串——中文字串來自 CARE-data
    # 的前綴對照表或 TFC 網站用詞，兩者都出過資料異常的事故，slug 是系統間
    # 約定的機器鍵，較不受這類上游用詞漂移影響（見 matcher.py 的合法值
    # 檢核）。預設空字串是為了不強迫既有呼叫端（例如既有測試）都要多帶一個
    # 欄位；呈現層對空字串一樣有備援（退回中文字串比對）。
    verdict_slug: str = ""
    # 查核報告的發布日期。呈現用——讓使用者自己判斷這份查核有多新，
    # 而不是由系統代為篩掉舊的（查核報告不會過期，謠言會重傳）。
    #
    # 有預設值是因為這個欄位本來就可能為空：食藥署公告那 576 篇連同 url
    # 一起沒有日期，上游 API 結構上不提供。
    source_published_at: str = ""
    # 判定出自哪一個來源（台灣事實查核中心、食藥署闢謠專區、Cofacts 真的假的…）。
    # 呈現層要照這個顯示，不能再寫死 TFC——2026-09-19 補進政府闢謠與 Cofacts
    # 之後，寫死那句話就是把別人的判定掛到 TFC 名下。
    source_name: str = ""
    # 未命中時 `related_info` 那幾段各自的出處，命中時為空。與 `related_info`
    # 並存而非取代它：那個字串是給純文字 fallback 與卡片內文用的可讀段落，
    # 這裡是給呈現層做連結用的結構化資料，兩者用途不同（理由同
    # `rag_sources.SourceRef` 的 docstring：從已組好的文字反解來源不可靠）。
    #
    # 沿用 RAG 答問路徑的 `SourceRef` 而非另立型別，是因為兩邊要遵守的是
    # 同一條規則——rag-responses 明文要求「缺 url 的來源仍須顯示，不得靜默
    # 丟棄」。`verify_claim` 未命中側過去完全不呈現來源，是這條規則在專案
    # 裡唯一沒有對齊的地方。
    related_sources: tuple[SourceRef, ...] = ()



class RelatedInfoRetriever(Protocol):
    """未命中時用來找相關衛教資訊的檢索器；鴨子型別對齊 `MongoAtlasVectorRetriever`。"""

    async def ainvoke(self, query: str) -> list[Document]: ...


def _checked_claim(match: ClaimMatch) -> str:
    """同一性驗證要比對的「查核報告的主張」：title 與 claim 都給，而非只信任
    claim 欄位（I3 finding）。design.md 決策 8 已用實測否定 claim 的可靠度：
    340 篇裡 35% 裝的其實是結論句而非主張句（例如「傳言說法缺乏醫學根據⋯
    因此為『錯誤』訊息」），缺乏主詞會讓 identity 驗證拿使用者主張去比一句
    沒有主題的結論、判成不同主張，誤殺本該命中的比對。TFC 標題（【錯誤】
    網傳「⋯」？）穩定含有謠言原文，因此以 title 為主幹、claim 有值時補充
    在後，而不是兩者互斥擇一。"""
    parts = [part for part in (match.title, match.claim) if part]
    return "｜".join(parts)


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"[（(\[【]?\s*(?:https?://|www\.)\S+?[）)\]】]?(?=[\s，。；、）)】]|$)")
_DANGLING_RE = re.compile(r"[（(\[【]\s*[）)\]】]")
_SPACE_RE = re.compile(r"[ \t]{2,}")
# 刪掉網址之後常留下「…資料來源 。」這種尾巴：標點前的空白、連著的標點都要收。
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([，。；、：）)】])")
_DUP_PUNCT_RE = re.compile(r"([，、；])(?=[，。；、])")


def strip_urls(text: str) -> str:
    """把內文裡的網址拿掉，只留看得懂的字。

    卡片的來源已經是可點的按鈕，內文再放一長串網址對長輩只是雜訊——他也點不到
    （Flex 的 text 不可點）。2026-09-19 James 連續兩次回報「相關衛教資訊貼了
    一堆網址」：知識庫有 14%（1,459/10,601 段）的內文本身就含網址，生成時會被
    照抄出來，光是換成 RAG 生成並不會讓它們消失。

    Markdown 連結保留文字、丟掉網址；裸網址整段刪掉，順手清掉刪完剩下的空括號
    與多餘空白。行內引用標記 `[1]` 不受影響——那是對應來源按鈕的編號，刪了就對
    不回去。
    """
    if not text:
        return text
    cleaned = _MARKDOWN_LINK_RE.sub(r"\1", text)
    cleaned = _BARE_URL_RE.sub("", cleaned)
    cleaned = _DANGLING_RE.sub("", cleaned)
    cleaned = _SPACE_RE.sub(" ", cleaned)
    cleaned = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = _DUP_PUNCT_RE.sub("", cleaned)
    return "\n".join(line.rstrip() for line in cleaned.splitlines()).strip()


class ClaimVerificationService:
    """把 `ClaimNormalizer` 與 `ClaimMatcher` 串成一次查核，回傳 `VerificationResult`。"""

    def __init__(
        self,
        normalizer: ClaimNormalizer,
        matcher: ClaimMatcher,
        gemini_service: GeminiService | None = None,
        *,
        invoke_reasoning: Callable[[str], Awaitable[str]] | None = None,
        related_retriever: RelatedInfoRetriever | None = None,
        related_answer: "Callable[[str], Awaitable[tuple[str, tuple[SourceRef, ...]]]] | None" = None,
        identity_verifier: ClaimIdentityVerifier | None = None,
    ) -> None:
        self._normalizer = normalizer
        self._matcher = matcher
        self._gemini = gemini_service
        self._invoke_reasoning = invoke_reasoning
        self._related_retriever = related_retriever
        # 未命中時那段「相關衛教資訊」由誰產生。
        #
        # 有注入就走它（正式環境接的是 RAG 的答案生成，見 dependencies.py）；
        # 沒注入才退回 `_fetch_related_info` 的原始片段。2026-09-19 James 指出
        # 卡片上「貼了一堆連結」——那就是原始片段：知識庫文章本文含網址，整段
        # 貼上去就照樣出現，而且沒有經過任何生成，與命中側「LLM 潤成白話理由」
        # 的標準不一致。
        self._related_answer = related_answer
        # None 時跳過同一性驗證、直接採用比對命中（向後相容既有行為與既有
        # 測試）。正式環境 SHALL NOT 省略這個參數：dependencies.py 必須實際
        # 注入已配置好的驗證器，否則向量誤配（design.md 決策 9 量到的 65%）
        # 會原樣回到線上——省略不會被任何呼叫端的型別或既有測試攔下來，
        # 純粹是接線紀律的問題。
        self._identity_verifier = identity_verifier

    async def verify(
        self, user_text: str, *, related_on_miss: bool = True
    ) -> VerificationResult:
        """一次查核的入口。實作在 `_verify`，這裡只負責觀測。

        `related_on_miss=False` 時未命中不產生「相關衛教資訊」：呼叫端自己會改走
        知識庫（圖卡主張，見 `claim_tools.verify_claim`），這段 RAG 生成做了也
        沒人看，只是多等幾秒。

        `stage=claim_verify` 記的是三選一的結果：`hit`、`no_match`
        （比對就沒中）、`identity_rejected`（比對中了但同一性驗證否決）。
        後兩者過去在日誌上是同一件事——都只是「使用者收到證據不足」——但
        它們要修的東西完全不同：`no_match` 指向語料缺口，`identity_rejected`
        指向那道 fail-closed 防線可能誤殺。

        `identity_rejected` 的量是這條管線目前最可能的沉默失效：防線擋下的
        誤配有實測（design.md 決策 9 的 65%），它連帶擋掉多少本來正確的命中
        則從來沒有量過，而使用者兩種情況看到的卡片一模一樣，不會有人回報。
        搭配 `stage=claim_identity` 的 outcome（同一個 rid）可以把其中「其實
        是 Gemini 呼叫失敗」的部分扣掉。
        """
        with stage_timer(
            logger, "claim_verify", user_len=len(user_text or "")
        ) as obs:
            obs["outcome"] = "error"
            return await self._verify(user_text, obs, related_on_miss)

    async def _verify(
        self, user_text: str, obs: dict[str, Any], related_on_miss: bool = True
    ) -> VerificationResult:
        claim = await self._normalizer.normalize(user_text)
        match = await self._matcher.match(claim)

        if match is not None:
            obs["score"] = round(match.score, 4)
            if self._identity_verifier is None:
                # 正式環境不該出現（見 __init__ 對這個參數的說明）。線上真的
                # 出現這個值，代表 65% 誤配率那道防線整條沒接上——那是接線
                # 疏漏裡最安靜的一種：功能照常回答，只是答錯的那些沒人擋。
                obs["identity"] = "skipped"

        if match is not None and self._identity_verifier is not None:
            # 向量比對命中不代表同一主張（design.md 決策 9）。這裡刻意不
            # catch identity_verifier 拋出的例外：GeminiClaimIdentityVerifier
            # 對「呼叫失敗」已經自己 fail-closed 回 False，會不帶例外地走到
            # 下面的判斷；唯一還會逸散到這裡的例外，是它自己刻意選擇不吞的
            # 「兩個依賴都沒注入」（見 identity.py 模組 docstring）——那是
            # dependencies.py 的接線疏漏，應該讓它大聲失敗，不該在這裡被
            # 再吞一次變成又一層靜默降級。
            #
            # 驗證的第一個引數是 user_text（使用者原問句），不是上面正規化
            # 後的 claim：卡片最終顯示的是 user_text（見下方 user_question
            # 欄位），若驗證比對的是另一個版本的文字，這道防線驗證的就不是
            # 「卡片實際顯示的內容是否與查核報告同一主張」，而是「normalizer
            # 這一次剛好萃取出的文字是否同一主張」——兩者一旦因正規化漂移
            # （例如包裝詞連同否定詞一起被剝除）而語意不同，防線形同沒擋
            # （I1 finding）。checked_claim 同時給 title 與 claim，理由見
            # `_checked_claim` 的 docstring（I3 finding）。
            checked_claim = _checked_claim(match)
            same = await self._identity_verifier.is_same_claim(
                user_text, checked_claim
            )
            obs["identity"] = "same" if same else "different"
            if not same:
                self._log_identity_detail(user_text, checked_claim)
                match = None

        if match is None:
            obs["outcome"] = (
                "identity_rejected"
                if obs.get("identity") == "different"
                else "no_match"
            )
            obs["verdict"] = NOT_ENOUGH_EVIDENCE_SLUG
            if related_on_miss:
                related_info, related_sources = await self._related_information(claim)
            else:
                related_info, related_sources = "", ()
            obs["related"] = len(related_sources)
            return VerificationResult(
                user_question=user_text,
                verdict=NOT_ENOUGH_EVIDENCE,
                verdict_slug=NOT_ENOUGH_EVIDENCE_SLUG,
                reasoning=_NO_MATCH_REASONING,
                source_title="",
                source_url="",
                source_published_at="",
                matched=False,
                related_info=related_info,
                related_sources=related_sources,
            )

        # verdict 在這裡已經定案（逐字取自 match）；下一行呼叫語言模型純粹是
        # 為了把報告內容潤成白話理由，其回傳值不會、也沒有管道能回頭改動上面這行。
        obs["outcome"] = "hit"
        obs["verdict"] = match.verdict_slug or match.verdict
        reasoning = await self._rewrite_reasoning(user_text, match)
        return VerificationResult(
            user_question=user_text,
            verdict=match.verdict,
            verdict_slug=match.verdict_slug,
            reasoning=reasoning,
            source_title=match.title,
            source_name=match.source_name,
            source_url=match.url,
            source_published_at=match.published_at,
            matched=True,
            related_info="",
        )

    def _log_identity_detail(self, user_text: str, checked_claim: str) -> None:
        """只在 DEBUG 落地的被否決文字對。

        `stage=claim_verify` 那行刻意只有長度與結果、沒有問句原文（與
        `message_handler` 的 `stage=handle text_len=` 同一個慣例）。但要判斷
        一次 `identity_rejected` 到底是擋對還是誤殺，只能看這兩句話本身——
        分數與 outcome 都不夠。需要人工抽樣校準時開一段 `LOG_LEVEL=DEBUG`。

        記的是實際送進驗證器的那一對字串（`user_text` 與 `checked_claim`），
        不是正規化後的 claim：抽樣要重現的是驗證器當下看到的東西，換成別的
        版本就變成在檢查另一個問題（見 `verify` 裡挑 user_text 的理由）。
        """
        if not logger.isEnabledFor(logging.DEBUG):
            return
        logger.debug(
            "claim_identity_detail user=%r checked=%r", user_text, checked_claim
        )

    async def _rewrite_reasoning(self, user_text: str, match: ClaimMatch) -> str:
        # matcher 選到的 chunk 是與使用者主張「最相似」的段落；TFC 報告結構
        # 固定是「一、網傳⋯」「二、查核中心採訪⋯」「結論」，使用者送進來的
        # 就是謠言本身，最相似的段落系統性地會是複述謠言的第一段，不是查核
        # 結論。若不告訴模型判定是什麼，它只能自己猜立場，容易寫成聽起來在
        # 附和謠言的中立轉述（I2 finding）。這裡把 match.verdict 放進 prompt
        # 當立場約束——不違反決策 1：判定值本身仍只來自 match.verdict（見
        # verify() 與模組 docstring），這裡只是不讓模型「猜」方向，模型的
        # 輸出依然只用於潤飾理由文字，回傳值沒有任何管道能回頭改動判定。
        prompt = (
            "以下是台灣事實查核中心針對一則說法的查核報告內容。"
            f"這篇報告的既定判定是「{match.verdict}」，請以這個判定為立場基礎，"
            "用白話文改寫成 2-3 句理由，具體說明查核報告依據什麼事實或專家"
            "意見得出這個判定，不要寫成中立轉述、不要讓文字讀起來像在附和"
            "說法本身。\n"
            "只寫理由本身：不要逐字寫出判定字樣"
            "（例如「這是錯誤的」「這是正確的」「判定為⋯」——判定已經顯示在"
            "卡片標頭，這裡不必也不該重複），也不要加開場白或結尾。\n\n"
            f"使用者的問題：{user_text}\n"
            f"查核報告內容：{match.content}"
        )
        try:
            reasoning = await self._call_reasoning(prompt)
        except Exception as exc:  # noqa: BLE001
            # fail-open：理由只是潤飾用的白話文字，抓不到不能讓整次查核失敗。
            # 降級用中性句而非報告原文摘要：matcher 選到的 chunk 系統性地是
            # 複述謠言那段（見上方註解），直接摘要可能讀起來像在支持謠言，
            # 尤其原本的「取前 200 字」還會把句子從中間切斷。完整說明交給
            # 卡片下方一定會有的來源連結（I2 finding）。
            logger.warning(
                "reasoning rewrite failed, degrading to neutral fallback: %s",
                exc,
                exc_info=True,
            )
            return _REASONING_FALLBACK

        reasoning = (reasoning or "").strip()
        return reasoning or _REASONING_FALLBACK

    async def _call_reasoning(self, prompt: str) -> str:
        if self._invoke_reasoning is not None:
            return await self._invoke_reasoning(prompt)
        if self._gemini is None:
            raise RuntimeError(
                "ClaimVerificationService requires gemini_service or invoke_reasoning"
            )
        # 純文字回應：理由不是結構化資料，刻意不套 with_structured_output 的
        # schema——那會讓「判定」看起來像是這次呼叫的合法輸出欄位之一。
        result = await self._gemini.chat_model.ainvoke([HumanMessage(content=prompt)])
        # Gemini 在部分情境會回傳 list-of-parts 而非純字串的 .content；直接
        # str(content) 會把 Python repr 印進理由段，改用與
        # app/services/agent/agent.py 共用的攤平邏輯（次要 finding 1）。
        return content_to_text(result.content)

    async def _related_information(
        self, claim: str
    ) -> tuple[str, tuple[SourceRef, ...]]:
        """未命中時的那段參考資訊：優先用 RAG 生成的答案，退回原始片段。

        RAG 那條路失敗（沒注入、逾時、查無資料）就退回 `_fetch_related_info`，
        不是留白：有東西可看仍然勝過只有一句「證據不足」。
        """
        if self._related_answer is not None:
            try:
                text, sources = await self._related_answer(claim)
                cleaned = strip_urls(text)
                if cleaned:
                    return cleaned, tuple(sources)
            except Exception:  # noqa: BLE001 - 同 _fetch_related_info 的 fail-open
                logger.warning("相關衛教資訊改用 RAG 生成時失敗，退回原始片段", exc_info=True)
        return await self._fetch_related_info(claim)

    async def _fetch_related_info(
        self, claim: str
    ) -> tuple[str, tuple[SourceRef, ...]]:
        """未命中時找相關衛教資訊，連同各段的出處一起回傳。

        `_related_retriever` 打的是與 matcher 相同的向量索引，因此同一批
        候選裡幾乎必然還包含剛才被同一性驗證擋下、或分數不足以命中的那些
        TFC 查核報告本身（C1 finding）：使用者的主張沒變，最相似的文件排序
        自然也不會變。若不過濾，卡片標頭寫著「證據不足」，下面的「相關衛教
        資訊」區塊卻可能貼著同一篇查核報告的判定敘述，等於同一性驗證擋下的
        結論從呈現層繞了回來——這是高頻路徑：任何被 fail-closed 否決的命中
        都符合這個條件。

        因此這裡排除 `verdict` 非空的文件（decision 4 要的是「衛教資訊」，
        TFC 查核報告本來就不是衛教文），一篇最多取一段，並附上來源標題與
        結構化的 `SourceRef`——未命中側過去是三段無來源、無標題的原始 chunk，
        既與命中側「可獨立驗證」的呈現標準不一致，也違反 rag-responses
        對來源呈現的既有要求。

        **去重鍵沿用 `RagAnswerService._source_key` 的規則**（url 優先，沒有
        url 時退回「來源名＋標題」），不是只看 url。舊版把整段去重包在
        `if url:` 裡，於是沒有網址的來源完全不去重——而「食藥署公告」那 576
        篇（`scraper_api` 的全站新聞稿 feed）上游結構上就沒有網址，同一篇的
        多個 chunk 因此可以佔滿 `_RELATED_INFO_TOP_K` 的全部名額，與本函式
        宣稱的「同一篇最多一段」相反。顯示成同一筆的兩段，去重也必須把它們
        當成同一筆。

        內容為空的檢查移到去重之前：空 chunk 不該先佔掉那篇文章的去重名額，
        害同一篇真正有內容的下一段被當成重複而丟掉。

        docs 的過濾／切片迴圈整段都在 try 裡（次要 finding 2）：舊版只有
        `ainvoke` 呼叫本身被保護，若 retriever 回傳非 list（介面變動、測試
        替身寫錯），後面的切片會丟出 TypeError 逸散出 `verify()`，與這裡
        「不能讓查核流程中斷」的既有承諾矛盾。
        """
        if self._related_retriever is None:
            return "", ()
        try:
            docs = await self._related_retriever.ainvoke(claim)
            excerpts: list[str] = []
            sources: list[SourceRef] = []
            seen_keys: set[str] = set()
            for doc in docs:
                if doc.metadata.get("verdict"):
                    continue
                content = (doc.page_content or "").strip()
                if not content:
                    continue
                source_name = str(doc.metadata.get("source_name") or "").strip()
                title = str(doc.metadata.get("original_title") or "").strip()
                url = str(doc.metadata.get("url") or "").strip()
                key = f"url:{url}" if url else f"meta:{source_name}|{title}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                index = len(sources) + 1
                excerpts.append(f"{title}\n{content}" if title else content)
                sources.append(
                    SourceRef(
                        index=index,
                        label=source_name or title or f"來源 {index}",
                        url=url,
                    )
                )
                if len(excerpts) >= _RELATED_INFO_TOP_K:
                    break
            # 原始片段這條路更需要清網址：貼的就是文章原文。
            return strip_urls("\n\n".join(excerpts)), tuple(sources)
        except Exception as exc:  # noqa: BLE001
            # 對齊 matcher/normalizer 的 fail-open：相關資訊只是附加參考，
            # 不是判定依據，抓不到就留白，不能讓查核流程中斷。
            logger.warning(
                "related info retrieval failed, degrading to empty: %s",
                exc,
                exc_info=True,
            )
            return "", ()
