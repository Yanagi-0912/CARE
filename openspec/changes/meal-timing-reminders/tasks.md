# meal-timing-reminders 實作計畫

> 依 `proposal.md` / `design.md` / `specs/*/spec.md` 實作。執行者只看單一任務也要能做：每個任務列出檔案、介面、測試與驗證指令。任務內先寫測試與實作再一起跑（不演紅燈）；每個任務各自 commit（Conventional Commits，繁體中文）。
>
> 全域約束：
> - 後端 Definition of Done = `bash init.sh` 全綠（`init.sh` 沒有執行位元）。測試禁用 monkeypatch，一律 DI 或 `collection=` 注入；測試路徑鏡射 `app/`。
> - 回傳 `MedicationReminder` / `Medication` / `MedicationLog` 的端點一律 `response_model_by_alias=False`。
> - `MedicationReminder` 新欄位必須登錄到 `app/models/family_authorization.py` 的 `FIELD_CLASSIFICATION`（fail-closed）。
> - 本 change 新增的端點呼叫 `authz.authorize(..., has_legacy_equivalent=False)`。
> - LINE 回覆純文字不用 Markdown；推播不得含適應症。
> - 前端：顏色只在 `tokens.css`、字串全走 i18next 六語、觸控 ≥44px、rem 不用 px、表單 react-hook-form + zod、server state 只用 TanStack Query 且 key 進 `queryKeys`。交付前跑 `.claude/skills/care-frontend/SKILL.md` §9 的 lint/test/三條 grep。
> - `CARE-n8n/` 不動。

## Task 1: 後端資料模型與欄位登錄

- [x] 1.1 `app/models/medication.py`：新增
  - `MealTiming = Literal["before_meal", "after_meal", "none"]`、`MEAL_TIMING_ORDER: tuple[str, ...] = ("before_meal", "after_meal", "none")`（推播分區與排序的唯一依據）。
  - `class ReminderEntry(BaseModel)`：`meal_timing: MealTiming = "none"`、`scheduled_time: str`（`HHMM_PATTERN` 驗證）、`medication_ids: List[str] = []`。
  - `def validate_entries(entries: list[ReminderEntry]) -> list[ReminderEntry]`：至少一個、`meal_timing` 不重複、同一藥品不得出現在兩個條目，違反時 `raise ValueError`；回傳依 `MEAL_TIMING_ORDER` 排序的新 list。
  - `def derive_entry_fields(entries: list[ReminderEntry]) -> dict`：回傳 `{"entries": [dump...], "scheduled_time": min, "timeout_anchor_time": max, "medication_ids": 依條目順序去重聯集}`。所有寫入路徑（service、repository）只能用它算派生值。
  - `MedicationReminder`：新增 `entries: List[ReminderEntry] = Field(default_factory=list)`、`timeout_anchor_time: str = ""`；`@model_validator(mode="before")` 於 `entries` 缺席或空時以 `scheduled_time` + `medication_ids` 合成單一 `none` 條目；`@model_validator(mode="after")` 呼叫 `validate_entries` 並以 `derive_entry_fields` 覆寫三個派生欄位（既有規則讀回後 `timeout_anchor_time == scheduled_time`）。
  - `MedicationLog`：新增 `urgent_at: Optional[datetime] = None`、`taken_medication_ids: List[str] = Field(default_factory=list)`。
  - `class ReminderEntryInput(BaseModel)`：同 `ReminderEntry` 欄位（API 輸入）。
  - `CreateMedicationReminderRequest.slot_entries: Optional[dict[MedicationSlotType, List[ReminderEntryInput]]] = None`，validator 對每個時段跑 `validate_entries`。
  - `UpdateMedicationReminderRequest.entries: Optional[List[ReminderEntryInput]] = None`（validator 同上；null 不在 `NULLABLE_FIELDS`）。
  - `class CreateMedicationRequest(BaseModel)`：`user_id: str`、`name: str = Field(min_length=1, max_length=100)`（去頭尾空白後不得為空）。
