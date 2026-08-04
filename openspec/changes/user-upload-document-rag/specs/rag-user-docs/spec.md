## ADDED Requirements

### Requirement: 上傳文件暫存 ingest

系統 SHALL 在使用者經 LINE 上傳之檔案（file／PDF）成功抽出文字後，將全文切成 chunk、產生 embedding，並寫入與官方知識庫分離的 Mongo 集合。每筆 chunk SHALL 含 `line_user_id`、`document_id`、`expires_at`（以及文字／向量欄位）。Ingest 失敗時 SHALL NOT 阻斷既有媒體回覆流程。

#### Scenario: PDF 抽字成功後寫入 user docs

- **WHEN** 使用者上傳 PDF 且抽字成功並取得 `line_user_id`
- **THEN** 系統寫入一筆以上 user-doc chunks，且每筆含相同 `document_id` 與未來時間的 `expires_at`

#### Scenario: Ingest 失敗不阻斷回覆

- **WHEN** embedding 或 Mongo 寫入失敗
- **THEN** 系統記錄錯誤，使用者仍可依抽出文字獲得一般媒體回覆

### Requirement: Mongo TTL 自動過期

系統 SHALL 對 user-docs 集合的 `expires_at` 欄位建立 TTL index（`expireAfterSeconds: 0`），使過期 chunk 由 Mongo 自動刪除。預設保留期限 SHALL 可設定，預設為 1 天。

#### Scenario: 啟動時確保 TTL index

- **WHEN** 應用啟動且 user-docs 功能已設定 collection
- **THEN** 系統確保 `expires_at` TTL index 存在

### Requirement: User-scoped 文件問答

系統 SHALL 提供工具（或等價服務）依目前請求的 `line_user_id` 僅檢索該使用者未過期的上傳 chunk，並依檢索段落生成回答。SHALL NOT 將上傳文件寫入或檢索官方知識庫集合作為此工具的唯一來源。

#### Scenario: 依上傳文件回答

- **WHEN** 代理呼叫上傳文件問答工具，且該使用者有未過期 chunk 與相關 query
- **THEN** 系統回傳依該使用者上傳內容生成的回答

#### Scenario: 無使用者身分

- **WHEN** 工具被呼叫但請求沒有 `line_user_id`
- **THEN** 回傳友善錯誤說明，不拋未處理例外

#### Scenario: 無上傳或已過期

- **WHEN** 該使用者沒有未過期 chunk
- **THEN** 回傳友善提示（例如尚未有可查閱的上傳文件）
