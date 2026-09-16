import asyncio
import logging
import re
import time
from typing import Any

from langchain_core.documents import Document

from app.core.request_logging import stage_timer
from app.services.gemini import GeminiService
from app.i18n.messages import t
from app.services.rag.cannot_answer import (
    CANNOT_ANSWER_MARKERS,
    NO_ANSWER_SENTINEL,
    answer_preview,
    matched_cannot_answer_marker,
)
from app.services.rag.answer_prompts import build_rag_prompt, wrap_context
from app.services.rag.cohere_reranker import Reranker, VectorScoreReranker
from app.services.rag.link_check import LinkChecker, dead_urls
from app.core.rag_sources import SourceRef, set_request_rag_sources
from app.services.rag.fail_messages import (
    NO_ANSWER_MESSAGE,
    NO_HITS_MESSAGE,
    RagFailCode,
    rag_fail,
)
from app.services.rag.query_rewriter import QueryRewriter, RewrittenQuery
from app.services.rag.retrieval_grader import Grade, RetrievalGrader
from app.services.rag.retriever import MongoAtlasVectorRetriever
from app.services.rag.web_search_service import WebSearchService
from app.services.gemini.shared.parser import content_to_text

logger = logging.getLogger(__name__)

# Wide retrieve candidates（依賴注入可用 settings 覆寫 retriever.k）
RETRIEVAL_TOP_K = 40
RERANK_TOP_N = 5
CITE_TOP_K = 3
# 精排後之文章層級去重：同一篇文章最多留幾個 chunk（見 dedup_ranked_docs）
RERANK_MAX_CHUNKS_PER_ARTICLE = 2
# CRAG grader 失敗時的數值門檻。0.0＝不設限，維持原行為。
#
# 正常路徑的相關性把關全靠 CRAG（grader 判 incorrect 就轉網搜），而
# RAG_VECTOR_MIN_SCORE 預設是 0.0——也就是說整條管線沒有任何數值下限。
# grader 本身逾時或配額用盡時，既有的降級是「不分級直接生成」，於是一組
# 可能毫不相關的 chunk 會被拿去生成醫療答案，而 prompt 裡「內容不足請說
# 不知道」只是軟約束。
#
# 這個門檻只在 grader 失敗那條路徑上生效，正常路徑不受影響——不是要用
# 數字取代 CRAG，是在 CRAG 不可用時補一張網。
DEFAULT_DEGRADED_MIN_SCORE = 0.0

# CRAG 判 ambiguous 時，啟動改寫第二輪的時間預算（秒）。0.0＝不設限，
# 維持導入前的行為。
#
# 第二輪是整條管線最貴的一段。下列數字是在 gemini-2.5-flash（thinking 預設
# 開啟）上實測的：rewrite 5.3s ＋ 第二輪檢索精排 1.6s ＋ grade 11.8s ≈ 19s，
# 後面還要再付一次 generate（3.8-10.2s）。同一題走不走第二輪是 43.6s 與 ~25s
# 的差別。
#
# ⚠ 預設模型已換成 gemini-3.8-flash，上面這組數字尚未在新模型上重測。實測新
# 模型的純文字回應快了約一倍，所以這個預算值很可能過於寬鬆——要調之前先重測，
# 不要照著舊數字推算。
#
# 為什麼是「用掉多少」而不是「還剩多少」：預算檢查點在第一輪 grade 之後，
# 那時已經知道這一輪的 grader 有多慢——grader 慢通常代表第二次也會慢，
# 用已花時間當預測比固定總時限準。
#
# 12 秒的來由：第一輪檢索＋精排＋grade 實測 5.0 / 9.1 / 13.9 秒，取在最慢
# 那題之下，讓它跳過第二輪、其餘兩題不受影響。**這是依三題樣本抓的起點，
# 不是調校過的值**；要調整請先用 evals/rag/golden.jsonl 量判定品質的變化。
#
# 超時的降級是「拿第一輪結果生成」而不是轉網搜：網搜要再打 Firecrawl
# 搜尋、可能逐頁 scrape、再生成一次，比第二輪更慢——為了省時間而走上更慢
# 的路是本末倒置。這與既有 rewrite 失敗時 `return ranked` 的降級一致。
DEFAULT_CRAG_REWRITE_BUDGET_SECONDS = 12.0

# 投機生成：CRAG 分級期間先把生成跑起來。
#
# 分級與生成互不依賴——生成只吃 ranked，分級不改動它——所以兩者可以並行。
# 實測分級 2.6-7.0s、生成 3.9-9.5s，而 84% 的題目分級結果是 correct、
# 送去生成的 docs 原封不動，這時等於整段分級時間白賺。
#
# 代價是另外 16%（incorrect／ambiguous）會多一次白跑的生成，付的是 token
# 不是延遲，並讓單次請求的 Gemini 併發從 1 升到 2。要關掉設 false。
DEFAULT_SPECULATIVE_GENERATE = True