- [x] 1.2 `app/models/family_authorization.py` `FIELD_CLASSIFICATION` 新增 `("medication_reminder", "entries"): "GENERAL"`、`("medication_reminder", "timeout_anchor_time"): "GENERAL"`。
- [x] 1.3 測試 `tests/unit/models/test_medication_models.py`：舊文件（無 `entries`）讀回合成單一 `none` 條目且 `timeout_anchor_time == scheduled_time`；兩條目時 `scheduled_time` 取最早、`timeout_anchor_time` 取最晚、`medication_ids` 為聯集；重複 `meal_timing` 拒絕；同一藥品跨條目拒絕；`slot_entries` 的 `"9am"` 拒絕；`MedicationLog` 缺 `urgent_at` / `taken_medication_ids` 讀回為 `None` / `[]`。`tests/unit/models/test_family_authorization.py` 既有守門測試須維持通過。
- [x] 1.4 `python -m pytest tests/unit/models -q` 全綠；commit `feat(medication): 提醒規則新增飯前飯後條目模型`。

## Task 2: Repository 層

- [x] 2.1 `MedicationReminderRepository.find_or_create_reminder`：`$setOnInsert` 加 `"entries": [{"meal_timing": "none", "scheduled_time": scheduled_time, "medication_ids": []}]` 與 `"timeout_anchor_time": scheduled_time`。更新 `tests/unit/repositories/test_medication_repository.py::test_find_or_create_reminder_upserts_atomically` 的斷言。
- [x] 2.2 `MedicationReminderRepository.link_medications_to_reminder(reminder_id, medication_ids, collection=None)` 改為三個各自原子、冪等的更新（順序固定）：
  1. `update_one({"_id": id, "entries": {"$exists": False}}, {"$set": {"entries": [none 條目，time 取文件 scheduled_time、ids 取文件 medication_ids]}})` —— 需先 `find_one` 取這兩個值，只在舊文件才會命中。
  2. `update_one({"_id": id, "entries.meal_timing": {"$ne": "none"}}, {"$push": {"entries": {"meal_timing": "none", "scheduled_time": <文件 scheduled_time>, "medication_ids": []}}})`。
  3. `update_one({"_id": id}, {"$addToSet": {"entries.$[none].medication_ids": {"$each": ids}, "medication_ids": {"$each": ids}}, "$set": {"updated_at": now}}, array_filters=[{"none.meal_timing": "none"}])`。
  `none` 條目時刻等於規則 `scheduled_time`（最早），派生值不變，因此不需重算。測試：舊文件（無 entries）、已有飯前飯後但無 none、已有 none 三種情境各斷言送出的 filter/update。
- [x] 2.3 `MedicationReminderRepository.update_reminder(reminder_id, update_data, collection=None)`：加 `collection=` 縫，行為不變（service 端負責把派生欄位放進 `update_data`）。
- [x] 2.4 `MedicationRepository` 新增 `list_by_user(user_id, collection=None) -> List[Medication]`（不篩 `enabled` 與日期，依 `created_at` 升冪）與 `create_one(medication, collection=None) -> Medication`（包 `create_many`）。測試各一。
- [x] 2.5 `MedicationLogRepository`：
  - `list_pending_urgent_reminders(threshold_time, collection=None)` 查詢改為 `{"status": "pending", "patient_reminder_sent": True, "urgent_reminder_sent": False, "$or": [{"urgent_at": {"$lte": threshold_time}}, {"urgent_at": {"$exists": False}, "scheduled_at": {"$lte": threshold_time - 20min}}]}`；呼叫端改傳 `now`（不再自己減 20 分鐘）。
  - 新增 `add_taken_medication(log_id, medication_id, collection=None) -> Optional[MedicationLog]`：`update_one({"_id": id}, {"$addToSet": {"taken_medication_ids": medication_id}})` 後回傳最新 log。
  - `mark_as_taken(log_id, taken_at=None, taken_medication_ids=None, collection=None)`：有給 ids 時同一個 update 加 `$addToSet: {"taken_medication_ids": {"$each": ids}}`。
  - `resync_pending_by_reminder(reminder_id, scheduled_at, slot_type, urgent_at=None, timeout_at=None, collection=None)`：改標查詢改為 `scheduled_at` 相同且（`slot_type` 不同 或 `urgent_at` 不同 或 `timeout_at` 不同），`$set` 三個欄位；回傳值不變。
  - 測試：雙分支查詢形狀、`add_taken_medication` 的 `$addToSet`、`mark_as_taken` 帶 ids、`resync` 只改 `urgent_at/timeout_at` 的情境。
- [x] 2.6 `python -m pytest tests/unit/repositories -q` 全綠；commit `feat(medication): repository 支援條目、逐藥確認與催促基準`。

