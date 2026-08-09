> 測試一律以依賴注入傳入替身：router 用 `app.dependency_overrides`、service 用建構子參數、repository 用 `collection=` 參數。禁止 `unittest.mock.patch` 修改全域或別處導入的實例。
> 先寫測試再寫實作：每個 `X.Y` 實作任務都有對應的測試任務，測試先紅再綠。
> 完成定義：`./init.sh` 全綠且有清楚的 git commit。
> `PRESCRIPTION_SCAN_ENABLED` 在第 9 節之前一律維持 `false`，讓每一節都能獨立合併而不改變線上行為。

## 1. 設定與資料模型

- [ ] 1.1 `app/core/config.py` 新增 `PRESCRIPTION_SCAN_ENABLED`（預設 `false`）、`PRESCRIPTION_SCAN_MAX_IMAGE_BYTES`（預設 8388608）、`PRESCRIPTION_DRAFT_TTL_MINUTES`（預設 60）、`DRUG_CATALOG_PATH`（預設 `resources/drug_catalog.json`），並同步 `.env.example`
- [ ] 1.2 `app/models/medication.py` 的 `MedicationReminder` 新增 `medication_ids: list[str] = Field(default_factory=list)`；`MedicationReminderResponse` 同步新增
- [ ] 1.3 新增 `app/models/prescription.py`：`FrequencyCode`（`QD`／`BID`／`TID`／`QID`／`HS`／`PRN`／`OTHER`）、`DrugTiming`、`ConfidenceLevel`（`high`／`medium`／`low`）、`ScanFailureReason`（`unreadable`／`not_prescription`／`service_unavailable`）
- [ ] 1.4 `app/models/prescription.py` 新增 `RecognizedDrug`（`name`／`generic_name`／`unit_content`／`total_quantity`／`usage_raw`／`frequency_code`／`dose_per_time`／`timing`／`duration_days`／`indication`／`license_number`／`name_confidence`）與 `RecognitionResult`（`institution`／`patient_name`／`dispensed_date`／`drugs`／`multiple_bags_suspected`）
- [ ] 1.5 `app/models/prescription.py` 新增 `PrescriptionDraft`（`draft_id`／`creator_user_id`／`recognition`／`confidence_level`／`created_at`／`expires_at`／`committed_at`／`committed_medication_ids`）
- [ ] 1.6 新增 `app/models/medication.py` 的 `Medication`（`id`／`user_id`／`created_by_user_id`／`name`／`generic_name`／`license_number`／`unit_content`／`total_quantity`／`usage_raw`／`frequency_code`／`indication`／`source`／`start_date`／`end_date`／`enabled`／`created_at`／`updated_at`）
- [ ] 1.7 `app/db/mongodb.py` 新增 `get_medications_collection()` 與 `get_prescription_drafts_collection()`
- [ ] 1.8 `tests/unit/models/test_medication_models.py` 補：`MedicationReminder` 未給 `medication_ids` 時為空陣列；以缺該欄位的 dict 建構仍成立（既有資料相容）
- [ ] 1.9 `tests/unit/models/test_prescription_models.py`（新增）：頻次代碼列舉的合法值、`RecognizedDrug` 各欄位可為空、`PrescriptionDraft` 預設未提交

## 2. 藥證庫：建置腳本與比對服務

- [ ] 2.1 新增 `scripts/build_drug_catalog.py`：下載全部藥品許可證資料集與藥品外觀資料集（兩者皆回傳 ZIP，需解壓後再解析 JSON），輸出 `resources/drug_catalog.json`，每筆含 `license_number`／`name_zh`／`name_en`／`normalized_keys`
- [ ] 2.2 新增 `app/services/medication/drug_catalog_service.py` 的 `DrugCatalogService`，建構子接受**已載入的條目清單**（不是路徑），另提供 `load_from_path()` classmethod 供組裝點使用
- [ ] 2.3 實作名稱正規化：去除引號與全半形差異、去除廠商前綴、統一空白與大小寫；先完全比對，未命中再以相似度比對取最高分，低於門檻視為未命中
- [ ] 2.4 `match(name)` 回傳命中的 `license_number`／`name_zh`／`name_en` 與信心度；未命中時信心度為 `low`
- [ ] 2.5 `tests/unit/services/medication/test_drug_catalog_service.py`（新增）：以小型固定清單建構服務，涵蓋完全比對命中、正規化後命中（引號／全形／廠商前綴）、形近但低於門檻視為未命中、空清單時全部未命中且不拋例外

## 3. 辨識服務

