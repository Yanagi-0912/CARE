## 1. Config / Mongo user-docs

- [x] 1.1 新增 `MONGODB_USER_DOCS_COLLECTION`、`MONGODB_USER_DOCS_VECTOR_INDEX`、`USER_DOCS_TTL_SECONDS`（預設 86400）至 config／`.env.example`
- [x] 1.2 啟動時 `ensure_user_docs_indexes`（TTL on `expires_at`）；單元測試 mock

## 2. Ingest service

- [x] 2.1 實作 `UserDocumentIngestService.ingest_text(line_user_id, text, *, source_name, media_type)`：chunk → embed → insert（含 `document_id`／`expires_at`）
- [x] 2.2 單元測試：chunk 寫入欄位、空文字 no-op、失敗 raise／由呼叫端吞

## 3. Media hook

- [x] 3.1 `LineMediaHandler`（或等價）在 file／PDF 抽字成功後呼叫 ingest；失敗只 log
- [x] 3.2 單元測試：成功呼叫 ingest；ingest 例外仍回傳媒體文字

## 4. Retrieve + answer tool

- [x] 4.1 User-docs retriever（filter `line_user_id` + vector search）與 `UserDocumentAnswerService`
- [x] 4.2 工具 `answer_from_uploaded_document` + DI／registry；無 user／無文件友善回覆
- [x] 4.3 單元測試：有 chunks 生成答案路徑（mock LLM／retriever）；無 user／無 docs

## 5. Agent prompt

- [x] 5.1 更新 `prompt.py`：上傳文件問題優先新工具；官方衛教仍 `get_rag_answer`
- [x] 5.2 相關 prompt／registry 測試更新

## 6. Verify

- [x] 6.1 `pytest` 相關單元全綠；勾選 tasks
- Final review fixes：force 上傳追問走 `answer_from_uploaded_document`（非 `get_rag_answer`）；`test_user_document_retriever` 驗證 filter／text field