## Task 3: Service 層

- [x] 3.1 `MedicationService.create_reminders`：
  - 先 `self._reminder_repository.list_reminders_by_user(target)`，請求的任一時段已有規則 → `HTTPException(409, "時段 X 已有用藥提醒")`，不建立任何規則。
  - 每個時段：`slot_entries` 有給 → 用它；否則單一 `none` 條目、時間取 `slot_times` 或預設。呼叫 `await self._assert_medications_belong(target, ids)`（`self._medication_repository.find_by_ids`，任一不屬於 target → 400）。
  - 建構 `MedicationReminder(entries=...)`（模型自動派生），改用 `self._reminder_repository.create_reminder`。
- [x] 3.2 `MedicationService.update_reminder`：
  - 規則條目數 > 1 且請求帶 `scheduled_time` → 400「此提醒有多個服藥時機，請改用詳細設定調整時間」。
  - 請求帶 `entries` → 驗證藥品歸屬，`update_data` 以 `derive_entry_fields` 展開（移除原 `entries` 輸入鍵、寫入四個欄位）。
  - 請求只帶 `scheduled_time`（單條目）→ 同步改寫該條目時刻並展開派生欄位（保持條目與派生一致）。
  - 對齊：`updated.scheduled_time != reminder.scheduled_time` 或 `slot_type` 改變 → 既有路徑；否則 `timeout_anchor_time` 改變 → `resync_pending_by_reminder(..., urgent_at=anchor+20min, timeout_at=anchor+30min)`。`_suppress_stale_new_slot` 與關閉路徑不變。
- [x] 3.3 `MedicationService.confirm_medication(log_id, user_id, medication_id=None) -> MedicationLog`：改用 `self._log_repository`。
  - 無 `medication_id`：`expected = await self._expected_medication_ids(log)`；`mark_as_taken(log_id, taken_medication_ids=expected)`。
  - 有 `medication_id`：不在 `expected` 內仍 `add_taken_medication`（冪等、無害）；之後若 `expected ⊆ log.taken_medication_ids` → `mark_as_taken(log_id)`；否則回傳更新後的 log（status 不變）。
  - `_expected_medication_ids(log)`：取規則 `medication_ids` 於 log 台北日期的有效藥品 id（`find_active_by_ids`），與 `list_medication_names_for_log` 共用同一段查詢（抽成 `_active_medications_for_log(log) -> list[Medication]`）。
- [x] 3.4 新增 `MedicationService.list_medications(user_id) -> List[Medication]`（`list_by_user` + `_resolve_thumbnail` / `_resolve_indication` 覆寫，與 `get_user_reminders_with_medications` 同一套解析）與 `create_manual_medication(creator_user_id, request: CreateMedicationRequest) -> Medication`（`source="manual"`, `frequency_code="OTHER"`, 外觀欄位空字串，`create_one`）。
- [x] 3.5 新增 `MedicationService.medication_groups_for_log(log) -> list[MedicationGroup]`：`MedicationGroup = NamedTuple(meal_timing, scheduled_time, items: list[tuple[str, MedicationListEntry]])`（定義在 `app/services/medication/medication_groups.py`，供排程器與 dispatcher 共用），依 `MEAL_TIMING_ORDER` 排序、只含當日有效藥品、`items` 排除 `log.taken_medication_ids`；另提供 `taken_names_for_log(log) -> list[str]`。
- [x] 3.6 測試 `tests/unit/services/test_medication_service.py`（沿用 `_service_with_fakes` 的 fake repo 模式，把 `FakeReminderRepository` 補上 `create_reminder`、`FakeLogRepository` 補上 `get_log_by_id`/`mark_as_taken`/`add_taken_medication`，新增 `FakeMedicationRepository.find_active_by_ids`/`list_by_user`/`create_one`）：409 重複時段；`slot_entries` 建立的派生值；他人藥品 400；多條目帶 `scheduled_time` 400；只改飯後時刻觸發帶 `urgent_at/timeout_at` 的 resync；逐藥確認未到齊維持 pending 並回覆剩餘；到齊轉 taken；停用藥不擋完成；整批確認寫入全部 ids。既有以 `patch(...)` 寫的 `create_reminders`/`confirm_medication` 測試改為 DI。
- [x] 3.7 `python -m pytest tests/unit/services/test_medication_service.py -q` 全綠；commit `feat(medication): 服務層支援條目建立更新與逐藥確認`。