- [ ] 3.1 新增 `app/services/medication/prescription_ocr_service.py` 的 `PrescriptionOcrService`，建構子注入 `gemini_service` 與逾時設定
- [ ] 3.2 定義輸出 schema 與提示詞：欄位無法判讀時回空值而非推測；`usage_raw` 保留藥袋原始字串；頻次無法明確歸類時一律 `OTHER`
- [ ] 3.3 `recognize(image_bytes, mime_type)` 回傳 `RecognitionResult`；辨識不到任何藥品項目時以 `not_prescription` 失敗
- [ ] 3.4 例外映射：逾時與外部呼叫失敗 → `service_unavailable`；回傳結構無效或無法解析 → `unreadable`。三者以可區分的例外型別表達，SHALL NOT 收斂成同一種
- [ ] 3.5 出現多於一個病患姓名或多份調劑日期時，設定 `multiple_bags_suspected=True`，仍回傳已辨識的項目
- [ ] 3.6 `tests/unit/services/medication/test_prescription_ocr_service.py`（新增）：以建構子注入 mock gemini_service，涵蓋 3.3～3.5 各條，含「模型回傳空藥品清單 → `not_prescription`」與「模型呼叫逾時 → `service_unavailable`」

## 4. 草稿 repository

- [ ] 4.1 新增 `app/repositories/prescription_draft_repository.py`：`ensure_indexes`（`draft_id` 唯一 + `expires_at` TTL）、`create`、`find_by_id_for_user`、`mark_committed`，沿用既有慣例的 `collection: Optional[Any] = None` 參數
- [ ] 4.2 `find_by_id_for_user` 以 `draft_id` 與 `creator_user_id` 同時過濾，查無時回 `None`（不區分「不存在」與「不屬於你」）
- [ ] 4.3 `mark_committed` 以「`committed_at` 仍為空」為條件做原子更新，取得提交權；未取得者回傳既有的 `committed_medication_ids`
- [ ] 4.4 `tests/unit/repositories/test_prescription_draft_repository.py`（新增）：以 `collection=` 傳入 mock，驗證 TTL 索引參數、`find_by_id_for_user` 的 filter 同時含兩個鍵、`mark_committed` 的條件式更新 filter

## 5. 藥品 repository 與 reminder 關聯

- [ ] 5.1 `app/repositories/medication_repository.py` 新增藥品的 `create_many`／`find_by_ids`／`find_active_by_ids(today)`／`set_enabled`
- [ ] 5.2 新增 `link_medications_to_reminder(reminder_id, medication_ids)`：以 `$addToSet` 附加，重複關聯不產生重複元素
- [ ] 5.3 `find_active_by_ids` 過濾 `enabled=False` 與不在 `start_date`～`end_date` 區間內的藥品
- [ ] 5.4 `tests/unit/repositories/test_medication_repository.py` 補 5.1～5.3 的 filter 與更新運算子驗證

## 6. 掃描協調服務

- [ ] 6.1 新增 `app/services/medication/prescription_scan_service.py` 的 `PrescriptionScanService`，建構子注入 `ocr_service`／`catalog_service`／`draft_repository`／`medication_repository`／`family_tree_repository`／`ttl_minutes`
- [ ] 6.2 `scan(image_bytes, mime_type, user_id)`：辨識 → 逐筆藥證庫校驗補齊 → 依「所有藥名皆通過校驗且用藥對象與頻次皆非空」判定 `high`，否則 `medium` → 建立草稿
- [ ] 6.3 用藥對象建議：以辨識出的病患姓名比對操作者族譜成員姓名，命中則帶為預設值；比對不到則留空。**建議值不寫入任何提醒**
- [ ] 6.4 `commit(draft_id, user_id, payload)`：驗證草稿屬於該使用者、未過期（過期回 410）、指定的用藥對象在族譜內（否則拒絕）
- [ ] 6.5 頻次映射：`QD`→`morning`；`BID`→`morning`,`evening`；`TID`→`morning`,`noon`,`evening`；`QID`→ 四段；`HS`→`bedtime`；`OTHER`→ 不自動映射且未指定時段時拒絕提交
- [ ] 6.6 `PRN` 藥品建立 `Medication` 但 SHALL NOT 出現在任何 `medication_ids` 中
- [ ] 6.7 提交時若對應時段的 reminder 不存在則一併建立（沿用既有 `medication_service` 的建立路徑與族譜檢查），已存在則只做關聯
- [ ] 6.8 冪等：以 `mark_committed` 取得提交權；未取得者直接回傳既有結果，SHALL NOT 重複建立
- [ ] 6.9 `tests/unit/services/medication/test_prescription_scan_service.py`（新增）：以建構子注入全部替身，涵蓋 6.2～6.8 各條，至少含「有藥名未命中 → `medium` 且無一鍵確認旗標」「PRN 不進 medication_ids」「TID 產生三個時段關聯」「重複 commit 只建立一次」「對象不在族譜 → 拒絕且無任何寫入」

