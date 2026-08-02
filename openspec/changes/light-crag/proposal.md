## Why

CARE 已有 wide retrieve + Cohere rerank + eval，但檢索後仍一律送進生成。當精排後的 chunk 其實答不了問題時，模型可能硬答或回「無法提供」，Agent 也不會自動改走網路搜尋。文章中的 CRAG 要點是：**先評檢索品質，不夠再用改寫／外部知識**；現在有 eval 可量測，適合做輕量版。

## What Changes

- 在 RAG pipeline（rerank 之後、生成之前）新增 **檢索充足性評分（grader）**。
- 評分為「不足／無關」時：SHALL **不生成知識庫答案**，改回傳可觀測的不足訊號（既有無命中／無法回答類訊息或其延伸），讓 Agent 可改呼叫 `search_public_web`。
- 評分為「模糊」時：SHALL 最多做 **一次** query rewrite 後重新 retrieve→rerank→grade；仍不足則同上降級。
- 評分為「充足」時：維持現有 generate + 來源附註行為。
- Grader 失敗（LLM／逾時）時：SHALL 降級為現況（直接生成），並記 warning，避免整條 RAG 掛掉。
- 新增單元測試；以既有 `rag_eval`／golden 可選驗證（不強制 CI 閘門）。
- **非 BREAKING**：對外 LINE／tool 名稱不變；僅改變「檢索不足時」較常出現無資料訊號而非胡謅。

## Capabilities

### New Capabilities

- `rag-crag`: 定義檢索後充足性分級、一次改寫重試、不足時不生成與失敗降級。

### Modified Capabilities

- `rag-responses`: 補充「檢索內容經評分不足時」的回答契約（對齊無命中／無法回答行為）。

## Impact

- 程式：`app/services/rag/`（新增 grader／改寫模組）、`answer_service.py`、`dependencies.py`；可能微調 `app/tools/rag_tools.py` 說明字串
- 設定：可選 env（例如啟用開關、改寫次數上限已定為 1）
- API／route：無新 HTTP endpoint；影響經由既有 RAG tool
- 依賴：沿用 Gemini structured output（boolean 或小型 enum），不新增供應商
- 測試：`tests/unit/services/rag/`；本機可用 golden 抽樣對照
