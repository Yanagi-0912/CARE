"""已查核主張比對：拿正規化後的主張，去知識庫比對「這則主張是不是已經被查核過」。

結構照抄 `retriever.py` 的 `MongoAtlasVectorRetriever`（同樣 `$vectorSearch`、
同樣 `AsyncIOMotorClient`、同樣 lazy `_ensure_collection()`）。`$vectorSearch`
的 `path` 沿用與內容檢索**同一個** `embedding` 向量欄位（design.md 決策 2：
claim 本身是純文字、資料庫裡沒有另外的 claim 向量，改為直接查既有的
embedding 索引，結果裡挑 `verdict` 非空者）。差別只在於多一道 `verdict`
過濾，以及輸出型別是單一 `ClaimMatch | None` 而非 `list[Document]`
——這裡要的是「有沒有比對到」，不是排序後的候選清單。

`vector_field`（預設 `"embedding"`，供 `$vectorSearch` 用）與 `claim_field`
（預設 `"claim"`，只用來把命中文件的主張原文讀出來供 `ClaimMatch.claim`
參考）是兩個不同欄位，不可合而為一：2026-08-18 對 production Atlas 實測
證實，把兩者當同一個參數、預設指向 `"claim"` 送進 `$vectorSearch` 的
`path`，會讓 MongoDB 回 `OperationFailure`（claim 不是向量索引），而
`match()` 的 fail-open 設計會把這個例外整個吞掉、每次都靜默降級成
「未命中」——這正是一開始只有 `claim_field` 這一個參數時留下的陷阱。

台灣事實查核中心（TFC）文章的所有 chunk 共用同一篇的 `claim`／`verdict`
（它們是文章層級屬性），向量檢索因此會把同一篇的多個 chunk 都撈回來；
若不先以 `url` 去重，top-k 會被單篇洗版，讓真正該進來比較的其他文章被擠掉。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.request_logging import stage_timer

logger = logging.getLogger(__name__)

_NUM_CANDIDATES_MULTIPLIER = 30

# 真正生效的門檻由 CLAIM_MATCH_MIN_SCORE 注入（見 design.md 決策 3：
# 誤配是本設計唯一嚴重的失效模式，門檻寧缺勿濫）。這裡的預設值只是建構子
# 沒被覆寫時的備援，tasks 1.2 已完成校準，數字與理由見 config.py。
DEFAULT_CLAIM_MATCH_MIN_SCORE = 0.86

# spec.md「判定值僅得來自已查核來源」明訂的五個合法值。獨立宣告而不從
# eval_scoring.py 匯入同名的 VALID_VERDICTS，是刻意比照該檔案自己的
# 理由（見該檔案該常數的註解）：兩邊只需要五個字串值，不需要因此互相
# 依賴，也不想讓 production 的 matcher 反過來依賴 eval 專用的模組。
#
# 這不是防禦性程式設計的空氣：CARE-data 的 LEGACY_PREFIX_VERDICT 對照表
# 真的漏收過「正確」前綴，讓那些文章的 verdict 欄位被寫成 None（task-8-
# report.md）。None 會被下面 `_best_verdicted_match` 的 `if not doc.get
# ("verdict")` 濾掉，但同一類上游資料清洗錯誤換一種形狀出現時——例如
# 誤植空白、TFC 改用詞、對照表這次漏收的不是整條、而是把值寫錯成其他
# 非五合法值之一的字串——不會是 falsy，會直接以未知字串的姿態透傳到
# 呈現層。verdict_flex.py 的配色表只認得五個合法值，未知字串會落到
# 「事實釐清／證據不足」共用的中性灰（design.md 決策 6 特別保留給「不
# 判真偽」的顏色），讓一則已被人工判定「錯誤」的謠言以中性色呈現——
# 視覺語意反向，而且沒有任何 log 能追。
_VALID_VERDICTS = frozenset({"錯誤", "部分錯誤", "正確", "事實釐清", "證據不足"})

# 分數差距在這個範圍內視為「一樣相關」，改以發布日期決勝。
#
# 為什麼需要：實測 8 題常見謠言，有 3 題的前兩名分差小於 0.005，其中
# 「打疫苗會改變DNA」只差 0.0004（2022 年與 2021 年的兩篇報告）——那個差距
# 沒有語意意義，誰勝出基本上是隨機的。與其隨機，不如給使用者較新的那篇。
#
# 刻意不做「一律取新」：分數差距顯著時代表真的比較相關，日期不該蓋過相關性。
# 也刻意不做日期範圍過濾——查核報告不會過期，2021 年查過的謠言 2026 年重傳時
# 那份報告依然有效，用日期硬篩會擋掉大量仍然正確的答案。
_SCORE_TIE_EPSILON = 0.005


@dataclass(frozen=True)
class ClaimMatch:
    claim: str
    verdict: str
    verdict_slug: str
    url: str
    title: str
    content: str
    score: float
    published_at: str = ""
    # 這則判定是誰做的。2026-09-19 之前庫裡只有 TFC，呈現層因此寫死「判定來源：
    # 台灣事實查核中心」；補上政府闢謠與 Cofacts 之後那句話會說謊，所以要跟著
    # 判定一起帶出來。
    source_name: str = ""


class ClaimMatcher(Protocol):
    async def match(self, claim: str) -> ClaimMatch | None: ...


class MongoAtlasClaimMatcher:
    """正規化主張 → embedding → MongoDB `$vectorSearch`（既有內容向量欄位）→ 是否已被查核過。"""

    def __init__(
        self,
        *,
        embeddings: Any,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        index_name: str,
        vector_field: str = "embedding",
        claim_field: str = "claim",
        content_field: str = "chunk_content",
        k: int = 10,
        min_score: float = DEFAULT_CLAIM_MATCH_MIN_SCORE,
    ) -> None:
        self.embeddings = embeddings
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.index_name = index_name
        self.vector_field = vector_field
        self.claim_field = claim_field
        self.content_field = content_field
        self.k = k
        self.min_score = min_score
        self._collection: Any = None

    def _ensure_collection(self) -> Any:
        if self._collection is not None:
            return self._collection

        missing = [
            name
            for name, value in (
                ("MONGODB_URI", self.mongo_uri),
                ("MONGODB_DB", self.db_name),
                ("MONGODB_COLLECTION", self.collection_name),
                # 這裡曾經寫成 "MONGODB_CLAIM_VECTOR_INDEX"，是設計初稿
                # （另建 claim 專用向量索引）留下的名字；design.md 決策 2
                # 已經改成沿用既有的 MONGODB_VECTOR_INDEX，`.env`／
                # config.py 都不存在 MONGODB_CLAIM_VECTOR_INDEX 這個變數。
                # 錯誤訊息沒有同步改，維運照著訊息去找一個不存在的設定，
                # 正是這條分支已經付過一次代價的失效形態（design.md
                # Migration Plan 同一個過時名稱也一併訂正）。
                ("MONGODB_VECTOR_INDEX", self.index_name),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing {', '.join(missing)}")

        client = AsyncIOMotorClient(self.mongo_uri)
        self._collection = client[self.db_name][self.collection_name]
        return self._collection

    async def match(self, claim: str) -> ClaimMatch | None:
        """比對入口。實作在 `_resolve`，這裡只負責觀測。

        每次比對都留一行 `stage=claim_match`（命中與降級都記）。過去只有例外
        路徑有 log，於是「未命中」在日誌上完全沒有痕跡——分不出是候選一篇都
        沒有、非法 verdict 被擋下、還是差 0.01 分沒過門檻。三者要採取的行動
        完全不同（補語料／查 ETL／調門檻），而 `CLAIM_MATCH_MIN_SCORE` 是否
        還在對的位置，除了線上分數分佈之外沒有別的依據可看。

        `candidates`／`top`／`runner_up` 必須在挑出最佳解**之前**記錄：
        `_best_verdicted_match` 一旦收斂成單筆，這三個數字就再也拿不回來。
        """
        with stage_timer(
            logger,
            "claim_match",
            claim_len=len(claim or ""),
            threshold=self.min_score,
        ) as obs:
            # 預設 error，由 `_resolve` 的各條出口覆寫。沒被覆寫就代表有例外
            # 從 `_resolve` 逸散出去——stage_timer 在 finally 記錄，那條路徑
            # 一樣會留下這行。
            obs["outcome"] = "error"
            return await self._resolve(claim, obs)

    async def _resolve(self, claim: str, obs: dict[str, Any]) -> ClaimMatch | None:
        # 對齊既有 RAG_HYBRID_ENABLED 在文字索引未建時的 fail-open 處置：
        # 這裡的任何失敗（缺設定、索引不存在、連線失敗、回傳格式異常）都不該
        # 中斷查核流程，一律降級為「未命中」，交由上層判定「證據不足」。
        try:
            raw_docs = await self._search(claim)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "claim match failed, degrading to no match: %s", exc, exc_info=True
            )
            obs["outcome"] = "search_failed"
            return None

        candidates = self._dedup_by_url(raw_docs)
        obs["candidates"] = len(candidates)
        if candidates:
            obs["top"] = round(float(candidates[0]["score"]), 4)
            if len(candidates) > 1:
                obs["runner_up"] = round(float(candidates[1]["score"]), 4)

        best = self._best_verdicted_match(candidates)
        if best is None:
            obs["outcome"] = "no_candidates"
            return None

        score = best["score"]
        # 平手改判日期的分支實際上多久觸發一次，只有這裡量得到（週報記的
        # 「8 題有 3 題分差小於 0.005」是離線抽樣，不是線上分佈）。
        if candidates and best is not candidates[0]:
            obs["tie_break"] = True
        if score < self.min_score:
            obs["outcome"] = "below_threshold"
            self._log_detail(claim, best)
            return None

        verdict = str(best.get("verdict") or "")
        if verdict not in _VALID_VERDICTS:
            # 上游資料清洗一旦出錯（見 _VALID_VERDICTS 註解的真實事故），這裡
            # 是最後一道防線：寧可整篇當未命中降級為「證據不足」，也不要讓
            # 呈現層拿一個非法字串去配色，配出語意相反的中性灰（design.md
            # 決策 6 專門保留給「不判真偽」的顏色）。當未命中處理、不嘗試在
            # 候選裡挑下一筆——同一次查詢出現非法 verdict 已是資料異常訊號，
            # 不該假裝正常繼續配對。
            logger.warning(
                "claim match has invalid verdict %r (url=%s), degrading to no match",
                verdict,
                best.get("url"),
            )
            obs["outcome"] = "invalid_verdict"
            return None

        content = str(best.get(self.content_field) or "")
        if not content:
            # 理由改寫（service.py._rewrite_reasoning）完全靠這個欄位改寫；
            # 沒有內容就沒有依據可改寫，繼續往下走只會讓語言模型憑空生成一段
            # 「查核報告怎麼看待這則說法」——那正是決策 1 要杜絕的事（判定
            # 沒有依據就不該發卡片）。這也是 content_field 接線疏漏（例如
            # dependencies.py 忘記傳 settings.MONGODB_TEXT_FIELD）的最後一道
            # 防線。
            logger.warning(
                "claim match has empty content (url=%s), degrading to no match",
                best.get("url"),
            )
            obs["outcome"] = "empty_content"
            return None

        obs["outcome"] = "hit"
        obs["verdict"] = verdict
        obs["url"] = str(best.get("url") or "") or None
        return ClaimMatch(
            claim=str(best.get(self.claim_field) or ""),
            verdict=verdict,
            verdict_slug=str(best.get("verdict_slug") or ""),
            url=str(best.get("url") or ""),
            title=str(best.get("original_title") or ""),
            content=content,
            score=float(score),
            published_at=str(best.get("published_at") or ""),
            source_name=str(best.get("source_name") or ""),
        )

    async def _search(self, claim: str) -> list[dict[str, Any]]:
        query_embedding = await self.embeddings.aembed_query(claim)
        if not query_embedding:
            return []

        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.index_name,
                    "path": self.vector_field,
                    "queryVector": query_embedding,
                    "numCandidates": self.k * _NUM_CANDIDATES_MULTIPLIER,
                    "limit": self.k,
                    # 前置過濾：只讓帶合法判定的文件參與相似度排名。
                    #
                    # 過去是取回 top-k 之後才用 $match 濾掉沒有 verdict 的
                    # 文件，那是結構性的召回上限——知識庫裡查核報告只佔約
                    # 三分之一，其餘是衛福部與食藥署的衛教文，而使用者問的
                    # 謠言題目往往兩邊都寫過，於是 top-10 可能整批是衛教文，
                    # 真正查核過那篇根本進不了候選。實測（五題常見謠言）：
                    #
                    #   問句                    後置 $match   前置 filter
                    #   微波爐加熱產生致癌物        4/10         10/10
                    #   感冒吃抗生素有用嗎          6/10         10/10
                    #   吃鳳梨心可以溶解血栓        7/10         10/10
                    #
                    # 拉高 numCandidates 解決不了，因為瓶頸是 limit。
                    #
                    # 條件寫成「落在五個合法判定內」而非「非空」，是因為
                    # Atlas 的字串前置過濾只支援等值與 $in，不支援 $exists；
                    # 而這個寫法同時把合法值約束下推到查詢層，與下游
                    # `_VALID_VERDICTS` 的檢核形成兩道一致的防線。
                    "filter": {"verdict": {"$in": sorted(_VALID_VERDICTS)}},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    self.claim_field: 1,
                    "verdict": 1,
                    "verdict_slug": 1,
                    "url": 1,
                    "original_title": 1,
                    "published_at": 1,
                    self.content_field: 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]
        return await self._ensure_collection().aggregate(pipeline).to_list(length=None)

    def _dedup_by_url(self, raw_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """濾掉沒有 verdict／沒有分數的文件，同一 url 只留最高分那筆，依分數排序。

        `$match` 已在 pipeline 裡濾過 verdict，這裡再做一次是防禦性的：
        假 collection（測試）或未來換掉的儲存層不保證真的執行了那個 stage。

        這段原本是 `_best_verdicted_match` 的前半。拆出來是為了讓 `match()`
        能在收斂成單筆之前，先把候選數與前兩名分數記進 stage log——挑完就
        再也拿不回來，而那正是校準門檻唯一的線上依據。
        """
        best_by_url: dict[str, dict[str, Any]] = {}
        for doc in raw_docs:
            if not doc.get("verdict"):
                continue
            score = doc.get("score")
            if not isinstance(score, (int, float)):
                continue
            url = str(doc.get("url") or "")
            current_best = best_by_url.get(url)
            if current_best is None or score > current_best["score"]:
                best_by_url[url] = doc

        return sorted(
            best_by_url.values(), key=lambda doc: doc["score"], reverse=True
        )

    def _best_verdicted_match(
        self, candidates: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """從已去重排序的候選裡取最高分；分數平手時改取較新的那篇。"""
        if not candidates:
            return None

        top_score = candidates[0]["score"]
        tied = [d for d in candidates if top_score - d["score"] <= _SCORE_TIE_EPSILON]
        if len(tied) == 1:
            return tied[0]
        # 日期是字串（"2026-03-11"），字典序即時間序；缺日期者排最後。
        return max(tied, key=lambda doc: str(doc.get("published_at") or ""))

    def _log_detail(self, claim: str, best: dict[str, Any]) -> None:
        """只在 DEBUG 落地的文字明細。

        `stage=claim_match` 那行刻意只有長度與分數、沒有問句原文——與
        `message_handler` 既有的 `stage=handle text_len=` 同一個慣例（日誌可能
        外送，使用者的健康問句不該預設進去）。

        但「差 0.02 分沒過門檻」這件事光看數字無法判斷該不該調門檻：要知道
        那篇沒過的報告是不是真的在講同一件事，必須看得到兩邊的文字。折衷是
        需要人工抽樣校準時開一段 `LOG_LEVEL=DEBUG`，平時不留。
        """
        if not logger.isEnabledFor(logging.DEBUG):
            return
        logger.debug(
            "claim_match_detail claim=%r top_title=%r top_score=%s",
            claim,
            str(best.get("original_title") or ""),
            best.get("score"),
        )


# 兩段式查詢第一段（halfvec 索引）要取幾倍候選再送進 float32 精算。
#
# 不沿用 `_NUM_CANDIDATES_MULTIPLIER`（30，即 300 筆）是因為 PG 這邊的成本
# 結構不同：精算階段要把候選的 float32 向量從 TOAST 讀出來算，3072 維乘上
# 候選數就是實際的 I/O。2026-09-19 在 care-vm 實測（k=10）：
#
#   候選 300 → 523 ms
#   候選 100 →  92 ms
#
# 而兩者的 top-10 完全重疊（10/10）——halfvec 的排序已經夠接近精確排序，
# 多出來的 200 筆候選只是白算。真正的召回保障是 iterative_scan，不是候選數。
_PG_CANDIDATES_MULTIPLIER = 10


class PgVectorClaimMatcher(MongoAtlasClaimMatcher):
    """與 `MongoAtlasClaimMatcher` 同樣的比對邏輯，但向量查詢走 pgvector。

    2026-09-19 向量從 Atlas 搬到 PostgreSQL（背景見
    `services/rag/pgvector_retriever.py` 的模組註解）。`health_articles_chunks`
    的 `embedding` 欄位已從 Atlas 清掉，原本的 `$vectorSearch` 會永遠回 0 筆，
    所以這條路徑必須跟著改。

    只覆寫 `_search()` 與 `_ensure_collection()`：去重、平手改判日期、verdict
    合法性檢核、fail-open 降級這些邏輯一行都沒動，全部沿用父類別。

    為什麼要兩段式查詢（halfvec 找候選 → float32 精算）：
        `CLAIM_MATCH_MIN_SCORE` 是 0.86，而且校準過（design.md 決策 3：誤配是
        這個設計唯一嚴重的失效模式）。HNSW 索引建在 halfvec 上，float32 轉
        float16 是有損的，門檻附近的案例可能因為小數點後幾位的差異而翻轉。
        所以先用 halfvec 的索引快速取一批候選，再用未失真的 float32 欄位重算
        距離排序——候選階段的誤差只影響「有沒有進候選」，最終分數則與 Atlas
        時期同樣是 float32 精度。
    """

    def __init__(
        self,
        *,
        embeddings: Any,
        dsn: str,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        table_name: str = "health_articles_chunks",
        vector_column: str = "embedding_half",
        exact_vector_column: str = "embedding",
        claim_field: str = "claim",
        content_field: str = "chunk_content",
        k: int = 10,
        min_score: float = DEFAULT_CLAIM_MATCH_MIN_SCORE,
    ) -> None:
        super().__init__(
            embeddings=embeddings,
            mongo_uri=mongo_uri,
            db_name=db_name,
            collection_name=collection_name,
            # 父類別的 _ensure_collection 會檢查 index_name，但 PG 版本用不到
            # Atlas 的向量索引；這裡給一個非空值讓那道檢查通過，實際上不會被用。
            index_name="unused-pgvector",
            claim_field=claim_field,
            content_field=content_field,
            k=k,
            min_score=min_score,
        )
        self.dsn = dsn
        self.table_name = table_name
        self.vector_column = vector_column
        self.exact_vector_column = exact_vector_column
        self._pool: Any = None

    async def _ensure_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        if not self.dsn:
            raise ValueError("Missing PGVECTOR_DSN")

        import asyncpg

        self._pool = await asyncpg.create_pool(
            self.dsn, min_size=1, max_size=2, command_timeout=10
        )
        return self._pool

    async def _search(self, claim: str) -> list[dict[str, Any]]:
        query_embedding = await self.embeddings.aembed_query(claim)
        if not query_embedding:
            return []

        vector_literal = "[" + ",".join(repr(float(v)) for v in query_embedding) + "]"
        candidate_limit = self.k * _PG_CANDIDATES_MULTIPLIER

        # verdict 的前置過濾在 WHERE 裡，與 Atlas 版的 $vectorSearch filter 同義
        # （父類別註解記了實測：後置過濾會讓召回從 10/10 掉到 4/10）。差別是
        # pgvector 0.8 的 iterative index scan 讓 HNSW 在有 WHERE 的情況下也能
        # 掃到足夠的結果，不會因為過濾而回不滿 k 筆。
        sql = (
            "WITH candidates AS ("
            f"  SELECT id, {self.exact_vector_column} AS exact_vec"
            f"  FROM {self.table_name}"
            f"  WHERE verdict = ANY($2) AND {self.vector_column} IS NOT NULL"
            f"  ORDER BY {self.vector_column} <=> $1::halfvec"
            "   LIMIT $3"
            ") "
            "SELECT id, exact_vec <=> $1::vector AS distance "
            "FROM candidates ORDER BY distance LIMIT $4"
        )

        pool = await self._ensure_pool()
        with stage_timer(logger, "claim_vector_search"):
            async with pool.acquire() as conn:
                await conn.execute("SET hnsw.iterative_scan = relaxed_order")
                rows = await conn.fetch(
                    sql,
                    vector_literal,
                    sorted(_VALID_VERDICTS),
                    candidate_limit,
                    self.k,
                )

        if not rows:
            return []

        # Atlas 的 vectorSearchScore 對 cosine 是 (1 + cos) / 2，而 pgvector 的
        # `<=>` 是 1 - cos，換算即 1 - distance/2。這一步不能省：CLAIM_MATCH_
        # MIN_SCORE 0.86 是照 Atlas 的標度校準的。
        score_by_id = {
            str(row["id"]): 1.0 - (float(row["distance"]) / 2.0) for row in rows
        }

        return await self._fetch_claim_docs(score_by_id)

    async def _fetch_claim_docs(
        self, score_by_id: dict[str, float]
    ) -> list[dict[str, Any]]:
        """依 PG 給的 id 到 Mongo 取判定與原文，組成與 Atlas 版相同的 dict 結構。

        回傳的每個 dict 必須帶齊 `_dedup_by_url`／`_best_verdicted_match`／
        `_resolve` 會讀的欄位，下游才不必知道向量換了家。
        """
        from bson import ObjectId

        object_ids = []
        for raw_id in score_by_id:
            try:
                object_ids.append(ObjectId(raw_id))
            except Exception:
                logger.warning("claim_match_bad_object_id id=%s", raw_id)
        if not object_ids:
            return []

        projection = {
            "_id": 1,
            self.claim_field: 1,
            "verdict": 1,
            "verdict_slug": 1,
            "url": 1,
            "original_title": 1,
            "published_at": 1,
            "source_name": 1,
            self.content_field: 1,
        }
        docs = await (
            self._ensure_collection()
            .find({"_id": {"$in": object_ids}}, projection)
            .to_list(length=None)
        )

        results: list[dict[str, Any]] = []
        for doc in docs:
            score = score_by_id.get(str(doc.get("_id")))
            if score is None:
                continue
            doc = dict(doc)
            doc.pop("_id", None)
            doc["score"] = score
            results.append(doc)

        results.sort(key=lambda d: d["score"], reverse=True)
        return results
