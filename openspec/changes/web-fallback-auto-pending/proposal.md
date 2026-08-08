## Why

CRAG 落到白名單網路回答時，知識缺口只補答給使用者，不會進入審核佇列；營運無法一鍵把已驗證來源入庫。需要在成功 web fallback 時自動建立 pending 知識回報，讓管理員同意即可 ingest。

## What Changes

- Web fallback **成功**且引用白名單 URL 時，自動建立 `KnowledgeReport`（`status=pending`、`reason=missing`），來源 URL 寫入 `user_source_urls`
- 若已有 `pending`／`reviewing` 回報含相同 URL，刪除舊回報後再建立新回報
- Admin approve：**可不傳** `selected_urls`，預設使用報告上的 URL；ingest 沿用既有「同 URL 先刪再寫」覆蓋行為
- 新增 `GET /api/admin/knowledge-reports` 供營運列出待審佇列（`pending`／`reviewing`）
- Web 失敗（`WEB_EMPTY`／`WEB_ERROR`／`MODEL_REFUSE`）或未走 web **不**自動建回報

## Capabilities

### New Capabilities

- `knowledge-reports`: Web fallback 自動 pending、URL 去重、approve 預設來源、admin 佇列列表（主規格尚未 archive 既有回報能力；本 change 只規範本次增量行為，沿用既有 create／approve／reject 實作）

### Modified Capabilities

- `rag-responses`: web fallback 成功後觸發自動知識回報

## Impact

- **API**：`GET /api/admin/knowledge-reports`（新）；`POST .../approve` 的 `selected_urls` 改為可選
- **服務**：`WebSearchService`／`RagAnswerService` 成功路徑串接 `KnowledgeReportService`；repo 新增依 URL／status 刪除
- **Auth**：admin 列表沿用 `require_admin_user`
- **測試**：單元測自動建報、去重、approve 省略 URL、admin list；mock ingest／repo
- **非目標**：完整 Admin UI、LIFF 改版、推播通知、非白名單 URL
