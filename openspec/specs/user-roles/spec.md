# user-roles Specification

## Purpose
TBD - created by archiving change user-role-admin. Update Purpose after archive.
## Requirements
### Requirement: 使用者角色欄位

系統 SHALL 在使用者文件保存 `role`，取值為 `admin` 或 `user`。建立新使用者且未指定 role 時 SHALL 預設為 `user`。讀取時若缺 role 欄位 SHALL 視為 `user`。

#### Scenario: 新建使用者預設 user

- **WHEN** 新使用者首次建立 profile 且未指定 role
- **THEN** 文件的 role 為 user

#### Scenario: admin 可通過管理閘門

- **WHEN** 已登入使用者的 role 為 admin
- **THEN** 可通過 require_admin_user 類依賴

#### Scenario: 一般使用者不可管理

- **WHEN** 已登入使用者的 role 為 user 或未設定
- **THEN** 呼叫需 admin 的端點回傳 403

