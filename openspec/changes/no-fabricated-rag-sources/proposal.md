## Why

Prod 已應證：`get_rag_answer` 回傳 `has_sources=False`（工具未附真實來源），但最終 LINE 回覆仍出現多筆「參考資料來源」網址且點擊無效——為 Agent 亂編。

## What Changes

- System prompt：僅當工具輸出含真實來源區塊時才保留；否則嚴禁自造來源標題／網址／編號清單。
- Agent 後置處理：若本輪 `get_rag_answer` 工具內容**沒有**來源標題，則自最終回覆移除任何來源標題及其後清單（防止模型亂編）。
- 有真實來源時行為不變（缺則後補、有則保留）。
- 單元測試覆蓋「無來源時剝離亂編」「有來源時保留／後補」。

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `line-reply-rules`：參考來源僅能來自工具真實輸出，不得捏造。
- `agent-architecture`：後置處理須剝離無依據的來源區塊。

## Impact

- **程式**：`prompt.py`、`agent.py`、相關測試
- **行為**：無 url 的 KB 命中時，使用者不再看到假連結；正文衛教仍可回
- **非範圍**：修復 Mongo 缺 url 資料本身（另案 ingest／資料治理）