## Task 4: Router 與 OCR 路徑

- [x] 4.1 `app/routers/users/medications.py` 新增：
  - `GET ""`（`response_model=List[Medication]`, `response_model_by_alias=False`, `user_id: Optional[str] = Query(None)`）：本人直接回；他人 `authz.authorize(op, user, "GENERAL", "READ", has_legacy_equivalent=False)` 後 `mask_response([...], "medication", op, user)`。
  - `POST ""`（`response_model=Medication`, `response_model_by_alias=False`, body `CreateMedicationRequest`）：`authorize(op, req.user_id, "GENERAL", "WRITE", has_legacy_equivalent=False)` → `service.create_manual_medication`。
  - `POST /reminders` 與 `PUT /reminders/{id}` 簽名不變（409/400 由 service 拋）。
- [x] 4.2 `tests/unit/routers/test_medications_router.py`：新端點的 200 形狀（`id` 非 `_id`）、`_PermissiveAuthz` 注入；`tests/unit/routers/test_endpoint_authorization.py`：MEMBER 對他人 `POST /medications` 403、無關者 `GET /medications?user_id=` 403、GUARDIAN 200（`wire_*` 的 fake service 補兩個方法）。
- [x] 4.3 `app/services/medication/prescription_scan_service.py` `_link_reminders` 程式不變；`_ReminderRepository` Protocol 不變。補一個測試：`FakeReminderRepository` 的規則帶飯前飯後條目時，提交後 `links` 仍只記錄 `(reminder_id, [medication_id])`（證明 OCR 路徑不碰條目）。
- [x] 4.4 `python -m pytest tests/unit/routers tests/unit/services/medication -q` 全綠；commit `feat(medication): 藥品列出與手動新增端點`。

## Task 5: 排程器

- [x] 5.1 `medication_scheduler.py` `process_ticks`：展開紀錄時 `anchor_dt = strptime(today + reminder.timeout_anchor_time)`；`urgent_at = anchor_dt + 20min`、`timeout_at = anchor_dt + 30min` 寫進 `MedicationLog(...)`。階段 2 改呼叫 `list_pending_urgent_reminders(threshold_time=current_time)`。misfire 判定仍用 `scheduled_dt`。
- [x] 5.2 `_TickMedicationNameCache` 新增 `get_groups(log) -> list[MedicationGroup]`：以既有 `_load` 的 reminders/medications 批次資料，按 `reminder.entries` 分組、排除 `log.taken_medication_ids`；`get_entries` 維持（供家屬警報與完成卡）。
- [x] 5.3 `_send_patient_reminder` / `_send_urgent_reminder` 改傳 `medication_groups=await cache.get_groups(log)`（見 6.2 的新參數）。
- [x] 5.4 `tests/unit/services/test_medication_scheduler.py`：飯前 07:30／飯後 08:30 規則於 07:30 展開的 log 帶 `urgent_at=08:50`、`timeout_at=09:00`；階段 2 以 `now` 呼叫；`get_groups` 分組與排除已確認藥品（`collection=` 注入風格）。既有守門測試 `test_process_ticks_builds_exactly_one_cache_per_stage_outside_the_loop` 維持。
- [x] 5.5 `python -m pytest tests/unit/services/test_medication_scheduler.py -q` 全綠；commit `feat(medication): 排程器以最晚條目時刻計算催促與逾時`。

## Task 6: Flex、i18n 與 postback

