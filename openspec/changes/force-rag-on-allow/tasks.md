## 1. 強制 RAG 行為

- [x] 1.1 新增單元測試（TDD）：`allow_rag=True` + 模型無 tool_calls → 回傳訊息含 `get_rag_answer` tool call；query 為使用者原文；已有 ToolMessage(name=get_rag_answer) 時不強制；`allow_rag=False` 不強制。測試放在 `tests/unit/services/agent/`（建議 `test_force_rag.py` 或擴充既有 agent 測試），禁止 monkey patch 全域，以 DI mock LLM
- [x] 1.2 實作 `app/services/agent/utils/nodes.py` 的強制注入與 log（`force_rag=True`）
- [x] 1.3 跑 `pytest tests/unit/services/agent/ -q` 全綠

## 2. 收尾

- [ ] 2.1 勾選本 tasks；相關變更 git commit（繁中訊息）
