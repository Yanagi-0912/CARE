## Why

精排（Cohere Rerank）目前對 wide retrieve 撈回的 40 個 chunk **獨立打分**——它看的是「這個 chunk 跟 query 有多相關」，不知道也不在乎某個 chunk 跟另一個 chunk 是不是同一篇文章切出來的。當同一篇文章被切成的多個 chunk 剛好都相關時，它們會一起擠進 top-5，把其他文章的席位排擠掉。

真實案例（kb-013「如何預防中風？」，Cohere 精排 top-5）：5 個席位只來自 **2 篇文章**（「3大關鍵行動」佔 2 席、「中風8大危險因子」佔 3 席）。後果是進生成 prompt 的 5 個 chunk 只涵蓋 2 個來源——答案能引用的角度變少，`_append_sources` 最多列 3 筆來源的設計也因此形同虛設（5 個 chunk 只換得 2 個可列的來源）。

**本改動的動機是答案品質（context 來源多樣性），不是 eval 指標**。題庫在 2026-08-09 補上多正解標籤後，Cohere 精排已經在 eval 上勝出（`hit_rate@5 = 0.864` vs RRF `0.818`，見 `openspec/changes/rag-eval-metrics`），文章層級去重對這幾個聚合指標的影響預期很小、方向也不確定——golden set 的判準是「答對了沒」，不是「答案來源夠不夠多元」，去重不會讓沒中的題目變中。這裡要解的是一個 eval 分數量不到、但實測攤開 `retrieved_titles` 就能直接看到的問題。

## What Changes

- 新增模組層級純函式 `dedup_ranked_docs(docs, *, max_per_article)`（`app/services/rag/answer_service.py`）：輸入精排後的**完整排序**（分數高在前），依序掃描，每篇文章最多保留 `max_per_article` 個 chunk，其餘依原順序捨棄——不重新排序，只做過濾
- 文章身分判定**重用**既有的 `RagAnswerService._source_key`（有 url 用 url，無 url 用 source_name+original_title），不重新發明身分邏輯，確保與 `_append_sources` 判斷「同一來源」的邏輯一致
- `RagAnswerService._retrieve_and_rerank` 改為：`reranker.rerank(query, docs, top_n=len(docs))` 取得完整排序 → `dedup_ranked_docs` 依文章去重 → 取前 `rerank_top_n` 筆進 prompt。**呼叫 reranker 時 `top_n` 從固定的 5 改成 `len(docs)`**——去重必須看過完整排序才能判斷「這篇文章有沒有更高分的 chunk 沒被算進去」，只看被截斷後的 top-5 就已經來不及去重
- 新增環境變數 `RAG_RERANK_MAX_CHUNKS_PER_ARTICLE`（預設 `2`），每篇文章保留幾個 chunk 可調
- 成本：Cohere Rerank 的計價單位是 **search unit**（1 次 query + 最多 100 份文件視為 1 個 search unit），與 `top_n` 參數無關；把送進 API 的 `top_n` 從 5 改成 40（wide retrieve candidates 數量）**不增加費用**，詳見 `design.md`
- `scripts/rag_eval.py` 的 `_maybe_rank`（供 `--rank-mode vector|cohere` 使用）與 `run_compare_rerank`（供 `--compare-rerank` 使用）同步鏡射上述 production 行為；`--rank-mode none` 的裸檢索觀測路徑不變
- **非 BREAKING**：對外 `get_rag_answer` tool 介面不變，不新增 HTTP route；只改變送進生成 prompt 的 5 個 chunk 分佈在幾篇文章上

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `rag-responses`：新增「精排後之文章層級去重」為新 requirement（`## ADDED Requirements`）。不修改既有的「檢索上下文與參考來源上限」requirement 內容——該 requirement 目前有未 archive 的 `rag-eval-metrics` change 持有 `MODIFIED` delta，本 change 疊加新的 requirement 而非修改同一段文字，避免兩個未 archive 的 change 在 archive 時對同一段落衝突

## Impact

- **程式**：`app/services/rag/answer_service.py`（新增 `dedup_ranked_docs`、`RagAnswerService.__init__` 新參數 `max_chunks_per_article`、`_retrieve_and_rerank` 改用完整排序）、`app/core/config.py`（新增 `RAG_RERANK_MAX_CHUNKS_PER_ARTICLE`）、`app/dependencies.py`（注入新設定）、`scripts/rag_eval.py`（`_maybe_rank`、`run_compare_rerank` 鏡射 production 行為）
- **設定**：`.env.example` 新增 `RAG_RERANK_MAX_CHUNKS_PER_ARTICLE=2`
- **API／route**：無新 HTTP route；`get_rag_answer` tool 對外介面與回覆格式不變，僅改變進 prompt 的 5 個 chunk 的文章分佈
- **測試**：`tests/unit/services/rag/test_answer_service.py` 新增 `dedup_ranked_docs` 純函式測試與 `_retrieve_and_rerank` 整合測試；`pytest tests/` 全綠才算完成
- **相依**：無新增套件
