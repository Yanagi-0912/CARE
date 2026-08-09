## Context

`RagAnswerService` 在 KB 不足時可走 `WebSearchService`（白名單 `.gov.tw` 等），成功時直接回給 Agent。`KnowledgeReportService` 已支援手動／tool 建立 pending，以及 approve → `IngestService.ingest_url`（同 URL `delete_many` 後再寫入）。兩者尚未串接；admin 也沒有列表 API。

## Goals / Non-Goals

**Goals:**
- Web fallback 成功且有白名單來源 URL → 自動 pending 回報
- pending／reviewing 同 URL 去重（刪舊留新）
- Admin 可列佇列；approve 可省略 `selected_urls`，用報告上的 URL ingest（KB 同 URL 覆蓋）

**Non-Goals:**
- Admin／LIFF UI、推播、非白名單入庫、snippet 直接入庫（approve 仍完整 scrape）

## Decisions

1. **觸發點**：`WebSearchService.answer` 在成功組好答案（含來源）後呼叫 `KnowledgeReportService.create_from_web_fallback(question, urls)`；失敗路徑（空／錯誤／cannot-answer）不呼叫。由 DI 注入 optional callback／service，避免 rag ↔ reports 循環 import。
2. **line_user_id**：用既有 `get_line_user_id()` contextvar；缺失時略過建報並 log，不影響回答。
3. **一則回報多 URL**：一次 fallback 的引用 URL（≤3）放同一 report 的 `user_source_urls`；`reason=missing`；`user_note` 標示 `auto:web-fallback`。
4. **去重**：建立前對每個 URL 刪除 status ∈ {pending, reviewing} 且 `user_source_urls` 含該 URL 的舊回報；再 insert 新 report。
5. **Approve**：`selected_urls` 可空／省略 → 使用 `report.user_source_urls`；仍須白名單；空則 400。Ingest 覆蓋沿用 `IngestService`。
6. **Admin list**：`GET /api/admin/knowledge-reports?status=pending|reviewing`（可選；預設兩者），需 `require_admin_user`，依 `created_at` 新到舊。

## Risks / Trade-offs

- [熱門題重複建報] → URL 去重；不對 resolved 自動重建（可後續加冷卻）
- [無 line_user_id 的非 LINE 呼叫] → 略過建報，回答照常
- [建報失敗] → log，不讓 fallback 回答失敗
- [Admin 缺 UI] → 先 API；curl／外部工具可審

## Migration Plan

純加性；無需資料遷移。部署後新 web fallback 才產生自動 pending。Rollback：停用建報呼叫或 feature 不注入 service。

## Open Questions

- （無；已拍板：成功 web + 白名單 URL 才 pending；同 URL 刪舊 pending／reviewing）
