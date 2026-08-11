## 1. 設定與接線

- [x] 1.1 新增 `RAG_WEB_FALLBACK_ENABLED`（default true）於 `app/core/config.py` 與 `.env.example`
- [x] 1.2 `RagAnswerService` 注入可選 `web_search`／`web_fallback_enabled`；不足路徑改呼叫 `WebSearchService.answer`
- [x] 1.3 `dependencies.py`：先建 `WebSearchService`，再注入 `RagAnswerService`；可停止 `configure_web_tool`（或保留但 registry 不用）

## 2. Agent 工具面

- [x] 2.1 `registry.get_all_tools` 移除 `search_public_web`／`include_web_tool`（或永久 False）
- [x] 2.2 更新 `get_rag_answer` docstring（不再指向 web tool）
- [x] 2.3 更新 `test_registry` 與相關斷言

## 3. 測試

- [x] 3.1 `test_answer_service`：空檢索／incorrect／ambiguous exhausted → mock web；flag off → 舊訊息
- [x] 3.2 確認既有 CRAG correct 路徑與 citation 測試仍過

## 4. 收尾

- [x] 4.1 跑相關 unit tests
- [x] 4.2 勾選本 tasks；必要時更新過時 design 註記（本 change 已覆蓋 light-crag「不自動上網」）
