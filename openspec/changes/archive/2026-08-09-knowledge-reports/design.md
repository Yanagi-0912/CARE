## Context

`IngestService` 已可白名單 URL 入庫。LIFF KnowledgeReports 為靜態 sample。無 admin auth 先例 → env `KNOWLEDGE_REPORTS_ADMIN_API_KEY` + header `X-Admin-Key`。

## Goals / Non-Goals

**Goals:** 建立／列表／審核；approve → `IngestService`；LIFF 真資料。  
**Non-Goals:** PDF、完整 Admin UI、推播通知。

## Decisions

1. **Mongo** `knowledge_reports`；欄位含 `report_id`、`line_user_id`、`status`、`reason`、`question`、`user_note`、`user_source_urls`、`resolution`、`reviewer_note`、`ingest_job`、timestamps。
2. **Status** `pending` | `reviewing` | `resolved` | `rejected`。建立＝`pending`。
3. **Approve**  
   - 設 `reviewing` → 對 `selected_urls` 逐個 `ingest_url`  
   - 全 `ok` → `resolved`；否則維持 `reviewing` 並寫 `ingest_job.error`  
   - URL 須白名單，否則 400
4. **Auth**  
   - User：`Depends(get_current_user)`  
   - Admin：`X-Admin-Key`；未設定 key → 503
5. **Agent tool**  
   - `submit_knowledge_report`；`line_user_id` 以 `contextvars` 在 message_handler `invoke` 前後 set／reset（與 medical tools 同 DI 風格）
6. **LIFF**  
   - `GET /api/knowledge-reports`；對應既有狀態文案；提交可第二版（第一版仍 CTA 回 LINE，tool 建立）

## Risks

- [Admin key 外洩] → 僅內部／環境變數，不進前端  
- [Ingest 部分失敗] → 不標記 resolved  
- [Nav 雜訊進庫] → 沿用現有 ingest（另 change 過濾）
