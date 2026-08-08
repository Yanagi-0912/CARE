## 1. 移除向量檢索硬門檻

- [ ] 1.1 `app/services/rag/retriever.py`：`DEFAULT_MIN_SCORE` 由 `0.5` 改為 `0.0`
- [ ] 1.2 `app/core/config.py`：新增 `RAG_VECTOR_MIN_SCORE`（`float`，預設 `0.0`）
- [ ] 1.3 `app/dependencies.py`：組裝 retriever 時改由 `settings.RAG_VECTOR_MIN_SCORE` 注入 `min_score`（不再吃 `DEFAULT_MIN_SCORE` 常數的隱性預設）
- [ ] 1.4 `.env.example`：新增 `RAG_VECTOR_MIN_SCORE=0.0` 並附註說明（見 `design.md` D1）
- [ ] 1.5 測試：`tests/unit/services/rag/test_retriever.py`

## 2. reranker 輸入補回標題

- [ ] 2.1 `app/services/rag/cohere_reranker.py`：新增模組層級函式 `rerank_document_text(doc: Document) -> str`，組成 `主題：{original_title}\n內容：{chunk}`；`doc.metadata` 無 `original_title`（或為空）時退回純 `page_content`
- [ ] 2.2 `CohereReranker.rerank` 改用 `rerank_document_text` 組出送給 Cohere API 的 `documents` 清單；回傳的 `Document.page_content` 維持原始 chunk 內容不變（見 `design.md` D2）
- [ ] 2.3 測試：`tests/unit/services/rag/test_cohere_reranker.py`

## 3. 清除 Firecrawl 導覽列噪音

- [ ] 3.1 新增 `scripts/purge_navigation_chunks.py`：以明列的 URL 清單為條件，刪除 CARE `IngestService`（Firecrawl）產生的 266 筆導覽列噪音 chunk
- [ ] 3.2 預設 dry-run（只列出將刪除的筆數與範例內容，不寫入資料庫），需明確帶 `--apply` 旗標才實際執行刪除
- [ ] 3.3 測試：`tests/unit/scripts/test_purge_navigation_chunks.py`

## 4. 修正 BM25 索引範本欄位名

- [ ] 4.1 `resources/atlas_text_search_index.json`：`mappings.fields` 的欄位鍵名 `text` 改為 `chunk_content`（線上實際索引使用的欄位名），並同步修正檔案內的 `_comment` 說明文字

## 5. Definition of Done

- [ ] 5.1 `./init.sh` 全綠（所有 pytest 通過）且有清楚的 git commit
