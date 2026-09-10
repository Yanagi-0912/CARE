## 1. 資料模型與授權表

- [x] 1.1 `app/models/appointment.py`：`AppointmentReminder`（儲存）、`AppointmentReminderResponse`（API）、Create／Update 請求；`notify_at`、`day_end_for`、`actionable_from`
- [x] 1.2 `family_authorization.py`：資源 `appointment_reminder`（GENERAL，全部欄位登記）、推播種類 `appointment_reminder`（GUARDIAN／CAREGIVER）
- [x] 1.3 測試 `tests/unit/appointment/test_appointment_models.py`、`tests/unit/models/test_family_authorization.py`（守門測試納入新模型）

## 2. Repository

- [x] 2.1 `app/repositories/push_claim.py`：重試上限與還原邏輯自 `MedicationLogRepository` 抽出共用
- [x] 2.2 `app/repositories/appointment_repository.py`：CRUD、原子狀態轉移、三個推播階段的時間窗查詢與搶佔、當日結束標記 missed
- [x] 2.3 測試 `tests/unit/appointment/test_appointment_repository.py`（時間窗每個邊界、搶佔重驗狀態、重試上限、missed 掃描）

## 3. 服務與排程

- [x] 3.1 `app/services/scheduling/push_tick_scheduler.py`：自 `MedicationScheduler` 抽出迴圈、心跳、`_dispatch`、`_resolve_prefs`
- [x] 3.2 `app/services/appointment/appointment_service.py`：驗證（offset 必填、未來時間、截到分鐘）、`exclude_unset`、改時間重置狀態、出發／到診（授權、冪等、衝突、門診當天）
- [x] 3.3 `app/services/appointment/appointment_scheduler.py`
- [x] 3.4 測試 `tests/unit/appointment/test_appointment_service.py`、`test_appointment_scheduler.py`；既有 `tests/unit/services/test_medication_scheduler.py`、`tests/unit/repositories/test_medication_repository.py` 不修改仍全綠

## 4. 推播與 LINE

- [x] 4.1 `app/services/line_messaging/flex/appointment_flex.py` 與 i18n（六種語言）
- [x] 4.2 dispatcher postback `appointment_depart`／`appointment_attend`
- [x] 4.3 測試 `tests/unit/appointment/test_appointment_flex.py`（隱私：builder 不收科別等參數、推播全文不含科別等字串；翻譯與 placeholder 一致；尺寸上限）、`test_appointment_postback.py`

## 5. API

- [x] 5.1 `app/routers/users/appointments.py`，掛在 `/api/appointments`
- [x] 5.2 `GET /api/medical/facilities/{facility_id}`
- [x] 5.3 測試 `tests/unit/appointment/test_appointments_router.py`（真的 FamilyAuthorizationService；回應形狀、offset、null、403／404／409／400 文案）

## 6. 收尾

- [x] 6.1 `pytest tests/` 全綠（4001 passed；唯一失敗 `test_require_magick_passes_when_binary_is_present` 需要本機安裝 ImageMagick，在 HEAD 上同樣失敗）
- [ ] 6.2 部署到 care-dev，以真實 LINE 帳號走一次 T-1h → 出發 → T+0 → 到診，與一次不按任何按鈕到 T+30
- [ ] 6.3 觀察一週推播量，確認家屬推播沒有逼近 LINE 月額度
