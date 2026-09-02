import logging
import re

from langchain_core.documents import Document

from app.services.gemini import GeminiService
from app.i18n.messages import t
from app.services.rag.cannot_answer import (
    CANNOT_ANSWER_MARKERS,
    answer_preview,
    matched_cannot_answer_marker,
)
from app.services.rag.answer_prompts import build_rag_prompt, wrap_context
from app.services.rag.cohere_reranker import Reranker, VectorScoreReranker
from app.core.rag_sources import SourceRef, set_request_rag_sources
from app.services.rag.fail_messages import (
    NO_ANSWER_MESSAGE,
    NO_HITS_MESSAGE,
    RagFailCode,
    rag_fail,
)
from app.services.rag.query_rewriter import QueryRewriter
from app.services.rag.retrieval_grader import Grade, RetrievalGrader
from app.services.rag.retriever import MongoAtlasVectorRetriever
from app.services.rag.web_search_service import WebSearchService

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

    async def answer(self, user_text: str) -> str:
        ranked = await self._retrieve_and_rerank(user_text)
        if not ranked:
            return await self._web_or_no_hits(user_text)

        if self.crag_enabled:
            try:
                ranked = await self._apply_crag(user_text, ranked)
            except Exception:
                logger.exception(
                    "CRAG failed; degrading to generate without grade crag_grade=degraded"
                )
                # CRAG 是這條路徑唯一的相關性把關，它失效時不能就這樣放行。
                # 精排分數是這裡唯一還可信的訊號（Cohere 的 relevance_score
                # 有明確語意；降級到 VectorScoreReranker 時則是融合後的排名
                # 分數）。過不了門檻就走與「知識庫無資料」相同的路徑——
                # 寧可少答，不要拿不相關的內容生成醫療答案。
                ranked = self._filter_by_degraded_score(ranked)
                if not ranked:
                    logger.info("rag_fail code=%s crag_grade=degraded_below_floor",
                                RagFailCode.KB_EMPTY)
                    return await self._web_or_no_hits(user_text)
            else:
                if ranked is None:
                    return await self._web_or_no_hits(user_text)

        kb_answer = await self._generate_answer(user_text, ranked)
        if self._is_cannot_answer(kb_answer):
            marker = matched_cannot_answer_marker(kb_answer, CANNOT_ANSWER_MARKERS)
            preview = answer_preview(kb_answer)
            logger.info(
                "rag_fail code=%s matched_marker=%s answer_preview=%s",
                RagFailCode.MODEL_REFUSE,
                marker,
                preview,
            )
            return rag_fail(RagFailCode.MODEL_REFUSE)

        return self._append_sources(kb_answer, ranked)

    def _filter_by_degraded_score(self, docs: list[Document]) -> list[Document]:
        """只保留精排分數達到門檻的文件。門檻為 0 時原樣回傳。

        分數取 `rerank_score`（Cohere）優先，退回 `score`（向量／RRF 融合）。
        兩者都沒有時視為不合格：拿不到分數就無從判斷相關性，而這條路徑的
        前提正是「唯一的把關已經失效」。
        """
        if self.degraded_min_score <= 0:
            return docs
        kept: list[Document] = []
        for doc in docs:
            raw = doc.metadata.get("rerank_score")
            if not isinstance(raw, (int, float)):
                raw = doc.metadata.get("score")
            if isinstance(raw, (int, float)) and float(raw) >= self.degraded_min_score:
                kept.append(doc)
        return kept

    async def _web_or_no_hits(self, query: str) -> str:
        if not self.web_fallback_enabled or self.web_search is None:
            return self._fail(RagFailCode.KB_EMPTY)
        try:
            return await self.web_search.answer(query)
        except Exception:
            logger.exception("web fallback failed")
            return self._fail(RagFailCode.WEB_ERROR)

    @staticmethod
    def _fail(code: str) -> str:
        logger.info("rag_fail code=%s", code)
        return rag_fail(code)

    async def _retrieve_and_rerank(self, query: str) -> list[Document]:
        docs = await self.retriever.ainvoke(query)
        if not docs:
            return []
        # 拿完整排序（不是只拿 top_n）：文章層級去重必須看過全部候選才能
        # 判斷「這篇文章還有沒有更高分的 chunk 沒被算進去」，只截斷後的
        # top_n 會讓去重看不到被擠掉的候選，等於沒去重。
        ranked = await self.reranker.rerank(query, docs, top_n=len(docs))
        deduped = dedup_ranked_docs(ranked, max_per_article=self.max_chunks_per_article)
        return deduped[: self.rerank_top_n]

    async def _apply_crag(
        self, user_text: str, ranked: list[Document]
    ) -> list[Document] | None:
        """回傳可用於生成的 docs；None 表示知識庫不足。"""
        assert self.grader is not None
        grade = await self.grader.grade(user_text, ranked)
        logger.info("crag_grade=%s", grade.value)

        if grade is Grade.CORRECT:
            return ranked

        if grade is Grade.INCORRECT:
            return None

        # ambiguous
        if self.rewriter is None:
            logger.info("crag_grade=ambiguous_no_rewriter")
            return None

        try:
            rewritten = await self.rewriter.rewrite(user_text, ranked)
        except Exception:
            logger.exception(
                "CRAG rewrite failed; degrading to generate crag_grade=rewrite_degraded"
            )
            return ranked

        second = await self._retrieve_and_rerank(rewritten)
        if not second:
            logger.info("crag_grade=ambiguous_exhausted empty_retry")
            return None

        grade2 = await self.grader.grade(rewritten, second)
        logger.info("crag_grade=%s after_rewrite", grade2.value)
        if grade2 is Grade.CORRECT:
            return second
        return None

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

    async def _generate_answer(self, question: str, docs: list[Document]) -> str:
        context = self._build_context(docs)
        messages = build_rag_prompt().format_messages(
            question=question, context=wrap_context(context)
        )
        rag_result = await self.gemini_service.chat_model.ainvoke(messages)
        answer_text = rag_result.content or t("rag.generate_fallback")
        if not isinstance(answer_text, str):
            answer_text = str(answer_text)
        return answer_text

    @staticmethod
    def _is_cannot_answer(text: str) -> bool:
        return (
            matched_cannot_answer_marker(text, CANNOT_ANSWER_MARKERS) != "<none>"
        )

    @staticmethod
    def _source_label(doc: Document) -> str | None:
        """來源顯示字串；無 url 時退回「來源名｜標題」，兩者皆無則回 None。"""
        source = str(doc.metadata.get("source_name") or "").strip()
        url = str(doc.metadata.get("url") or "").strip()
        title = str(doc.metadata.get("original_title") or "").strip()
        if url:
            return f"{source}：{url}" if source else url
        if title:
            return f"{source}｜{title}" if source else title
        return None

    @staticmethod
    def _source_key(doc: Document) -> str:
        """判定「同一個來源」的鍵；有 url 用 url，否則用來源名＋標題。"""
        url = str(doc.metadata.get("url") or "").strip()
        if url:
            return f"url:{url}"
        source = str(doc.metadata.get("source_name") or "").strip()
        title = str(doc.metadata.get("original_title") or "").strip()
        return f"meta:{source}|{title}"

    @staticmethod
    def _source_ref(doc: Document, index: int) -> SourceRef:
        """從 metadata 直接取值組成結構化來源。

        刻意不重用 `_source_label` 的輸出：那個字串是給純文字清單看的，
        用全形冒號把來源名與網址黏在一起，而來源名本身也可能含冒號，
        反解回來不可靠。
        """
        source = str(doc.metadata.get("source_name") or "").strip()
        title = str(doc.metadata.get("original_title") or "").strip()
        url = str(doc.metadata.get("url") or "").strip()
        label = source or title or f"來源 {index}"
        return SourceRef(index=index, label=label, url=url)

    @staticmethod
    def _append_sources(answer_text: str, docs: list[Document]) -> str:
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
            label = RagAnswerService._source_label(doc)
            if label is None:
                continue
            key = RagAnswerService._source_key(doc)
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
            source_refs.append(RagAnswerService._source_ref(doc, new_idx))

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
