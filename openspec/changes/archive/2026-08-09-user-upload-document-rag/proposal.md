## Why

使用者上傳 PDF 後，系統先前會誤把全文當官方 KB query；修掉 force RAG 後雖可依抽出文字摘要，但長文件無法可靠「針對這份 PDF 問答」。需要把上傳文件切 chunk、embedding、暫存後再檢索，讓後續問題能真正搜這份文件。

## What Changes

- 新增**使用者上傳文件**的 Mongo 向量集合（與官方 KB 分離），文件含 `line_user_id`、`expires_at`，以 Mongo TTL index 自動過期（本階段**不做 Redis**）。
- 媒體抽字成功後（優先 `file`／PDF）：切 chunk → embed → 寫入上述集合。
- 新增工具（或等價服務入口）依目前 `line_user_id` 檢索使用者上傳 chunk 並生成回答。
- Agent prompt：關於上傳文件的問題優先走此路徑；官方 `get_rag_answer` 維持查官方 KB。
- 單元測試覆蓋 ingest／TTL metadata／user-scoped retrieve／工具無 user 時失敗友善。

## Capabilities

### New Capabilities

- `rag-user-docs`：使用者上傳文件的暫存 ingest、TTL、user-scoped 檢索與問答。

### Modified Capabilities

- `agent-architecture`：工具集與媒體／後續問答路由納入上傳文件檢索工具。

## Impact

- **程式**：`app/services/rag/`（新 service／retriever）、`media_handler` 或 message 流程、`tools/`、`dependencies.py`、`config.py`、`prompt.py`、`main.py`（ensure TTL index）
- **資料**：新 Mongo collection + Atlas vector index（部署需建立 index）
- **API／route**：無對外 REST 變更（走既有 LINE webhook）
- **非目標**：Redis session 指標、官方 KB 混寫、圖片 OCR 修復
- **測試計畫**：`pytest` 單元測試（mock Mongo／embeddings）；手動：上傳 PDF → 再問文件內問題
