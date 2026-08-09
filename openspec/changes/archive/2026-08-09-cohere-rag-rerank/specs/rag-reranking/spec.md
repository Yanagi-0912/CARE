## ADDED Requirements

### Requirement: 兩階段檢索與 Cohere 精排

系統在執行知識庫 RAG 時 SHALL 先以向量檢索取回最多 `RAG_RETRIEVE_CANDIDATES` 筆候選文件（預設 40），再以 Cohere Rerank API 依使用者問題對候選做精排，並只將精排後前 `RAG_RERANK_TOP_N` 筆（預設 5）作為生成上下文。精排模型預設 SHALL 為 `rerank-v4.0-pro`（可經設定覆寫為例如 `rerank-v4.0-fast`）。

#### Scenario: 成功精排後縮短上下文

- **WHEN** 向量檢索命中多於 `RAG_RERANK_TOP_N` 筆文件，且 Cohere Rerank 成功
- **THEN** 生成 prompt 僅包含精排後前 `RAG_RERANK_TOP_N` 筆文件內容，且順序與精排結果一致

#### Scenario: 候選不足 top_n

- **WHEN** 向量檢索（含 min_score 過濾後）命中筆數少於 `RAG_RERANK_TOP_N`
- **THEN** 系統以實際命中筆數呼叫精排（或等價處理），並將全部可用文件依精排順位放入生成上下文

### Requirement: Cohere 失敗或未設定時降級

當 `COHERE_API_KEY` 未設定，或 Cohere Rerank 呼叫失敗（逾時、HTTP 錯誤、SDK 例外）時，系統 SHALL NOT 中斷 RAG 流程；SHALL 改以向量檢索分數排序後取前 `RAG_RERANK_TOP_N` 筆作為生成上下文，並記錄 warning 等級日誌標示已降級。

#### Scenario: 未設定 API 金鑰

- **WHEN** 環境未提供 `COHERE_API_KEY` 且向量檢索有命中
- **THEN** 系統不呼叫 Cohere，以向量分數取前 `RAG_RERANK_TOP_N` 筆生成回答，並留下可觀測的降級紀錄

#### Scenario: Cohere 呼叫失敗

- **WHEN** Cohere Rerank API 回傳錯誤或逾時
- **THEN** 系統以降級路徑完成該次 RAG，而非向使用者拋出未處理例外

### Requirement: 無候選時不呼叫精排

當向量檢索無任何通過過濾的文件時，系統 SHALL NOT 呼叫 Cohere Rerank，並維持既有無命中回應行為。

#### Scenario: 知識庫無命中

- **WHEN** 向量檢索結果為空
- **THEN** 不發起 Cohere 請求，並回傳知識庫無命中之既有提示