## 7. API 端點與組裝

- [ ] 7.1 `app/routers/users/medications.py` 新增 `POST /prescription-scan`：multipart 影像；超過大小上限回 413、非影像 content type 回 415；功能開關關閉時回 404
- [ ] 7.2 新增 `GET /prescription-drafts/{draft_id}` 與 `POST /prescription-drafts/{draft_id}/commit`
- [ ] 7.3 辨識失敗的三種原因分別映射為可區分的 HTTP 回應與 `reason` 欄位，SHALL NOT 合併成同一則錯誤
- [ ] 7.4 `GET /api/users/medications`（既有）的回應新增 `medications` 欄位
- [ ] 7.5 `app/dependencies.py` 組裝三個新服務與新 repository；`DrugCatalogService.load_from_path` 失敗時記錄錯誤並以空清單建構，SHALL NOT 讓應用啟動失敗
- [ ] 7.6 `app/db/mongodb.py` 的索引建立流程納入 `prescription_drafts` 的 TTL 索引
- [ ] 7.7 `tests/unit/routers/test_medications_router.py` 補：以 `app.dependency_overrides` 注入替身，涵蓋 7.1～7.4 各條，含「功能開關關閉 → 404」「他人的 draft_id → 404」「過期草稿 commit → 410」

## 8. 推播文案的藥品區塊

- [ ] 8.1 `app/services/line_messaging/flex/medication_flex.py` 的 `build_patient_medication_flex` 與 `build_patient_urgent_reminder_flex` 新增可選的藥品名稱清單參數，為空時版面與現況完全相同
- [ ] 8.2 藥品清單設顯示上限，超出收斂為單行計數
- [ ] 8.3 `build_caregiver_alert_flex` 與 `build_caregiver_missed_summary_flex` **不加**藥品清單，維持既有措辭
- [ ] 8.4 `app/services/medication/medication_scheduler.py` 於組裝推播文案時（且僅於此時）以 `find_active_by_ids` 解析藥品名稱；展開與搶佔路徑不讀 `medication_ids`
- [ ] 8.5 `tests/unit/services/line_messaging/test_medication_flex.py` 補：空清單時的 flex 與現況一致（快照或結構比對）、有藥品時含名稱、超過上限時收斂、家屬卡片不含藥品名稱
- [ ] 8.6 `tests/unit/services/test_medication_scheduler.py` 補：`medication_ids` 為空的既有規則展開與推播行為不變；藥品失效時不出現在清單中

## 9. CARE-LIFF

- [ ] 9.1 `src/api/medicationApi.ts` 新增三個端點的 client 與型別
- [ ] 9.2 新增 `src/pages/Medications/PrescriptionScanDialog.tsx`：`<input type="file" accept="image/*" capture="environment">` 取像、上傳、載入狀態、三種失敗原因各自的引導文案與「改為手動建立」按鈕
- [ ] 9.3 新增 `src/pages/Medications/PrescriptionDraftForm.tsx`：逐筆呈現藥品、對照顯示 `usage_raw`、可編輯藥名與時段對應、用藥對象選擇、`PRN` 藥品明示不會定時提醒
- [ ] 9.4 信心度分級控制：`high` 顯示一鍵確認；`medium` 隱藏一鍵確認並標示需補齊的欄位；未通過藥證庫校驗的藥名以視覺標記突顯
- [ ] 9.5 草稿畫面顯示「此結果由自動辨識產生，請對照藥袋確認」
- [ ] 9.6 `src/pages/Medications/index.tsx` 加入掃描入口，並依功能開關決定是否顯示
- [ ] 9.7 `src/i18n/` 新增全部文案，沿用既有多語與字級慣例
- [ ] 9.8 `CARE-LIFF/src/tests/medications.test.tsx` 補：`medium` 時無一鍵確認、`PRN` 顯示不提醒說明、三種失敗原因各自的文案、上傳過大檔案的錯誤呈現

## 10. 收尾

- [ ] 10.1 執行 `./init.sh`，`pytest` 全綠
- [ ] 10.2 `CARE-LIFF` 執行 `npm run test` 全綠
- [ ] 10.3 `openspec validate prescription-bag-scan --strict` 通過
- [ ] 10.4 以實際藥袋樣本量測，回填 `design.md` 中三個 Open Question（相似度門檻、藥品顯示上限、`QD` 預設時段）
- [ ] 10.5 開啟 `PRESCRIPTION_SCAN_ENABLED`
