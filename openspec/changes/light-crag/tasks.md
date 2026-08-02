## 1. 設定與介面

- [x] 1.1 於 `app/core/config.py` 與 `.env.example` 新增 `RAG_CRAG_ENABLED`（default `true`）
- [x] 1.2 新增 `app/services/rag/retrieval_grader.py`：`Grade` 三態（`correct`／`ambiguous`／`incorrect`）、`RetrievalGrader` protocol、可注入的 `GeminiRetrievalGrader`（structured output；文件摘要截斷）
- [x] 1.3 新增 `app/services/rag/query_rewriter.py`：`QueryRewriter` protocol + Gemini 實作（最多產出一個改寫 query）

## 2. 接線 RagAnswerService

- [x] 2.1 更新 `RagAnswerService`：rerank 後呼叫 grader；`incorrect`→無資料訊息；`ambiguous`→一次 rewrite 再跑 retrieve→rerank→grade；`correct`→既有 generate
- [x] 2.2 Grader／rewriter 例外時降級為直接 generate，並打 warning log
- [x] 2.3 於 `app/dependencies.py` 依 `RAG_CRAG_ENABLED` 組裝注入（關閉時不跑 CRAG）
- [x] 2.4（可選）微調 `app/tools/rag_tools.py` 的 tool docstring，提示無資料時可改試 `search_public_web`

## 3. 測試（DI，禁止 monkey patch 改全域）

- [x] 3.1 新增 `tests/unit/services/rag/test_retrieval_grader.py`：三態解析／注入 fake client
- [x] 3.2 更新／新增 `tests/unit/services/rag/test_answer_service.py`：correct 生成、incorrect 不生成、ambiguous 改寫成功／失敗、grader 例外降級（注入 FakeGrader／FakeRewriter／FakeRetriever）
- [x] 3.3 執行 `./init.sh` 或等價 `pytest tests/unit` 全綠

## 4. 驗證與收尾

- [x] 4.1 本機抽樣跑數題（含預期不足題）確認 log 有 `crag_grade` 且行為符合 spec
- [ ] 4.2 以清楚訊息 commit（使用者指示後再 push）
