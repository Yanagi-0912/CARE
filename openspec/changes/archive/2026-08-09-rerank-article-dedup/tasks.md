## 1. 精排後之文章層級去重

- [x] 1.1 `app/services/rag/answer_service.py`：新增模組層級純函式 `dedup_ranked_docs(docs, *, max_per_article)`，依序掃描保持原順序，每篇文章最多保留 `max_per_article` 個 chunk；文章身分重用 `RagAnswerService._source_key`；`max_per_article < 1` 視為 `1`
- [x] 1.2 `RagAnswerService.__init__` 新增參數 `max_chunks_per_article: int = 2`
- [x] 1.3 `RagAnswerService._retrieve_and_rerank` 改為：`reranker.rerank(query, docs, top_n=len(docs))` 取完整排序 → `dedup_ranked_docs(..., max_per_article=self.max_chunks_per_article)` → `[:self.rerank_top_n]`
- [x] 1.4 `app/core/config.py`：新增 `RAG_RERANK_MAX_CHUNKS_PER_ARTICLE`（`int`，預設 `"2"`，放在 `RAG_VECTOR_MIN_SCORE` 之後）
- [x] 1.5 `app/dependencies.py`：組裝 `RagAnswerService` 時傳入 `max_chunks_per_article=settings.RAG_RERANK_MAX_CHUNKS_PER_ARTICLE`
- [x] 1.6 `.env.example`：新增 `RAG_RERANK_MAX_CHUNKS_PER_ARTICLE=2` 與繁中註解
- [x] 1.7 測試（TDD：先寫失敗測試、確認失敗、再實作）：`tests/unit/services/rag/test_answer_service.py`
  - `test_dedup_ranked_docs_caps_two_chunks_per_article_by_default`：7 個 chunk 來自 2 篇文章（A×4、B×3），cap=2 → 結果為 A,A,B,B，順序保持
  - `test_dedup_ranked_docs_cap_one_keeps_only_top_ranked_chunk_per_article`：cap=1 → 每篇只留最高分那個
  - `test_dedup_ranked_docs_identity_without_url_uses_source_and_title`：無 url 文章的身分判定——source+title 相同才合併，不同則不合併
  - `test_dedup_ranked_docs_non_positive_cap_treated_as_one`：`max_per_article` 為 0／負值時視為 1，不拋例外
  - `test_dedup_ranked_docs_empty_input_returns_empty`：空輸入回傳空
  - `test_retrieve_and_rerank_sends_full_ranked_list_to_reranker_and_dedups`：注入 mock reranker，斷言 `rerank` 收到 `top_n=len(docs)`（不再是固定 5），且最終回傳長度 ≤ `rerank_top_n`、無文章超過 cap

## 2. eval 腳本鏡射 production 行為

- [x] 2.1 `scripts/rag_eval.py`：檔頭從 `app.services.rag.answer_service` import `dedup_ranked_docs`
- [x] 2.2 `_maybe_rank`（供 `--rank-mode vector|cohere` 使用）改為：rerank 全排（`top_n=len(docs)`）→ `dedup_ranked_docs` → 截斷至呼叫端傳入的 `top_n`；`rank_mode == "none"` 的原始檢索順序路徑維持不動
- [x] 2.3 `run_compare_rerank`（供 `--compare-rerank` 使用）改為重用 `_maybe_rank`（vector／cohere 兩分支都套用），使 `--compare-rerank` 的輸出真正反映去重後的行為，而不是繞過 `_maybe_rank` 直接呼叫 reranker

## 3. 驗證

- [x] 3.1 `pytest tests/` 全綠（Definition of Done）
- [x] 3.2 `openspec validate rerank-article-dedup --strict` 通過
- [x] 3.3 實跑 `python scripts/rag_eval.py --compare-rerank --top-n 5 --out /tmp/rag-compare-dedup.json`，把去重前後的 RRF／Cohere 三項指標並列記錄進 `design.md`（照實記錄，不修飾）
- [x] 3.4 從輸出 JSON 抽 kb-013 的 cohere 分支 `retrieved_titles`，確認 top-5 的相異文章數變化，記錄進 `design.md`（本 change 的主要驗證標的，不是聚合指標）
