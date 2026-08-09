## Why

Web fallback 已會自動建立 pending 知識回報，後端也有 admin list／approve／reject API，但 LIFF 沒有審核頁，營運只能靠 curl。需要最小可用的 Admin 審核 UI。

## What Changes

- CARE-LIFF 新增 `/admin/knowledge-reports`：列出 pending／reviewing，詳情可一鍵核准（不選 URL）或拒絕
- 以前端讀取 `GET /api/profiles/me` 的 `role === admin` 做路由閘門；非 admin 不得進入
- Sidebar／入口僅對 admin 顯示審核連結
- 後端 API **不改**（沿用既有 admin knowledge-reports）

## Capabilities

### New Capabilities

- `admin-knowledge-reports-ui`：LIFF Admin 知識回報審核頁行為（實作於 sibling `CARE-LIFF`）

### Modified Capabilities

- （無後端 requirement 變更）

## Impact

- **CARE-LIFF**：routing、AdminRoute、API client、Admin 頁、i18n、Vitest
- **CARE**：僅 OpenSpec／superpowers 計畫；無 API 變更
- **依賴**：使用者 profile 需已設 `role=admin`
