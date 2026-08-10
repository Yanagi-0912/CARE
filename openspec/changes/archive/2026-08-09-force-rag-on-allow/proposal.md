## Why

Guardrail 已將健康相關訊息設為 `allow_rag=True` 並綁定 `get_rag_answer`，但 LLM 仍常直接回答（例如「我有六隻腳趾頭」），違反「健康／識詐必查庫」契約。僅靠 prompt 無法保證，需要在 `agent` 節點做硬保險。

## What Changes

- 在 `agent_node`：當 `allow_rag=True`、工具集含 `get_rag_answer`、本輪尚無任何 tool call、且本輪對話尚未執行過 `get_rag_answer` 時，強制注入一次 `get_rag_answer` tool call（query＝最新使用者訊息）。
- 記錄 log（例如 `force_rag=True`）便於觀測。
- 單元測試覆蓋強制／不強制條件。
- 更新 `agent-architecture` 規格要求上述行為。

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `agent-architecture`：新增「allow_rag 時未呼叫工具則強制 RAG」要求。

## Impact

- **程式**：`app/services/agent/utils/nodes.py`；測試 `tests/unit/services/agent/`
- **API／route**：無
- **行為**：健康相關問題較不會跳過查庫；寒暄（`allow_rag=False`）不受影響
- **測試計畫**：`pytest tests/unit/services/agent/ -q`
