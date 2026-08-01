## 1. 設定與相依

- [x] 1.1 於 `requirements.txt` 新增 Cohere SDK（或決定薄 HTTP client 並記錄於 design 實作註記）
- [x] 1.2 於 `app/core/config.py` 與 `.env.example` 新增 `COHERE_API_KEY`、`COHERE_RERANK_MODEL`（default `rerank-v4.0-pro`）、`RAG_RETRIEVE_CANDIDATES`（default 40）、`RAG_RERANK_TOP_N`（default 5）、`COHERE_RERANK_TIMEOUT_SECONDS`（default 5）

## 2. Reranker 與檢索參數

- [x] 2.1 新增 `app/services/rag/cohere_reranker.py`：定義可注入介面（例如 `rerank(query, docs, top_n) -> list[Document]`），實作 Cohere 呼叫、寫入 `rerank_score`／`rerank_rank` metadata
- [x] 2.2 調整 `MongoAtlasVectorRetriever` 預設／注入的 `k` 為 `RAG_RETRIEVE_CANDIDATES`（維持 `min_score` 過濾與 `numCandidates = k * 30`）
- [x] 2.3 更新 `RagAnswerService`：retrieve → rerank（或降級取 top_n）→ generate；來源清單依精排後順位取最多 3 筆
- [x] 2.4 於 `app/dependencies.py` 組裝 reranker 並注入 `RagAnswerService`；無 API key 時注入明確降級實作（不發網）

## 3. 測試（依賴注入，禁止 monkey patch 改全域）

- [x] 3.1 新增 `tests/unit/services/rag/test_cohere_reranker.py`：成功重排、top_n 截斷、空輸入不呼叫 client（client 以建構參數注入 mock）
- [x] 3.2 新增／更新 `tests/unit/services/rag/test_answer_service.py`：精排成功只把 top_n 進 prompt；Cohere 失敗／無 key 時降級；無命中不呼叫 reranker
- [x] 3.3 更新既有 retriever／registry 相關測試中對 `k`／top 文件數的假設（若有）

## 4. 部署與驗證

- [x] 4.1 更新 `CARE-infra`（或部署文件）使 `care-backend-secret` 可注入 `COHERE_API_KEY`
- [x] 4.2 執行等價 pytest（`tests/unit` 303 passed）確認全綠；commit 待使用者指示後再做