- [x] 6.1 `app/i18n/messages.py` 新增六語：`meal.before_meal`（飯前 / Before meal / Sebelum makan / Trước ăn / ก่อนอาหาร / 食前）、`meal.after_meal`（飯後 / After meal / Sesudah makan / Sau ăn / หลังอาหาร / 食後）、`meal.none`（其他 / Other / Lainnya / Khác / อื่น ๆ / その他）、`flex.med.group_heading`（`{meal}　{time}`）、`flex.med.button.taken_one`（已吃 / Taken / Sudah / Đã uống / ทานแล้ว / 服用済み）、`flex.med.display.taken_one`（`我吃了 {name}`）、`flex.med.button.taken_all`（全部已服用 / All taken / …）、`meds.progress`（`已記錄：{taken}。還有 {count} 種：{remaining}`）、`meds.progress_none_left`（`已記錄：{taken}`）。`tests/unit/i18n` 既有的六語完整性測試須過。
- [x] 6.2 `medication_flex.py`：
  - `build_patient_medication_flex(..., medication_groups: Optional[list[MedicationGroup]] = None)`、`build_patient_urgent_reminder_flex(..., medication_groups=None)`。有 `medication_groups` 且非 `disabled` 時：依序每組一個小標（單一 `none` 組時不顯示小標）＋每列 `_medication_row_node` 右側加 `ft.secondary_button(t("flex.med.button.taken_one"), {"type":"postback","data": f"action=confirm_medication&log_id={log_id}&medication_id={mid}", "displayText": t("flex.med.display.taken_one").format(name=...)})`；仍受 `MEDICATION_LIST_MAX_ITEMS`（跨組合計）收斂，超出者無按鈕；footer 按鈕文案改 `flex.med.button.taken_all`。`medication_groups` 為 `None` 或全空時走既有 `medication_names` 版面。
  - 家屬卡片不動。
- [x] 6.3 `dispatcher.py` `confirm_medication` 分支：解析 `medication_id`；`log = await service.confirm_medication(log_id, user_id, medication_id=medication_id)`；`log.status == "taken"` → 既有完成卡；否則 `reply` 純文字 `meds.progress`（`taken_names_for_log` / `medication_groups_for_log` 取名單）。
- [x] 6.4 測試 `tests/unit/services/line_messaging/test_medication_flex.py`（分組小標、逐藥 postback data、單一 none 組無小標、無藥品時版面與舊版相同）、`tests/unit/services/line_messaging/test_event_handler.py`（逐藥 postback 未到齊回純文字、到齊回 Flex；fake service 記錄呼叫參數）。
- [x] 6.5 `python -m pytest tests/unit/services/line_messaging tests/unit/i18n -q` 全綠；commit `feat(line): 服藥提醒依飯前飯後分區並支援逐藥確認`。

## Task 7: 後端收尾

- [x] 7.1 `bash init.sh` 全綠；`.env.example` 不需新變數。
- [x] 7.2 更新 `openspec/specs/medication-reminders/spec.md` 與 `medication-identification/spec.md`：把 `openspec/changes/meal-timing-reminders/specs/*` 的 ADDED / MODIFIED 條文合併進 living spec（本機沒有 `openspec` CLI，手動合併）。
- [x] 7.3 commit `docs(openspec): 合併 meal-timing-reminders 的 spec 條文`。

## Task 8: 前端：型別、API、i18n

- [x] 8.1 `src/types/medication.ts`：`MealTiming`、`MEAL_TIMING_ORDER`、`MEAL_LABEL_KEY: Record<MealTiming, string>`（`meds.meal.before_meal` 等）、`ReminderEntry { meal_timing; scheduled_time; medication_ids }`；`MedicationReminder` 加 `entries: ReminderEntry[]`、`timeout_anchor_time: string`；`CreateRemindersRequest` 加 `slot_times?: Partial<Record<MedicationSlotType, string>>`、`slot_entries?: Partial<Record<MedicationSlotType, ReminderEntry[]>>`；`UpdateReminderRequest` 加 `entries?: ReminderEntry[]`；`CreateMedicationRequest { user_id; name }`。修正 `DEFAULT_SLOT_TIMES` 上「新增請求不帶 slot_times」的註解。
- [x] 8.2 `src/api/medicationApi.ts`：`fetchMedications(targetUserId?) -> Medication[]`（`GET /api/medications?user_id=`）、`createMedication(req) -> Medication`（`POST /api/medications`）；修正 `createReminders` 的註解。`src/lib/queryClient.ts` 加 `medicationList: (targetUserId?) => ['medication-list', targetUserId ?? 'self'] as const`。
- [x] 8.3 `src/i18n/medicationMessages.ts` 六語新增：`meds.meal.before_meal`、`meds.meal.after_meal`、`meds.meal.none`、`meds.add.timeField`（提醒時間）、`meds.add.timeNote` 改寫為「可直接調整時間；需要飯前飯後分開提醒請用詳細設定」、`meds.add.detailed`（詳細設定）、`meds.detailed.title`、`meds.detailed.back`、`meds.detailed.slotSummaryEmpty`（尚未設定）、`meds.detailed.entrySummary`（`{{meal}} {{time}} · {{count}} 種藥`）、`meds.detailed.enableTiming`（`提醒{{meal}}`）、`meds.detailed.timeFor`（`{{meal}}時間`）、`meds.detailed.medsHeading`（藥品）、`meds.detailed.assignTo`（`放到{{meal}}`）、`meds.detailed.unassigned`（未指派）、`meds.detailed.noMeds`、`meds.detailed.addMedName`、`meds.detailed.addMed`、`meds.detailed.addMedSuccess`、`meds.detailed.needTiming`（請至少開啟一個時機）、`meds.detailed.save`、`meds.detailed.saveSuccess`、`meds.edit.multiEntryNote`（此提醒有飯前飯後多個時間，請到詳細設定調整）、`meds.edit.openDetailed`。跑 `npx vitest run src/tests/i18n.test.ts`。
- [x] 8.4 `npm run build` 通過；commit `feat(medications): 飯前飯後條目的型別、API 與文案`。

