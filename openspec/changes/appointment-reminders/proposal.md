## Why

LIFF 前端要新增「掛號提醒」（需求：前端 2026-09-09 文件，2026-09-10 修訂）。它與用藥提醒同構——同一套家庭權限、同一套 LINE 推播、可以幫家人設定——但有兩點本質差異：

1. **單次事件。** 某年某月某日某時某分，錯過就是錯過，要重新掛號、再等好幾週。
2. **狀態是「已出發 → 已到診」**，不是每日打卡。

後端目前沒有任何對應的資料模型與端點。

## What Changes

1. **資料模型** `appointment_reminders`：一筆提醒就是一次門診，三個推播階段的旗標直接存在同一份文件上（不另開 log collection）。`appointment_at` 是 timezone-aware 瞬間，另存使用者送來的 UTC offset。
2. **API** `/api/appointments/reminders`：GET（`target_user_id`、`include_past`）、POST、PUT（`exclude_unset`）、DELETE、`POST {id}/depart`、`POST {id}/attend`。回應附 `notify_at`（後端算好的推播時間點）。
3. **排程**：T-1h「我已出發」（本人＋家屬）、T+0「我已到診」或催促（本人＋家屬）、T+30 家屬警報（不是 attended 就發）、當日結束標記 `missed`。
4. **沿用既有引擎**：把 `MedicationScheduler` 的迴圈、心跳、推播權搶佔、收件人偏好抽成 `PushTickScheduler`，推播重試上限抽成 `push_claim.release_push_claim`；用藥與掛號共用，用藥行為不變。
5. **家屬收件人**：`FamilyAuthorizationService.notification_recipients`，新增種類 `appointment_reminder`（GUARDIAN／CAREGIVER）。三個階段共用同一份名單。
6. **LINE postback** `action=appointment_depart|appointment_attend&appointment_id=…`，與 LIFF 端點走同一個服務方法與授權判定。
7. **`GET /api/medical/facilities/{id}`**：編輯畫面依 id 重查院所的 `clinic_time`。
8. **隱私邊界**：推播只含日期時間、醫院名稱（家屬版多一個就診者名稱）；Flex builder 的參數沒有科別、醫師、看診號、備註。

### 明確不做

- 串接醫院掛號系統、使用者自訂提前多久提醒、以 `clinic_time` 驗證門診時間、交通估算與導航、與用藥資料互通（需求文件「不做」）。
- 把用藥提醒的 T+30 家屬警報改走同一個 resolver——那會改變用藥的既有行為（目前只送 `creator_user_id`），另開 change。

## Impact

- API/route：**新增** 6 支 `/api/appointments/*` 與 1 支 `/api/medical/facilities/{id}`；既有 route 的請求與回應形狀不變。
- Specs：新增 `appointment-reminders`。
- Code：`app/models/appointment.py`、`app/repositories/appointment_repository.py`、`app/repositories/push_claim.py`、`app/services/appointment/`、`app/services/scheduling/push_tick_scheduler.py`、`app/services/line_messaging/flex/appointment_flex.py`、`app/routers/users/appointments.py`；`medication_scheduler.py`／`medication_repository.py` 改為沿用共用實作（行為不變）。
- 授權表：`ResourceName`／`CLASSIFICATION_OF`／`FIELD_CLASSIFICATION` 新增 `appointment_reminder`；`NOTIFICATION_POLICY` 新增 `appointment_reminder`。
- 排程器 pod 多一個心跳 `appointment`。
- 資料：新 collection，無遷移。

## 測試計畫

見 tasks.md。所有新測試以依賴注入替身，不使用 monkeypatch；repository 與排程器以記憶體 collection 測時間窗邊界（模擬 Motor 的 naive UTC 讀回）。
