## 1. Guardrail 分類範圍

- [x] 1.1 新增／更新單元測試：`tests/unit/services/guardrail/`（或既有對應路徑）驗證 `_CLASSIFICATION_PROMPT`（或等價公開常數）含醫療詐騙／假藥等語意；使用 DI 注入 mock 分類器，禁止 monkey patch
- [x] 1.2 更新 `app/services/guardrail/service.py` 的分類提示，使醫療識詐相關訊息會走 RAG；跑相關 pytest 全綠

## 2. Agent Prompt 與工具說明

- [x] 2.1 新增／更新單元測試：`tests/unit/services/agent/`（或對應路徑）断言 `SYSTEM_PROMPT` 含：醫療識詐角色、健康／識詐必須先 `get_rag_answer`、非執法／勸阻匯款／165 等關鍵約束
- [x] 2.2 更新 `app/services/agent/prompt.py` 的 `SYSTEM_PROMPT`（保留既有院所／位置／RAG 前綴／來源規則）
- [x] 2.3 更新 `app/tools/rag_tools.py` 的 `get_rag_answer` docstring，涵蓋醫療詐騙／假藥／可疑醫療訊息；補單元測試断言 docstring；跑相關 pytest

## 3. 知識種子清單

- [x] 3.1 新增 `resources/medical_anti_fraud_seed_urls.txt`（3–8 筆 `*.gov.tw` 官方 URL，`#` 可註解）；可選在檔首註明以 `scripts/ingest_url.py` ingest
- [x] 3.2 新增輕量測試或靜態檢查：種子檔存在、非註解列皆為 http(s) 且通過 `is_allowed_url`（DI／直接呼叫純函式即可）

## 4. 驗證與收尾

- [x] 4.1 執行 `./init.sh`（或專案慣例之全量 pytest）確認全綠
- [x] 4.2 將本 change 相關程式與 openspec／resources 變更做成清楚的 git commit（繁中或專案慣例訊息）
