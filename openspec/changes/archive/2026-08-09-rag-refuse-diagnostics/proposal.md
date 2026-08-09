## Why

出現 `rag_fail code=MODEL_REFUSE` 時，現有 log 只記代碼，看不到生成原文與命中的拒絕 marker，無法應證是「模型真拒答」還是「marker 誤殺」（例如回答含「無法」）。

## What Changes

- 當 KB 或 Web 生成後因 `_is_cannot_answer` 進入 `MODEL_REFUSE` 時，SHALL 記錄：
  - `matched_marker`（第一個命中的 marker；空字串視為 empty）
  - `answer_preview`（生成原文截斷預覽）
- 抽出可測的「找 matched marker」輔助函式（KB／Web 共用或對稱）。
- **非範圍**：不改 refuse 行為、不改 marker 清單、不自動 web fallback。

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `rag-responses`：MODEL_REFUSE 路徑須可診斷（log matched_marker + answer_preview）。

## Impact

- **程式**：`answer_service.py`、`web_search_service.py`（及可選小模組）
- **API**：無
- **測試**：單元測試驗證 refuse 時會以正確 kwargs 呼叫 logger
- **觀測**：下一則 prod refuse 即可從 log 應證原因
