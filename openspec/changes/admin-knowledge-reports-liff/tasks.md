## 1. API client + role

- [x] 1.1 `profileApi`／型別支援 `role`
- [x] 1.2 `knowledgeReportsApi`：admin list／approve／reject
- [x] 1.3 可選：小 helper `isAdminRole(role)`

## 2. Admin 閘門與路由

- [x] 2.1 `AdminRoute`（讀 profile role；非 admin 導離）
- [x] 2.2 `App.tsx` 註冊 `/admin/knowledge-reports`
- [x] 2.3 Sidebar（必要時 Home）僅 admin 顯示連結 + i18n

## 3. Admin 審核頁

- [x] 3.1 頁面：列表、篩選 pending／reviewing、詳情 dialog
- [x] 3.2 核准／拒絕操作與 loading／error；成功刷新列表
- [x] 3.3 樣式沿用／延伸 KnowledgeReports CSS

## 4. 測試與勾核

- [x] 4.1 Vitest：admin 列表與操作；非 admin 被擋
- [x] 4.2 勾選本 tasks；更新 superpowers progress