# 整條管線的總逾時（秒）。0＝不設限。
#
# 為什麼需要：各段各自有逾時（Cohere、Firecrawl、連結檢查），但加起來沒有上限；
# Gemini 的呼叫以前根本沒有逾時（langchain-google-genai 4.2.2 預設 timeout=None、
# 重試 6 次；2026-09-16 起由 gemini_service 統一設 GEMINI_REQUEST_TIMEOUT_SECONDS
# 與重試 2 次，但單次上限×重試次數仍大於這裡的總預算）。實測過 rag_retrieve
# 卡 94 秒才回 0 筆，使用者等 107 秒換一句「查無資料」。檢索那段另有每條腿的
# 逾時（retriever.DEFAULT_LEG_TIMEOUT_SECONDS），這裡是最後一道：不管卡在哪一段，
# 到點就停。
#
# 45 秒的來由：
#   - 上界：LINE loading 動畫最長 60 秒（官方文件：「5 to 60 seconds」），超過
#     使用者連「還在處理」都看不到。60 秒要分給 RAG 以外的段落：guardrail＋
#     agent 決策＋最終回覆實測合計約 5 秒（2026-09-02）；語音回覆的 TTS 沒量過
#     （上限是連線 5＋接收 15 秒）。這裡留 15 秒給它們。
#   - 下界：golden 55 題完整管線（2026-09-14，本機 gemini-3.8-flash，各 1 次）
#     最慢 18.7 秒（網搜路徑），p90 14.9 秒。45 秒是最慢那題的 2.4 倍，正常
#     題目不會被切掉。
#
# 到點回 [RAG_ERR:TIMEOUT]，agent 依 prompt 規則 10 請使用者稍後再問。
DEFAULT_RAG_ANSWER_TIMEOUT_SECONDS = 45.0

_CITATION_RE = re.compile(r"\[(\d+)\]")


def cited_indices(answer_text: str) -> list[int]:
    """回傳答案中出現過的引用編號，依首次出現順序、去重。"""
    seen: set[int] = set()
    order: list[int] = []
    for match in _CITATION_RE.finditer(answer_text or ""):
        idx = int(match.group(1))
        if idx not in seen:
            seen.add(idx)
            order.append(idx)
    return order


