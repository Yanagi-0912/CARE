## Why

`rag-eval-metrics` change 建立的可信量測基準顯示：`hit_rate@5 = 0.412`、`mean_mrr = 0.198`、`mean_ndcg@5 = 0.253`。hit_rate 遠高於 nDCG@5，代表相關文件多半撈得到，只是排不上前面——這是排序問題，不是召回問題。兩個實測根因：

1. 向量檢索的 `min_score=0.5` 硬門檻在候選進 reranker 前就先過濾，與「wide retrieve → rerank」的架構意圖相反（第一階段應衝 recall，過濾與排序是精排的職責）。而且 hybrid 路徑經 RRF 融合後 `metadata["score"]` 已被覆寫為融合分數，對它套用針對 cosine 相似度設計的 0.5 絕對門檻在語意上是錯的。
2. 上游 ETL（`Capoo0618/CARE-data` 的 `main_pipeline.py`）以 `f"主題：{title}\n內容：{chunk}"` 產生 embedding，但寫入 Mongo 的 `chunk_content` 不含標題，導致 Cohere reranker 收到的是缺語境的斷句碎片，與向量空間所見文本不一致。實測 `--compare-rerank` 顯示 cohere 精排目前反而劣於純向量排序，與此假說一致。

## What Changes

- `DEFAULT_MIN_SCORE` 由 `0.5` 改為 `0.0`，並新增環境變數 `RAG_VECTOR_MIN_SCORE`（預設 `0.0`）保留該設定項，需要時可調回非零門檻
- reranker 送出的 document 文本改為 `主題：{original_title}\n內容：{chunk}`，格式對齊上游 embedding；無標題時退回純內容；精排回傳的 `page_content` 維持原始 chunk 不變
- 新增 `scripts/purge_navigation_chunks.py`：以明列 URL 為條件，清除 CARE `IngestService`（Firecrawl）產生的 266 筆導覽列噪音 chunk；預設 dry-run，需 `--apply` 才實際刪除
- 修正 `resources/atlas_text_search_index.json` 的欄位名 `text` → `chunk_content`（線上實際索引欄位是 `chunk_content`，範本寫錯會讓照做的人建出失效的 BM25 索引）
- **非 BREAKING**：對外 `get_rag_answer` tool 介面不變，僅改變檢索候選過濾行為與精排輸入文本；不新增 HTTP route

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `rag-responses`：向量檢索候選過濾行為改變——移除固定相似度門檻，過濾與排序職責移交精排階段；送入精排的文件文本格式對齊 embedding。

## Impact

- **程式**：`app/services/rag/retriever.py`（`DEFAULT_MIN_SCORE`）、`app/core/config.py`（新增 `RAG_VECTOR_MIN_SCORE`）、`app/dependencies.py`（注入新設定）、`app/services/rag/cohere_reranker.py`（新增 `rerank_document_text`）、`resources/atlas_text_search_index.json`、`scripts/` 新增 `purge_navigation_chunks.py`
- **設定**：`.env.example` 新增 `RAG_VECTOR_MIN_SCORE=0.0`
- **API／route**：無新 HTTP route；僅影響 `get_rag_answer` tool 內部檢索候選數量與精排輸入品質，對外回覆格式不變
- **測試**：`tests/unit/services/rag/test_retriever.py`、`tests/unit/services/rag/test_cohere_reranker.py`、`tests/unit/scripts/test_purge_navigation_chunks.py` 新增／更新；`./init.sh`（或 pytest）全綠才算完成
- **相依**：無新增套件
