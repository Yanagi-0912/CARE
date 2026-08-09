## ADDED Requirements

### Requirement: Admin 可檢視待審知識回報

已登入且 `role=admin` 的使用者 SHALL 可開啟 LIFF Admin 知識回報頁，載入 `GET /api/admin/knowledge-reports` 的 pending／reviewing 佇列，並檢視問題、原因、備註與來源 URL。

#### Scenario: Admin 載入佇列

- **WHEN** admin 開啟審核頁
- **THEN** 系統顯示待審回報列表（可依 pending／reviewing 篩選）

### Requirement: Admin 可核准或拒絕回報

Admin SHALL 能對選定回報呼叫核准（不強制選 URL，沿用報告 `user_source_urls`）或拒絕 API，並在成功後更新列表狀態。

#### Scenario: 一鍵核准

- **WHEN** admin 對含來源 URL 的 pending 回報按下核准
- **THEN** 前端呼叫 approve API（可不帶 selected_urls），成功後該筆自待審列表移除或狀態更新

#### Scenario: 拒絕回報

- **WHEN** admin 按下拒絕
- **THEN** 前端呼叫 reject API，成功後該筆自待審列表移除或狀態更新

### Requirement: 非 Admin 不得進入審核頁

非 admin 使用者 SHALL NOT 使用 Admin 審核頁；前端 MUST 阻擋進入，後端 API 仍以 403 為最終防護。

#### Scenario: 一般使用者開啟審核路由

- **WHEN** `role` 不為 admin 的使用者導向審核頁
- **THEN** 不顯示審核操作，並導離或顯示無權限
