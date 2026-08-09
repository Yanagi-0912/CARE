## Why

前一修只擋「已跑過位置工具後」的 force RAG。Prod 新 log（rid=d0d12b45）顯示「我要看醫院」在**第一輪**就被 `force_rag=True` 注入 `get_rag_answer`（模型未呼叫位置工具），修補未覆蓋此路徑。

## What Changes

- 偵測「找院所／看醫生／要去醫院」類意圖時：**禁止** force RAG。
- 同條件且模型未產生 tool_calls 時：改強制注入 `request_location_quick_reply`（讓使用者仍能分享位置）。
- 含「在哪／地址」等查特定院所線索時，不強制位置（留給 `lookup_medical_facility`）。
- 健康衛教題（無院所意圖）的 force RAG 維持不變。
- 單元測試覆蓋「我要看醫院」→ force location、不 force RAG。

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `agent-architecture`：force RAG 排除院所搜尋意圖；該意圖可 force 位置 quick reply。

## Impact

- **程式**：`nodes.py`、`test_force_rag.py`
- **行為**：找醫院第一輪不再誤入 RAG
