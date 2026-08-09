## Why

CARE 的 RAG 目前是 MongoDB Atlas 向量檢索後直接取 top-k（`k=10` + `min_score`）送進生成，沒有第二階段精排。這容易出現「對的 chunk 排在第 11 名被丟掉」或「進 prompt 的 10 筆品質不均」的失敗。真實使用者已在 LINE 使用知識庫問答，應優先加上投資報酬率最高的 cross-encoder reranking；供應商採用 **Cohere Rerank**。

## What Changes

- 向量檢索改為「先撈廣」：提高 candidate limit（預設約 40–50），再經 Cohere Rerank 精排後只保留 top-n（預設 5）給生成 prompt。
- 新增 Cohere Rerank 客戶端與設定（`COHERE_API_KEY`、model 預設 `rerank-v4.0-pro`、candidate/top-n 參數）。
- 「參考資料來源」仍最多 3 筆，但排序改以 **rerank 後分數／順位** 為準。
- 缺少 API key 或 Cohere 暫時失敗時：SHALL 降級為僅向量分數排序（不中斷回答），並記錄 warning log。
- 新增單元測試涵蓋：成功精排、API 失敗降級、無命中行為不變。
- **非 BREAKING**：對外 LINE／tool 介面不變；僅改善檢索品質與延遲略增（約 +50–200ms）。

## Capabilities

### New Capabilities

- `rag-reranking`：定義 RAG 兩階段檢索（wide retrieve → Cohere rerank → top-n context）與失敗降級行為。

### Modified Capabilities

- `rag-responses`：調整「檢索上下文與參考來源上限」——進 prompt 的文件改為 rerank 後 top-n（不再固定直接用向量 top-10）；來源清單仍最多 3 筆且依精排順位。

## Impact

- **程式**：`app/services/rag/retriever.py`、`answer_service.py`、新增 `app/services/rag/cohere_reranker.py`（或同等模組）、`app/dependencies.py`、`app/core/config.py`、`.env.example`
- **相依**：新增 Cohere Python SDK 或 HTTP client（`requirements.txt`）
- **部署／祕密**：需在本機 `.env`、CI secrets、以及 `CARE-infra` 的 `care-backend-secret` 注入 `COHERE_API_KEY`
- **API／route**：無新 HTTP route；僅影響 `get_rag_answer` tool 內部品質與延遲
- **測試**：`tests/unit/services/rag/` 新增／更新；`./init.sh`（或 pytest）全綠才算完成
