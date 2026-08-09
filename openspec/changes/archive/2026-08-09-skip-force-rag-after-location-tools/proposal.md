## Why

Prod 已應證：使用者說「我要看醫院」時，第一輪正確呼叫 `request_location_quick_reply`，第二輪卻因 `force_rag=True` 再強制 `get_rag_answer`，浪費延遲並產生無關 RAG／來源。

## What Changes

- `force_rag` 注入條件新增：若本輪對話已執行過位置／院所工具（`request_location_quick_reply`、`find_nearby_hospitals`、`lookup_medical_facility`），SHALL NOT 再強制 RAG。
- 單元測試覆蓋「已請分享位置後不 force」。
- 健康題、尚未跑位置工具時的 force RAG 行為維持不變。

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `agent-architecture`：force RAG 排除已走位置／院所工具路徑。

## Impact

- **程式**：`nodes.py`、`test_force_rag.py`
- **行為**：找醫院／請位置後不再誤觸 RAG
