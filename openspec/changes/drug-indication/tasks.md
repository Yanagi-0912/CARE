## 1. 設定與資料格式

- [ ] 1.1 於 `app/core/config.py` 新增 `DRUG_INDICATION_PATH`（預設 `resources/drug_indications.json`）與 `DRUG_INDICATION_SUMMARY_MAX_CHARS`（暫定值，待 design 的 Open Question 回填），並同步 `.env.example`
- [ ] 1.2 定義 `drug_indications.json` 的結構（證號 → `{text, summary, summary_of}`）並寫成 `resources` 層測試：`tests/unit/resources/test_drug_indications_artifact.py` 斷言檔案可解析、鍵為證號、`summary_of` 與 `text` 的雜湊一致（比照既有的 `test_drug_catalog_artifact.py`）

## 2. 建表腳本：取用仿單適應症

- [ ] 2.1 `scripts/build_drug_catalog.py` 新增 `--fetch-indications` 旗標，預設不跑；未帶旗標時腳本行為與變更前完全相同
- [ ] 2.2 從已下載的許可證資料集取 `適應症`，以證號去重後寫入 `text`（不需 LLM，此步驟可獨立驗證）
- [ ] 2.3 實作「是否需要摘要」的機械判定（>40 字、含換行、含四個以上連續英文字母、含編號清單），並以固定樣本測試判定結果
- [ ] 2.4 實作以 `text` 的 sha256 前綴為 `summary_of` 的冪等跳過：原文未變即不重算（對應 spec「重跑建置」scenario）

## 3. 建表腳本：摘要生成

- [ ] 3.1 撰寫摘要 prompt，將 spec 的約束寫入（只做濃縮、不得新增或推論、不得遺漏任何一個適應症、不得改寫成療效保證或用藥建議）
- [ ] 3.2 接上既有的 `MODEL_NAME` 呼叫；金鑰缺席時 SHALL 明確報錯而非靜默產出空摘要
- [ ] 3.3 實作合格性檢查：不合格或呼叫失敗時 `summary` 留空，SHALL NOT 寫入不合格結果（對應 spec「摘要產生失敗」scenario）
- [ ] 3.4 以固定樣本（含多適應症、含英文名詞、含編號清單各一）人工檢視摘要輸出，回填 design 的 Open Question「摘要的長度上限」

## 4. DrugIndicationService

- [ ] 4.1 新增 `app/services/medication/drug_indication_service.py`：建構子接受已載入的條目（不接受路徑），比照 `DrugCatalogService` 讓測試可直接餵小型固定資料集
- [ ] 4.2 實作 `load_from_path`：檔案缺席或損毀時回傳空服務並記錄錯誤，SHALL NOT 讓應用啟動失敗（對應 design 的 Migration Plan）
- [ ] 4.3 實作 `lookup(license_number)`：回傳 `{text, summary}` 或 None
- [ ] 4.4 實作比對：排除停用字後取中文字 2-gram，零重疊判定為不相干；藥袋適應症為空或證號未確定時回傳「未比對」
- [ ] 4.5 於 `app/dependencies.py` 組裝並注入（唯一組裝點慣例）
- [ ] 4.6 測試 `tests/unit/services/medication/test_drug_indication_service.py`：涵蓋 spec 的「完全不相干」「藥袋沒有適應症」「證號未確定」三個 scenario，以及檔案缺席的降級。以 DI 餵固定資料集，不得 monkey patch

## 5. 接入辨識流程

- [ ] 5.1 `app/models/prescription.py` 的 `RecognizedDrug` 新增比對結果欄位（未比對／相符／不相干），預設為「未比對」
- [ ] 5.2 `PrescriptionScanService` 注入 `DrugIndicationService`，於藥證庫校驗之後填入比對結果
- [ ] 5.3 **確認 `scan()` 的信心度計算一字未動**：`all_names_verified`、`all_frequencies_known`、`confidence_level` 的運算式與變更前相同
- [ ] 5.4 測試 `tests/unit/services/medication/test_prescription_scan_service.py` 新增：比對結果為「不相干」時，草稿信心度仍為 high、名稱信心度不變（對應 spec「判定不相干仍維持高信心」「不改變名稱信心度」兩個 scenario）

## 6. 提交與讀取路徑

- [ ] 6.1 `app/models/medication.py` 的 `Medication` 新增仿單適應症欄位；比照 `thumbnail_url` 的慣例於讀取當下就地解析，不落地存進資料庫
- [ ] 6.2 `MedicationService.get_user_reminders_with_medications` 以 `license_number` 解析仿單適應症；證號未確定時為 None（對應 spec「多候選未挑定」scenario）
- [ ] 6.3 測試 `tests/unit/routers/test_medications_router.py`：`GET /api/medications/reminders` 回應含仿單適應症欄位；證號未確定的藥品該欄位為 None

## 7. 不得進入推播

- [ ] 7.1 確認 `app/services/line_messaging/flex/medication_flex.py` 的藥品清單區塊不含任何適應症欄位（既有行為，本任務是加測試把它釘住）
- [ ] 7.2 測試 `tests/unit/services/line_messaging/test_medication_flex.py`：帶有仿單適應症的藥品，推播內容不含該字串（對應 spec「時段推播」scenario）

## 8. LIFF 呈現

- [ ] 8.1 `CARE-LIFF/src/types/medication.ts` 新增仿單適應症欄位型別
- [ ] 8.2 新增適應症呈現元件：藥袋那行置主要位置，仿單置預設收合的次要區塊，兩者各自標示來源
- [ ] 8.3 摘要為空時顯示原文；證號未確定時整個仿單區塊不渲染（SHALL NOT 出現空白區塊）
- [ ] 8.4 `CARE-LIFF/src/i18n/medicationMessages.ts` 新增六語系文案（來源標示、展開／收合、原文標籤）
- [ ] 8.5 測試 `CARE-LIFF/src/tests/medications.test.tsx`：兩者皆有、只有藥袋有、摘要為空三種情形的渲染結果

## 9. 收尾

- [ ] 9.1 `./init.sh` 全綠（所有 pytest 通過）
- [ ] 9.2 `CARE-LIFF`：`npm run test` 與 `npm run build` 通過
- [ ] 9.3 回填 design 的 Open Questions（摘要長度上限、n-gram 大小與停用字表）；真實誤判率一項標明待真實藥袋資料，不在本 change 內解決
- [ ] 9.4 清楚的 git commit 與 PR