## Task 9: 前端：新增表單可設時間 + 詳細設定入口

- [x] 9.1 `ReminderFormDialog.tsx`：schema 加 `slotTimes: z.record(z.enum(SLOT_TYPES), z.string().regex(/^\d{2}:\d{2}$/))`，預設 `DEFAULT_SLOT_TIMES`；每個勾選的時段在卡片**下方**（`FieldLabel` 之外，避免點時間觸發 checkbox）渲染 `<Input type="time" id={`slot-time-${slot}`} aria-label={t('meds.add.timeField')}>`；`onSubmit` 改為 `(payload: { slots; slotTimes; startDate; endDate? }) => Promise<void>`；新增 `onOpenDetailed: () => void` prop，footer 左側加 `variant="outline"` 的「詳細設定」鈕（關閉 dialog 並切換檢視）。
- [x] 9.2 `index.tsx`：`handleCreate` 送 `slot_times`；新增 `view` 狀態（見 10.x）。
- [x] 9.3 `src/tests/medications.test.tsx`：更新 `vi.mock` 工廠補 `fetchMedications`/`createMedication`；新增「勾選早改 07:30 後送出帶 slot_times」測試；既有測試對齊。
- [x] 9.4 `npm run test` 全綠；commit `feat(medications): 新增提醒時可直接設定時間`。

## Task 10: 前端：詳細設定檢視

- [x] 10.1 `src/pages/Medications/useMedicationList.ts`：`useMedicationList(targetUserId?)` → `{ medications, loading, addMedication(name) }`（`useQuery(queryKeys.medicationList)` + `useMutation(createMedication)` 成功後 `setQueryData` 追加）。
- [x] 10.2 `src/pages/Medications/DetailedSetupView.tsx`：props `{ targetUserId?: string; targetName: string; reminders: MedicationReminder[]; onBack(); onCreate(slot, entries, startDate); onUpdate(reminderId, entries) }`。第一層：`ItemGroup` 四張時段卡（`SLOT_TONE` 徽章、條目摘要或「尚未設定」），點擊進入 `SlotEntryEditor`。
- [x] 10.3 `src/pages/Medications/SlotEntryEditor.tsx`：react-hook-form + zod，欄位 `before: { enabled, time }`、`after: { enabled, time }`、`assignments: Record<medicationId, MealTiming | null>`；規則：至少一個時機啟用；既有 `none` 條目的藥品以「未指派」顯示且保留在 `none` 條目送出（不遺失 OCR 掛上的藥）。藥品清單每張 `Item` 顯示 `PillThumbnail`（無圖不佔位）、藥名、`formatAppearancePrimary` 文字，右側 `ToggleGroup variant="primary"` 兩顆【飯前】【飯後】（value 陣列單選處理，同 `index.tsx` 的作法；再點一次取消 → 未指派）。底部「新增藥品」`Input` + 按鈕（呼叫 `addMedication`）。儲存：組 `entries`（before/after 各一、若有未指派藥品加 `none` 條目，時間取規則既有 `none` 時刻或最早啟用時機的時刻），有規則走 `onUpdate`、無則 `onCreate`。
- [x] 10.4 `index.tsx`：`const [view, setView] = useState<'list' | 'detailed'>('list')`；`view === 'detailed'` 時以 `DetailedSetupView` 取代標題列以下的清單與新增鈕（對象 chips 保留）；`onCreate` → `create({ user_id, slots: [slot], slot_entries: { [slot]: entries }, start_date })`；`onUpdate` → `update(id, { entries })`；成功 toast `meds.detailed.saveSuccess`。
- [x] 10.5 `src/tests/medicationsDetailed.test.tsx`：打開詳細設定 → 點「早」→ 開飯前 07:30、飯後 08:30 → 指派兩顆藥 → 儲存，斷言 `createReminders` 收到的 `slot_entries`；既有規則走 `updateReminder` 帶 `entries`；未開任何時機顯示錯誤；新增藥品後出現在清單。
- [x] 10.6 `npm run test` 全綠；commit `feat(medications): 詳細設定檢視可設定飯前飯後並指派藥品`。

