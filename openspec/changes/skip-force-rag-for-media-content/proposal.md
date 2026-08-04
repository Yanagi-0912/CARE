## Why

上傳 PDF／圖片抽字後，系統把整份媒體全文當 `get_rag_answer` 的 query 強制查官方 KB。使用者意圖是「幫我看這份文件」，不是「用全文搜衛教庫」；KB 查不到後模型又依對話上下文硬摘要，出現「找不到相關資訊」卻又能說明文件內容的矛盾回覆。

## What Changes

- 偵測 `LineMediaHandler` 媒體抽出前綴時：**禁止** force RAG（勿把全文當 KB query）。
- 同條件下維持既有「不 force 附近院所」行為（已有 `_is_media_extracted_content`）。
- System prompt 補充：媒體抽出內容應依文件摘要／回答，勿誤稱媒體類型、勿當成 KB 查詢失敗來道歉。
- 單元測試：飲食指南 PDF 媒體全文 → 不 force RAG、不 force location；一般衛教文字仍 force RAG。

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `agent-architecture`：force RAG 排除媒體抽出全文；媒體輸入改由模型依抽出內容回答。

## Impact

- **程式**：`nodes.py`、`prompt.py`、`test_force_rag.py`（必要時 `test_prompt.py`）
- **API／route**：無
- **行為**：上傳文件不再先走無效 KB 查詢
- **測試計畫**：`pytest tests/unit/services/agent/test_force_rag.py`（及 prompt 相關單元測試）全綠