class RagAnswerService:
    def __init__(
        self,
        gemini_service: GeminiService,
        retriever: MongoAtlasVectorRetriever,
        reranker: Reranker | None = None,
        *,
        rerank_top_n: int = RERANK_TOP_N,
        max_chunks_per_article: int = RERANK_MAX_CHUNKS_PER_ARTICLE,
        grader: RetrievalGrader | None = None,
        rewriter: QueryRewriter | None = None,
        crag_enabled: bool = False,
        web_search: WebSearchService | None = None,
        web_fallback_enabled: bool = False,
        degraded_min_score: float = DEFAULT_DEGRADED_MIN_SCORE,
        crag_rewrite_budget_seconds: float = DEFAULT_CRAG_REWRITE_BUDGET_SECONDS,
        link_checker: LinkChecker | None = None,
        speculative_generate: bool = DEFAULT_SPECULATIVE_GENERATE,
        total_timeout_seconds: float = DEFAULT_RAG_ANSWER_TIMEOUT_SECONDS,
    ) -> None:
        self.gemini_service = gemini_service
        self.retriever = retriever
        self.reranker: Reranker = reranker or VectorScoreReranker()
        self.rerank_top_n = rerank_top_n
        self.max_chunks_per_article = max_chunks_per_article
        self.grader = grader
        self.rewriter = rewriter
        self.crag_enabled = crag_enabled and grader is not None
        self.web_search = web_search
        self.web_fallback_enabled = bool(web_fallback_enabled and web_search is not None)
        self.degraded_min_score = degraded_min_score
        self.crag_rewrite_budget_seconds = crag_rewrite_budget_seconds
        # None＝不檢查來源網址存活，行為與導入前完全相同（見 link_check.py）
        self.link_checker = link_checker
        # 只有 CRAG 開啟時才有東西可以並行——沒有分級就沒有等待可以填。
        self.speculative_generate = bool(speculative_generate and self.crag_enabled)
        self.total_timeout_seconds = total_timeout_seconds

    async def answer(self, user_text: str) -> str:
        # 總計時的 path 欄位標出這一輪實際走了哪條路——同樣是 40 秒，
        # 走 kb 與走 web 要查的地方完全不同。
        with stage_timer(logger, "rag_answer") as timing:
            limit = self.total_timeout_seconds if self.total_timeout_seconds > 0 else None
            deadline = asyncio.timeout(limit)
            try:
                async with deadline:
                    return await self._answer(user_text, timing)
            except TimeoutError:
                # 只接自己這個總逾時。管線裡別處拋出的 TimeoutError 是另一種
                # 故障，照舊往上拋——記成「逾時」會把查錯方向帶歪。
                if not deadline.expired():
                    raise
                return self._timed_out(timing)

    def _timed_out(self, timing: dict[str, Any]) -> str:
        """總逾時到點：記錄、清掉已交出的來源，回 TIMEOUT。

        進行中的投機生成與改寫已由 `_answer` 的 finally 取消（逾時是以取消
        送進去的），這裡不必再收。
        """
        # 不能留在原本的 path：記成 kb 會讓分流門檻的校準把它當成「這個分數帶
        # 知識庫答得出來」的樣本（理由同 kb_model_refuse）。last_path 記逾時
        # 當下走到哪條路，才分得出卡在知識庫還是網搜。
        timing["last_path"] = timing.get("path")
        timing["path"] = "timeout"
        # 網搜路徑可能已把來源交給呈現層才逾時，逾時訊息不能掛著那組按鈕。
        set_request_rag_sources(())
        logger.warning(
            "rag_fail code=%s timeout_s=%s last_path=%s",
            RagFailCode.TIMEOUT,
            self.total_timeout_seconds,
            timing["last_path"],
        )
        return rag_fail(RagFailCode.TIMEOUT)

    async def _answer(self, user_text: str, timing: dict[str, Any]) -> str:
        # 預算從這裡起算，涵蓋第一輪檢索、精排與 grade——預算要防的是整體
        # 延遲，只計 _apply_crag 內部會漏掉前面已經花掉的時間。
        started = time.perf_counter()
        timing["path"] = "kb"
        # candidates＝第一輪的 docs；approved＝CRAG 放行的 docs。兩者刻意用不同
        # 名字：投機生成是否可用，正是靠「這兩個是不是同一個 list」判斷的。
        candidates = await self._retrieve_and_rerank(user_text)
        # 精排最高分，供日後校準「CRAG 之前就分流到網搜」的門檻：對照最後走
        # kb 答出來與 web_crag_reject 的題目各落在哪個分數帶。為什麼不用問題
        # 文字訓練分類器——知識庫持續新增，分類器學到的是舊知識庫的覆蓋範圍，
        # 而被它送去網搜的題目永遠不會再碰知識庫、日誌也永遠記 web，重新訓練
        # 只會把錯誤鎖得更死。這個分數是每次從知識庫即時算出的，沒有這個問題。
        timing["top_rerank"] = self._top_rerank_score(candidates)
        if not candidates:
            timing["path"] = "web_empty_retrieval"
            return await self._web_or_no_hits(user_text)

        speculative = self._start_speculative_generate(user_text, candidates)
        rewrite = self._start_speculative_rewrite(user_text, candidates)
        try:
            return await self._answer_from(
                user_text, candidates, speculative, rewrite, started, timing
            )
        finally:
            # 冪等：正常路徑上任務已被 await，這裡不做事；提早 return 或例外
            # 逃出時才真正收掉，不留 orphan task。CRAG 放行時改寫結果用不到，
            # 也是在這裡被取消。
            _abandon_task(speculative)
            _abandon_task(rewrite)

    async def _answer_from(
        self,
        user_text: str,
        candidates: list[Document],
        speculative: "asyncio.Task[str] | None",
        rewrite: "asyncio.Task[RewrittenQuery] | None",
        started: float,
        timing: dict[str, Any],
    ) -> str:
        approved: list[Document] | None = candidates
        if self.crag_enabled:
            try:
                approved = await self._apply_crag(
                    user_text, candidates, started=started, rewrite=rewrite
                )
            except Exception:
                logger.exception(
                    "CRAG failed; degrading to generate without grade crag_grade=degraded"
                )
                # CRAG 是這條路徑唯一的相關性把關，它失效時不能就這樣放行。
                # 精排分數是這裡唯一還可信的訊號（Cohere 的 relevance_score
                # 有明確語意；降級到 VectorScoreReranker 時則是融合後的排名
                # 分數）。過不了門檻就走與「知識庫無資料」相同的路徑——
                # 寧可少答，不要拿不相關的內容生成醫療答案。
                timing["path"] = "kb_crag_degraded"
                approved = self._filter_by_degraded_score(candidates)
                if not approved:
                    logger.info("rag_fail code=%s crag_grade=degraded_below_floor",
                                RagFailCode.KB_EMPTY)
                    timing["path"] = "web_degraded_below_floor"
                    _abandon_task(speculative)
                    return await self._web_or_no_hits(user_text, rewrite)
            else:
                if approved is None:
                    timing["path"] = "web_crag_reject"
                    # 這裡就收掉投機生成，不要留到 `_answer` 的 finally——網搜
                    # 那段要跑 5~15 秒，留著等於讓一份確定不會用的 KB 生成整個
                    # 跑完。2026-09-16 實測 5 題有 3 題走這條路，每題白燒一次
                    # 2.9-7.4 秒的完整生成。牆鐘時間省不到（它本來就是並行的
                    # task），省的是 Gemini 的 token。
                    #
                    # 只是盡力而為：請求已經在路上，取消關掉的是連線，供應商那
                    # 端已經生成的 token 仍可能照算。
                    _abandon_task(speculative)
                    return await self._web_or_no_hits(user_text, rewrite)

        kb_answer = await self._resolve_generate(
            speculative, user_text, candidates, approved
        )
        ranked = approved
        if self._is_cannot_answer(kb_answer):
            marker = matched_cannot_answer_marker(kb_answer, CANNOT_ANSWER_MARKERS)
            preview = answer_preview(kb_answer)
            logger.info(
                "rag_fail code=%s matched_marker=%s answer_preview=%s",
                RagFailCode.MODEL_REFUSE,
                marker,
                preview,
            )
            # 不能留在 path=kb：這一題知識庫其實沒答出來，記成 kb 會讓分流門檻
            # 的校準把它當成「這個分數帶知識庫答得出來」的樣本，門檻被往下拉。
            # path 要在轉網搜之前就定下來：網搜那段不改 path，校準樣本才會留在
            # kb_model_refuse 這一格，與 web_crag_reject 分得開。
            timing["path"] = "kb_model_refuse"
            if self.web_fallback_enabled:
                # 與「知識庫沒命中」「CRAG 判不相關」走同一條路：知識庫有文件但
                # 模型判定答不出來，對使用者來說一樣是「官方網站還沒查過」。以前
                # 這裡直接回 MODEL_REFUSE 叫使用者換個說法，而 CRAG 判 incorrect
                # 的題目反而會去網搜——同一種「知識庫答不了」，兩條路待遇不同。
                return await self._web_or_no_hits(user_text, rewrite)
            return rag_fail(RagFailCode.MODEL_REFUSE)

        dead = await self._dead_source_urls(kb_answer, ranked, timing)
        return self._append_sources(kb_answer, ranked, dead)

    @staticmethod
    def _top_rerank_score(docs: list[Document]) -> float | None:
        """候選中最高的 Cohere `rerank_score`；沒有任何一篇帶這個鍵時回傳 None。

        只認 `rerank_score`：Cohere 的 relevance_score 是絕對校準的相關性分數，
        跨題可比；融合分數是逐題正規化或只看名次的分數，跨題比較沒有意義，拿來
        訂所有題目共用的門檻不成立。Cohere 失效時回傳 None，日誌就不輸出這個
        欄位（`_format_kv` 略過 None）——「沒有分數」必須與「分數很低」分得開，
        否則校準時會把 Cohere 失效的題目誤當成低分樣本。
        """
        scores = [
            float(raw)
            for doc in docs
            if isinstance(raw := doc.metadata.get("rerank_score"), (int, float))
        ]
        return round(max(scores), 4) if scores else None

    def _filter_by_degraded_score(self, docs: list[Document]) -> list[Document]:
        """只保留 Cohere `rerank_score` 達到門檻的文件。門檻為 0 時原樣回傳。

        **只認 `rerank_score`，不退回 `score`。** 沒有這個欄位就代表 Cohere
        也失效了（`VectorScoreReranker` 不寫這個鍵），此時整批視為不合格，
        走與「知識庫無資料」相同的路徑。

        為什麼不退回 `score`——這是量出來的，不是設計偏好。以 golden set 的
        22 題、110 篇降級路徑候選（36 相關／74 不相關）實測
        （`scripts/rag_degraded_floor_scan.py`）：

          - 融合分數（convex α=0.6）：相關文件均值 0.632、不相關 0.650。
            **不相關的比相關的還高**，中位數同向（0.606 / 0.619）。原因是
            min-max 是逐題正規化的，每題該腿的第一名恆為 1.0——「整批候選
            都不相關」的那一題，它最好的那筆照樣滿分。而這張網要擋的正是
            整批不相關的情況，正規化剛好把唯一有用的訊號抹掉。
          - 原始 cosine：相關 0.8941、不相關 0.8872，差 0.0069，分佈完全
            重疊（相關最低 0.854 < 不相關的 p75 0.897）。門檻在 0.05~0.80
            之間只會刷掉那 3 筆「BM25 撈到但向量沒撈到」的文件——那不是
            相關性過濾，是關掉 BM25 那條腿；拉到 0.90 才開始有效果，代價是
            一次丟掉 9 題的相關文件。

        也就是說這兩個尺度上都不存在有意義的門檻值。`rerank_score` 不同：
        Cohere 的 relevance_score 是絕對校準的分數，0.3 在它上面才有語意。

        另一個好處是這讓門檻與 `RAG_FUSION_MODE` 解耦。退回 `score` 時，
        RRF（兩腿滿分也才 1/61+1/61 ≈ 0.033）與凸組合（0~1）在同一個 0.3
        之下的行為天差地遠——切換融合模式會順帶改到醫療答案的把關，那是
        兩件不該綁在一起的事。

        行為變更的範圍（其餘情況與本次變更前完全相同）：只有「Cohere 不可用
        **且** 走純向量檢索（RAG_HYBRID_ENABLED=false）」這個組合會改變——
        該組合下 `score` 是 cosine（≈0.88 > 0.3），過去會放行，現在轉網搜。
        依上述量測，那個放行本來就近似「什麼都沒擋」；要維持舊行為請把
        `RAG_DEGRADED_MIN_SCORE` 設為 0，那也更誠實地描述它實際在做的事。
        """
        if self.degraded_min_score <= 0:
            return docs
        kept: list[Document] = []
        for doc in docs:
            raw = doc.metadata.get("rerank_score")
            if isinstance(raw, (int, float)) and float(raw) >= self.degraded_min_score:
                kept.append(doc)
        return kept

    async def _web_or_no_hits(
        self,
        query: str,
        rewrite: "asyncio.Task[RewrittenQuery] | None" = None,
    ) -> str:
        if not self.web_fallback_enabled or self.web_search is None:
            return self._fail(RagFailCode.KB_EMPTY)
        try:
            with stage_timer(logger, "rag_web_fallback"):
                search_queries = await self._search_queries_for_web(query, rewrite)
                if search_queries is None:
                    return await self.web_search.answer(query)
                return await self.web_search.answer(
                    query, search_queries=search_queries
                )
        except Exception:
            logger.exception("web fallback failed")
            return self._fail(RagFailCode.WEB_ERROR)

    def _start_speculative_rewrite(
        self, user_text: str, docs: list[Document]
    ) -> "asyncio.Task[RewrittenQuery] | None":
        """在 CRAG 分級開始前就把查詢改寫排進事件迴圈。

        改寫結果只有兩條路用得到：分級判 ambiguous（kb_query 重查知識庫）與
        判 incorrect（zh_terms／en_terms 網搜）。兩者都要等分級結束才知道，
        而改寫不依賴分級結果，所以與分級並行。2026-09-12 實測改寫（thinking
        low）1.2-3.3 秒、分級 1.6-3.7 秒：多數情況改寫先跑完，網搜路徑不必
        多等；ambiguous 路徑則省下原本排在分級之後的整段改寫。

        代價是分級判 correct 時這次改寫白跑（投機生成那段註解記錄過 84% 的
        題目判 correct），付的是 token 不是延遲，單次請求的 Gemini 併發也再多 1。
        """
        if not (self.crag_enabled and self.rewriter is not None):
            return None
        return asyncio.create_task(
            self._timed_rewrite(user_text, docs, speculative=True)
        )

    async def _timed_rewrite(
        self, user_text: str, docs: list[Document], *, speculative: bool
    ) -> RewrittenQuery:
        assert self.rewriter is not None
        # speculative 欄位的理由同 rag_generate：並行那次的 ms 與分級重疊，
        # 不能直接和序列的階段相加。
        with stage_timer(logger, "rag_crag_rewrite", speculative=speculative or None):
            return await self.rewriter.rewrite(user_text, docs)

    async def _await_rewrite(
        self,
        rewrite: "asyncio.Task[RewrittenQuery] | None",
        user_text: str,
        docs: list[Document],
    ) -> RewrittenQuery:
        """取用並行中的改寫；沒有並行任務時（例如檢索為空）當場改寫。

        `rag_rewrite_wait` 記的是分級結束後還得等改寫多久——這才是改寫讓
        使用者多等的時間，接近 0 代表被分級完全蓋掉。
        """
        if rewrite is None:
            return await self._timed_rewrite(user_text, docs, speculative=False)
        with stage_timer(logger, "rag_rewrite_wait"):
            return await rewrite

    async def _search_queries_for_web(
        self,
        user_text: str,
        rewrite: "asyncio.Task[RewrittenQuery] | None",
    ) -> RewrittenQuery | None:
        """網搜要用的改寫查詢。改寫失敗回 None，網搜退回用原句，不中斷回答。"""
        if self.rewriter is None:
            return None
        try:
            return await self._await_rewrite(rewrite, user_text, [])
        except Exception:
            logger.exception(
                "query rewrite failed; web search falls back to the original question"
            )
            return None

    @staticmethod
    def _fail(code: str) -> str:
        logger.info("rag_fail code=%s", code)
        return rag_fail(code)

    async def _retrieve_and_rerank(
        self, query: str, *, attempt: str = "first"
    ) -> list[Document]:
        # attempt 區分這是第一輪還是 CRAG 改寫後的第二輪：整段檢索＋精排會
        # 跑兩次，兩次的 ms 分不開就看不出「慢是因為跑了兩遍」。
        with stage_timer(logger, "rag_retrieve", attempt=attempt) as t_retrieve:
            docs = await self.retriever.ainvoke(query)
            t_retrieve["docs"] = len(docs)
        if not docs:
            return []
        # 拿完整排序（不是只拿 top_n）：文章層級去重必須看過全部候選才能
        # 判斷「這篇文章還有沒有更高分的 chunk 沒被算進去」，只截斷後的
        # top_n 會讓去重看不到被擠掉的候選，等於沒去重。
        with stage_timer(
            logger, "rag_rerank", attempt=attempt, docs_in=len(docs)
        ) as t_rerank:
            ranked = await self.reranker.rerank(query, docs, top_n=len(docs))
            t_rerank["docs_out"] = len(ranked)
        deduped = dedup_ranked_docs(ranked, max_per_article=self.max_chunks_per_article)
        return deduped[: self.rerank_top_n]

    async def _apply_crag(
        self,
        user_text: str,
        ranked: list[Document],
        *,
        started: float,
        rewrite: "asyncio.Task[RewrittenQuery] | None" = None,
    ) -> list[Document] | None:
        """回傳可用於生成的 docs；None 表示知識庫不足。

        *started* 是本次 answer 的 `time.perf_counter()` 起點，供改寫第二輪的
        時間預算判斷（見 DEFAULT_CRAG_REWRITE_BUDGET_SECONDS）。*rewrite* 是與
        分級並行的改寫任務（見 `_start_speculative_rewrite`）。
        """
        assert self.grader is not None
        with stage_timer(logger, "rag_crag_grade", attempt="first") as t_grade:
            grade = await self.grader.grade(user_text, ranked)
            t_grade["grade"] = grade.value
        logger.info("crag_grade=%s", grade.value)

        if grade is Grade.CORRECT:
            return ranked

        if grade is Grade.INCORRECT:
            return None

        # ambiguous
        if self.rewriter is None:
            logger.info("crag_grade=ambiguous_no_rewriter")
            return None

        elapsed = time.perf_counter() - started
        if self._rewrite_budget_exhausted(elapsed):
            # 拿第一輪的 ranked 生成。grader 說的是「有關但資訊不足」，不是
            # 「無關」，而 prompt 的「內容不足請說不知道」與 _is_cannot_answer
            # 仍在後面把關。
            logger.info(
                "crag_grade=ambiguous_budget_exhausted elapsed_s=%.1f budget_s=%.1f",
                elapsed,
                self.crag_rewrite_budget_seconds,
            )
            return ranked

        try:
            rewritten = await self._await_rewrite(rewrite, user_text, ranked)
        except Exception:
            logger.exception(
                "CRAG rewrite failed; degrading to generate crag_grade=rewrite_degraded"
            )
            return ranked

        second = await self._retrieve_and_rerank(rewritten.kb_query, attempt="rewrite")
        if not second:
            logger.info("crag_grade=ambiguous_exhausted empty_retry")
            return None

        with stage_timer(logger, "rag_crag_grade", attempt="rewrite") as t_grade2:
            grade2 = await self.grader.grade(rewritten.kb_query, second)
            t_grade2["grade"] = grade2.value
        logger.info("crag_grade=%s after_rewrite", grade2.value)
        if grade2 is Grade.CORRECT:
            return second
        return None

    def _rewrite_budget_exhausted(self, elapsed_seconds: float) -> bool:
        """已花時間是否用完改寫預算。預算 <= 0 視為不設限（沿用本檔其他門檻的慣例）。"""
        budget = self.crag_rewrite_budget_seconds
        return budget > 0 and elapsed_seconds >= budget

    @staticmethod
    def _build_context(docs: list[Document]) -> str:
        """組出帶編號與出處標頭的 context。

        標頭只放 source_name 與 original_title，**不放 url** —— url 進 context
        會佔 token，且模型可能改寫或杜撰網址。url 由 `_append_sources`
        依編號對應回填。
        """
        blocks: list[str] = []
        for idx, doc in enumerate(docs, start=1):
            parts: list[str] = []
            source = str(doc.metadata.get("source_name") or "").strip()
            title = str(doc.metadata.get("original_title") or "").strip()
            if source:
                parts.append(f"來源：{source}")
            if title:
                parts.append(f"標題：{title}")
            header = f"[{idx}]" + (f" {'｜'.join(parts)}" if parts else "")
            blocks.append(f"{header}\n{doc.page_content}")
        return "\n\n".join(blocks)

    def _start_speculative_generate(
        self, user_text: str, docs: list[Document]
    ) -> "asyncio.Task[str] | None":
        """在 CRAG 分級開始前就把生成排進事件迴圈。"""
        if not self.speculative_generate:
            return None
        return asyncio.create_task(
            self._generate_answer(user_text, docs, speculative=True)
        )

    async def _resolve_generate(
        self,
        speculative: "asyncio.Task[str] | None",
        user_text: str,
        candidates: list[Document],
        approved: list[Document],
    ) -> str:
        """取用投機結果，或丟棄後以放行的 docs 重新生成。

        判斷用 identity（`approved is candidates`）而非內容比對：CRAG 判
        `correct` 時原封不動回傳同一個 list，走改寫第二輪時回的是另一個
        list，降級過濾有門檻時也會建新 list——identity 恰好把「送去生成的
        docs 有沒有被換掉」問乾淨。

        重點是判錯的方向：identity 為假但內容其實相同時，只是白重生一次，
        慢一點而已；**不可能**發生「拿未經放行的 docs 生成的答案回給使用
        者」。這個不對稱是本最佳化能碰醫療答案的前提。
        """
        if speculative is None:
            return await self._generate_answer(user_text, approved)
        if approved is candidates:
            logger.info("speculative_generate=hit")
            return await speculative
        logger.info("speculative_generate=miss")
        _abandon_task(speculative)
        return await self._generate_answer(user_text, approved)

    async def _generate_answer(
        self, question: str, docs: list[Document], *, speculative: bool = False
    ) -> str:
        context = self._build_context(docs)
        messages = build_rag_prompt().format_messages(
            question=question, context=wrap_context(context)
        )
        # speculative 欄位是必要的：投機那次與分級並行，它的 ms 會與
        # rag_crag_grade 重疊，直接拿去和序列版本相加會重複計算。
        with stage_timer(
            logger, "rag_generate", docs=len(docs), speculative=speculative or None
        ):
            rag_result = await self.gemini_service.chat_model.ainvoke(messages)
        # `content_to_text` 而非 `str()`：Gemini 開著 thinking 時 `.content`
        # 回的是 list-of-parts（`[{"type": "text", "text": "…", "extras":
        # {"signature": "<數千字 base64>"}}]`），`str()` 會把整個 Python
        # repr 連同簽章一起變成「答案」。實測一則 400 字的衛教回覆會被包成
        # 4,600~7,000 字，之後全程當作答案文字傳遞——進 agent 的 context、
        # 進引用解析、也會進卡片。
        # 模型回空字串就是答不出來，直接給拒答標記。原本退回
        # rag.generate_fallback（「抱歉，我目前找不到相關資料」），再靠字眼比對
        # 轉成拒答；拒答改成只認標記後，那段文案會被當成答案送出去。
        answer_text = content_to_text(rag_result.content) or NO_ANSWER_SENTINEL
        return answer_text

    @staticmethod
    def _is_cannot_answer(text: str) -> bool:
        return (
            matched_cannot_answer_marker(text, CANNOT_ANSWER_MARKERS) != "<none>"
        )

    @staticmethod
    def _doc_url(doc: Document) -> str:
        return str(doc.metadata.get("url") or "").strip()

    @staticmethod
    def _cited_urls(answer_text: str, docs: list[Document]) -> list[str]:
        """只取答案真的引用到的那幾筆的網址，依引用順序、去重。

        不查全部 `ranked`：沒被引用的 doc 不會出現在來源清單裡，為它們付
        HTTP 往返是純粹的延遲，也會用不相干的網址稀釋 LRU 快取。
        """
        urls: list[str] = []
        seen: set[str] = set()
        for idx in cited_indices(answer_text):
            if idx < 1 or idx > len(docs):
                continue
            url = RagAnswerService._doc_url(docs[idx - 1])
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)
        return urls

    async def _dead_source_urls(
        self, answer_text: str, docs: list[Document], timing: dict[str, Any]
    ) -> frozenset[str]:
        """判定哪些被引用的網址現在打不開。關閉或失敗時回空集合。"""
        if self.link_checker is None:
            return frozenset()
        urls = self._cited_urls(answer_text, docs)
        if not urls:
            return frozenset()
        with stage_timer(logger, "rag_link_check", checked=len(urls)) as lc_timing:
            dead = await dead_urls(self.link_checker, urls)
            lc_timing["dead"] = len(dead)
        if dead:
            # 記下實際被降級的網址：這是 link rot 的唯一可觀測訊號，也是
            # 之後要不要回頭清庫（重新 ingest 或下架該來源）的依據。
            logger.info(
                "citation_link_dead count=%d urls=%s", len(dead), sorted(dead)
            )
        timing["dead_citations"] = len(dead)
        return dead

    @staticmethod
    def _source_label(doc: Document, url: str | None = None) -> str | None:
        """來源顯示字串；無 url 時退回「來源名｜標題」，兩者皆無則回 None。

        *url* 為 None 時取 metadata 原值。呼叫端會在網址判定為打不開時改傳
        空字串——把「死掉的 url」完全等同於「沒有 url」，既有的退回邏輯就
        原封不動地變成降級路徑，不需要新增一種顯示分支。
        """
        source = str(doc.metadata.get("source_name") or "").strip()
        url = RagAnswerService._doc_url(doc) if url is None else url.strip()
        title = str(doc.metadata.get("original_title") or "").strip()
        if url:
            return f"{source}：{url}" if source else url
        if title:
            return f"{source}｜{title}" if source else title
        return None

    @staticmethod
    def _source_key(doc: Document, url: str | None = None) -> str:
        """判定「同一個來源」的鍵；有 url 用 url，否則用來源名＋標題。

        *url* 的語意同 `_source_label`。傳空字串進來時去重會退回
        「來源名＋標題」，與這筆來源在顯示上的身分保持一致——顯示成同一行
        的兩筆，去重也必須把它們當成同一筆。
        """
        url = RagAnswerService._doc_url(doc) if url is None else url.strip()
        if url:
            return f"url:{url}"
        source = str(doc.metadata.get("source_name") or "").strip()
        title = str(doc.metadata.get("original_title") or "").strip()
        return f"meta:{source}|{title}"

    @staticmethod
    def _source_ref(doc: Document, index: int, url: str | None = None) -> SourceRef:
        """從 metadata 直接取值組成結構化來源。

        刻意不重用 `_source_label` 的輸出：那個字串是給純文字清單看的，
        用全形冒號把來源名與網址黏在一起，而來源名本身也可能含冒號，
        反解回來不可靠。
        """
        source = str(doc.metadata.get("source_name") or "").strip()
        title = str(doc.metadata.get("original_title") or "").strip()
        url = RagAnswerService._doc_url(doc) if url is None else url.strip()
        label = source or title or f"來源 {index}"
        return SourceRef(index=index, label=label, url=url)

    @staticmethod
    def _append_sources(
        answer_text: str,
        docs: list[Document],
        dead_urls: frozenset[str] = frozenset(),
    ) -> str:
        """組出來源清單。*dead_urls* 中的網址一律降級為「不顯示網址」。

        存活檢查本身在 `_dead_source_urls` 做完才進來，這個函式維持純函式
        性質（同一組輸入永遠組出同一個輸出），網路 I/O 不混進呈現邏輯裡。
        預設空集合＝完全是導入檢查前的行為。

        降級不是丟棄：該筆來源仍佔一個編號、仍出現在清單裡，只是退回
        「來源名｜標題」而沒有網址，呈現層也就不會給它一顆點了打不開的
        按鈕。這符合 rag-responses「缺 url 不得靜默丟棄」——答案本文的
        引用標記指得到東西，使用者也看得到我們依據的是哪個機構的資料。
        """
        cited = cited_indices(answer_text)
        if not cited:
            logger.info("citation_missing docs=%d", len(docs))
            set_request_rag_sources(())
            return answer_text

        key_to_new: dict[str, int] = {}
        renumber: dict[int, int] = {}
        source_lines: list[str] = []
        source_refs: list[SourceRef] = []

        for old_idx in cited:
            if old_idx < 1 or old_idx > len(docs):
                continue
            doc = docs[old_idx - 1]
            raw_url = RagAnswerService._doc_url(doc)
            url = "" if raw_url in dead_urls else raw_url
            label = RagAnswerService._source_label(doc, url)
            if label is None:
                # 網址死了、又沒有來源名與標題可退回，這筆就真的無從顯示。
                # 與導入前「metadata 全空」走的是同一條路徑。
                continue
            key = RagAnswerService._source_key(doc, url)
            existing = key_to_new.get(key)
            if existing is not None:
                renumber[old_idx] = existing
                continue
            if len(source_lines) >= CITE_TOP_K:
                continue
            new_idx = len(source_lines) + 1
            key_to_new[key] = new_idx
            renumber[old_idx] = new_idx
            source_lines.append(f"[{new_idx}] {label}")
            # 與文字清單同一個迴圈、同一個 new_idx，兩者編號因此不可能漂移。
            source_refs.append(RagAnswerService._source_ref(doc, new_idx, url))

        def _replace(match: re.Match[str]) -> str:
            mapped = renumber.get(int(match.group(1)))
            return f"[{mapped}]" if mapped is not None else ""

        # 先改寫內文再決定要不要附清單：即使一筆來源都解析不出來，
        # 那些指向不存在來源的標記仍必須從答案中移除。
        body = _CITATION_RE.sub(_replace, answer_text)

        if not source_lines:
            logger.info("citation_unresolved cited=%s docs=%d", cited, len(docs))
            set_request_rag_sources(())
            return body

        set_request_rag_sources(source_refs)
        heading = t("agent.sources_heading")
        return f"{body}\n\n{heading}\n" + "\n".join(source_lines)


