## 1. 強制 RAG 行為

- [x] 1.1 新增單元測試（TDD）：`allow_rag=True` + 模型無 tool_calls → 回傳訊息含 `get_rag_answer` tool call；query 為使用者原文；已有 ToolMessage(name=get_rag_answer) 時不強制；`allow_rag=False` 不強制。測試放在 `tests/unit/services/agent/`（建議 `test_force_rag.py` 或擴充既有 agent 測試），禁止 monkey patch 全域，以 DI mock LLM
- [x] 1.2 實作 `app/services/agent/utils/nodes.py` 的強制注入與 log（`force_rag=True`）
- [x] 1.3 跑 `pytest tests/unit/services/agent/ -q` 全綠

## 2. 收尾

- [x] 2.1 勾選本 tasks；相關變更 git commit（繁中訊息）
- [x] 2.2 歸檔前覆核：spec delta 的 SHALL 原本是無條件的，但實作有 10 個條件、
  其中 6 個是例外。4 個已由本 capability 其他要求涵蓋（已走位置／院所工具、
  找院所意圖、媒體抽出全文、官網意圖），另外 2 個（指名院所查詢、上傳文件問答）
  沒有任何地方記錄，已補進本 delta。`tests/unit/services/agent/test_force_rag.py` 通過