## Task 11: 前端：卡片與編輯視窗

- [x] 11.1 `ReminderCard.tsx`：`entries.length > 1` 或含非 `none` 條目時，在日期列下方列出每個條目一行 `Badge`（`meds.meal.*`）＋時間（`num`）＋藥名（以 `medications` 依 id 對照）；單一 `none` 條目維持原版面。`aria-label` 沿用。
- [x] 11.2 `ReminderEditDialog.tsx`：`reminder.entries.length > 1` 時隱藏時間欄位，改顯示 `meds.edit.multiEntryNote` 與「到詳細設定調整」按鈕（新增 prop `onOpenDetailed(reminder)`）；其餘欄位照舊。`index.tsx` 接上（切到 detailed 並預選該時段：`DetailedSetupView` 加 `initialSlot?: MedicationSlotType`）。
- [x] 11.3 測試：卡片顯示兩條目；多條目編輯無時間欄位且按鈕切換檢視。`npm run test` 全綠；commit `feat(medications): 提醒卡與編輯視窗呈現飯前飯後條目`。

## Task 12: 前端收尾與 e2e

- [x] 12.1 `e2e/medications.spec.ts`：`page.route('**/api/medications/**')` 與 `**/api/medications?user_id=*` stub（含 OPTIONS 204 與 CORS 標頭，比照 `personalhealth.spec.ts`），`localStorage` 設 `CARE_AUTH_TOKEN` 與 `CARE_LINE_USER_ID`；一條流程：新增表單改時間送出 → 詳細設定建立飯前飯後 → 卡片顯示兩行。本機只跑 `npx playwright test e2e/medications.spec.ts --project=chromium`（WebKit 缺系統函式庫）。
- [x] 12.2 依 SKILL.md §9：`npm run lint`、`npm run test`、`npm run build`、三條 grep（字面色 0、Tailwind 色階 0、hardcode 中文不新增）。四種主題與 16/20/24px 字級以 dev server 目視（併入 13.x 手動測試）。
- [x] 12.3 commit `test(e2e): 飯前飯後提醒設定流程`。

## Task 13: 手動測試環境（交付前）

- [x] 13.1 後端：`cd CARE && APP_ROLE=api CORS_ALLOW_ORIGINS=http://localhost:5173 .venv/bin/uvicorn app.main:app --port 8000`（`APP_ROLE=api` 關掉排程器，不會真的推 LINE 訊息；連的是 `.env` 的 MongoDB）。
- [x] 13.2 假登入：以 `AUTH_JWT_SECRET`（預設 `dev-only-change-me`）用 PyJWT 簽 `{"sub": "U_MANUAL_TEST", "iss": "care-backend", "exp": ...}`，寫成 `scratchpad/manual-login.js` 供瀏覽器 console 貼上：`localStorage.setItem('CARE_AUTH_TOKEN', ...)`、`localStorage.setItem('CARE_LINE_USER_ID', 'U_MANUAL_TEST')`。
- [x] 13.3 前端：`cd CARE-LIFF && VITE_LIFF_ID= VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev`（清空 `VITE_LIFF_ID` 讓非 LINE 瀏覽器跳過 `liff.init`）。
- [x] 13.4 給使用者：`http://localhost:5173/medications`、貼 console 的登入指令、測試腳本（新增表單改時間 → 詳細設定 → 卡片 → 編輯），以及清理測試資料的方式（`U_MANUAL_TEST` 的規則與藥品可在頁面上刪除）。
- [x] 13.5 使用者驗收後：`superpowers:finishing-a-development-branch`（兩個 repo 各開 PR 到 main，PR 描述繁體中文）。
