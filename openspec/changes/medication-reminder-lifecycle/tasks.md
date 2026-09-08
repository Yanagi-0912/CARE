## 1. 展開判定納入藥品有效性（根因）

- [x] 1.1 `MedicationScheduler._resolve_suppressed_reminder_ids`：整批查一次 `find_active_by_ids`，挑出「掛了藥、但當日一顆有效的都不剩」的規則 id；沒有任何規則掛藥時不發出查詢；查詢失敗回傳空集合（fail-open）
- [x] 1.2 `process_ticks` 階段 1 對抑制中的規則 `continue`，不展開當日紀錄
- [x] 1.3 `MedicationLogRepository.cancel_pending_by_reminder_ids`：批次作廢，帶 `scheduled_from` 下界把範圍限制在當日
- [x] 1.4 `process_ticks` 呼叫上述批次作廢，僅在真的改動紀錄時記錄一行（避免每輪重印）
- [x] 1.5 測試 `tests/unit/services/test_medication_scheduler.py`
      - `test_expired_course_does_not_expand_log`：全部失效 → 不展開
      - `test_reminder_without_linked_medications_still_expands`：`medication_ids` 為空的舊規則照常展開，且完全不發出藥品查詢
      - `test_partially_expired_course_still_expands`：還有一顆有效 → 照常展開
      - `test_expired_course_cancels_already_expanded_logs`：作廢當日殘留紀錄，下界為當日 00:00
      - `test_medication_lookup_failure_does_not_suppress`：查詢拋例外 → 不抑制
- [x] 1.6 測試 `tests/unit/repositories/test_medication_repository.py`
      - `test_cancel_pending_by_reminder_ids_batches_with_date_floor`：查詢形狀與 `$set`
      - `test_cancel_pending_by_reminder_ids_skips_empty_input`：空輸入不發出查詢

## 2. 推播重試上限（後果）

- [x] 2.1 `MedicationLog` 新增 `patient_reminder_attempts`／`urgent_reminder_attempts`／`caregiver_alert_attempts`，預設 0
- [x] 2.2 `MedicationLogRepository._release_push_claim`：先 `$inc` 再依結果決定是否清掉旗標；達上限記 error 並放棄
- [x] 2.3 三支 `release_*` 改走上述共用實作；`release_caregiver_alert` 保留 `status="missed"` 條件與 `status→pending` 的回寫
- [x] 2.4 測試 `tests/unit/repositories/test_medication_repository.py`
      - `test_release_patient_reminder_increments_and_retries`：累加後還原
      - `test_release_gives_up_at_attempt_cap`：達上限不還原
      - `test_release_on_legacy_log_without_attempts_field`：缺欄位的舊紀錄第一次仍重試
      - `test_release_returns_false_when_log_no_longer_matches`：不符條件時不做任何寫入
      - `test_release_caregiver_alert_does_not_clobber_taken`（既有測試，改寫為對應新實作）

## 3. 停機補償訊息不再洗版

- [x] 3.1 misfire 訊息移至 `upsert_log` 之後，與家屬通知共用 `created` 把關
- [x] 3.2 測試 `tests/unit/services/test_medication_scheduler.py::test_misfire_log_only_on_first_creation`

## 4. 既有髒資料清理

- [x] 4.1 `scripts/cleanup_expired_course_logs.py`：預設只讀不寫，`--apply` 才寫入；`taken` 一律不動；`pending` 與 `missed` 筆數分開印
- [x] 4.2 測試 `tests/unit/scripts/test_cleanup_expired_course_logs.py`：naive UTC → 台北日期換算、療程邊界日、單顆存活、無關聯藥品的規則不在範圍內

## 5. 收尾

- [x] 5.1 `pytest tests/` 全綠（2265 passed）
- [ ] 5.2 於 care-dev 以 `--user-id U03c4d5f92d5abfcd71ebaf1babaf9539` 執行清理腳本的只讀模式，核對清單筆數與 11 天 × 3 則的推估相符
- [ ] 5.3 部署後確認該使用者不再收到空提醒卡，且排程器 log 不再每分鐘重印 misfire 訊息