def _abandon_task(task: "asyncio.Task[str] | None") -> None:
    """收掉不再需要的投機任務。冪等，可安全重複呼叫。

    已完成且帶例外時要主動取出例外，否則 asyncio 會噴
    "Task exception was never retrieved"——那條路上的失敗本來就要丟棄，
    不該變成 log 噪音。
    """
    if task is None or task.cancelled():
        return
    if not task.done():
        task.cancel()
        return
    task.exception()


def dedup_ranked_docs(
    docs: list[Document], *, max_per_article: int
) -> list[Document]:
    """精排後之文章層級去重：同一篇文章最多留 max_per_article 個 chunk。

    *docs* 必須已依相關性排序（分數高在前，例如 reranker 的完整排序結果）；
    本函式只依序掃描並過濾超出上限的 chunk，**不重新排序**，因此保留的
    chunk 之間相對順序與輸入一致。

    文章身分判定沿用 `RagAnswerService._source_key`（有 url 用 url，無 url
    用 source_name+original_title），不重新發明身分邏輯，確保與
    `_append_sources` 判斷「同一來源」的邏輯一致。

    `max_per_article < 1` 視為 1（至少保留每篇文章的最高分 chunk），不拋例外。
    """
    cap = max_per_article if max_per_article >= 1 else 1
    counts: dict[str, int] = {}
    out: list[Document] = []
    for doc in docs:
        key = RagAnswerService._source_key(doc)
        count = counts.get(key, 0)
        if count >= cap:
            continue
        counts[key] = count + 1
        out.append(doc)
    return out
