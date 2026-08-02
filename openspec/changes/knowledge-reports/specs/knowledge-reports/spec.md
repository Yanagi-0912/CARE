## ADDED Requirements

### Requirement: 建立與查詢知識回報

系統 SHALL 將知識回報持久化於 MongoDB，並允許已登入使用者建立回報與查詢自己的回報列表。每筆回報 SHALL 含唯一 `report_id`、`line_user_id`、`status`（pending／reviewing／resolved／rejected）、`reason`、`question`，以及可選的補充說明與來源 URL 列表。

#### Scenario: 使用者建立回報

- **WHEN** 已驗證使用者提交 question 與合法 reason
- **THEN** 系統建立 status=pending 的回報並回傳 report_id

#### Scenario: 使用者列出自己的回報

- **WHEN** 已驗證使用者請求列表
- **THEN** 僅回傳該使用者的回報，依建立時間新到舊

### Requirement: 核准後自動 ingest

營運端核准回報時，系統 SHALL 對選定的白名單 URL 呼叫既有 `IngestService.ingest_url`。僅當全部選定 URL 入庫成功時，SHALL 將 status 設為 resolved；任一失敗 SHALL NOT 標記 resolved，並記錄 ingest 錯誤資訊。非白名單 URL SHALL 拒絕核准。

#### Scenario: 核准成功入庫

- **WHEN** 營運以有效 admin key 核准並提供允許網域 URL，且 ingest 全部成功
- **THEN** 回報 status 為 resolved，且向量庫含該 URL 的 chunk

#### Scenario: ingest 失敗不假 resolved

- **WHEN** 核准過程中任一 URL ingest 失敗
- **THEN** status 不為 resolved，並可查得錯誤訊息

#### Scenario: 拒絕回報

- **WHEN** 營運拒絕回報
- **THEN** status 為 rejected，不執行 ingest

### Requirement: Agent 可提交回報

系統 SHALL 提供 Agent tool `submit_knowledge_report`，在已知 line_user_id 的對話脈絡下建立 pending 回報。

#### Scenario: Tool 建立 pending

- **WHEN** Agent 呼叫 submit_knowledge_report 且脈絡有 line_user_id
- **THEN** 建立 pending 回報並回傳 report_id 摘要
