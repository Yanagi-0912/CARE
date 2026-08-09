## Why

CARE 的「用藥提醒」目前提醒的是一個**時段**，不是一種**藥**。

`app/models/medication.py:57` 的 `MedicationReminder` 欄位只有 `creator_user_id`、`user_id`、`slot_type`、`scheduled_time`、`start_date`、`end_date`、`enabled`——沒有任何藥品欄位。前端 `CARE-LIFF/src/pages/Medications/ReminderFormDialog.tsx` 對應地也只讓使用者勾早／中／晚／睡前與起訖日期，且 `existingSlots` 會把已設定的時段 disable，等於「一位使用者、一個時段」只能有一筆規則。

於是有三個實質問題：

1. **推播內容是空的**：`build_patient_medication_flex`（`app/services/line_messaging/flex/medication_flex.py:103`）能告訴長輩「早上到了該吃藥」，但說不出「該吃哪一種、吃幾顆」。長輩手上通常同時有三到五種藥，這則提醒沒有回答他真正的問題。
2. **表達不了真實處方**：一張藥袋常見「A 藥 TID、B 藥 BID」。現有模型無法表達「早上吃 A+B、中午只吃 A」，也無法在療程結束時單獨停掉其中一種藥——只能整個時段一起關掉。
3. **建檔門檻高到不會有人用**：使用者必須自己看懂藥袋、換算「TID PC」是什麼意思、再回到 LIFF 逐一勾選。實際上長輩不會做，家屬也懶得做。功能存在但沒有入口成本夠低的建檔路徑。

同時，藥袋是台灣醫療環境裡**結構最穩定的資料來源**。衛署藥字第0910033863號公告要求藥品調劑包裝必須標示病患姓名、性別、藥品商品名、單位含量與數量、用法與用量、調劑地點與調劑者、調劑日期與警語，健保署另要求一袋一藥。也就是說「吃什麼」與「怎麼吃」兩件事都是法定必載欄位，而且是印刷體文字——辨識難度遠低於辨識藥丸本體上的刻痕。這讓「拍一張藥袋就把整個療程建好」成為可行的路徑，而不是一個願望。

食藥署沒有提供影像辨識 API，但提供了可用來校驗的開放資料：藥品外觀資料集（`data.fda.gov.tw/data/opendata/export/42/json`，實測 6,269 筆、其中 6,247 筆附官方外觀圖）與全部藥品許可證資料集（`data.gov.tw/dataset/9122`）。辨識出來的藥名若在藥證庫比對不到近似項，就是錯讀的強訊號——這是本 change 唯一能對抗「模型自己很有信心但讀錯字」的手段，也是把 OCR 結果寫進用藥提醒之前的必要關卡。

## What Changes

- **新增藥品實體（BREAKING：僅資料模型擴充，無既有行為變更）**：新增 `medications` collection 與 `Medication` 模型（藥品名稱、許可證字號、單位含量、總數量、頻次代碼、用法原文、適應症、來源、療程起訖、`enabled`）。`MedicationReminder` 新增 `medication_ids: list[str]`，預設空陣列——既有規則讀回後為空，行為與現在完全一致。
- **新增藥袋辨識服務**：`PrescriptionOcrService` 以 Gemini vision 對藥袋影像做 schema 約束的結構化抽取，輸出機構、病患姓名、調劑日期與逐筆藥品；每個欄位帶信心度。**不接傳統 OCR**，理由見 `design.md`。
- **新增藥證庫比對**：`DrugCatalogService` 以離線建置的查表對辨識出的藥品名做模糊比對，命中則補上許可證字號與成分並提高該筆信心度，未命中則標記為低信心。伴隨一支建表腳本 `scripts/build_drug_catalog.py`。
- **新增草稿與確認流程**：辨識結果先存為草稿（`prescription_drafts` collection，TTL 自動過期），使用者在 LIFF 逐欄核對、修正、指定用藥對象後才提交；提交時才寫入 `medications` 與 `medication_reminders`。**未經確認的辨識結果 SHALL NOT 產生任何提醒規則。**
- **PRN 不建立定時提醒**：頻次辨識為「需要時服用」的藥品 SHALL 建立藥品資料但 SHALL NOT 關聯到任何時段規則，且介面 SHALL 明示「這種藥不會定時提醒」。
- **推播帶出藥品清單**：`build_patient_medication_flex` 與二次催促、家屬逾時警報的文案新增「本次應服」區塊，列出該時段關聯的藥品名稱。無關聯藥品時維持現有版面。
- **入口不經 LINE 訊息路徑**：影像由 LIFF 直接上傳至新的 `POST /api/users/medications/prescription-scan`。`media_handler`、`mutimedia_processor` 與 `dispatcher` 的圖片分支**完全不動**，n8n 的 `MEDIA_PARSE_WEBHOOK_URL` 路徑不受影響。取捨與被否決的兩個替代方案見 `design.md`。
- **LIFF 新增掃描與草稿編輯頁**：`CARE-LIFF` 的 Medications 頁新增「掃描藥袋」入口，拍照／選圖後在同頁呈現辨識結果、逐欄編輯、時段對應與提交。Rich Menu **不動**——六格已全數指派（`openspec/specs/rich-menu/spec.md:21`），新增入口會需要換掉既有格位與重製各語系 PNG，不在本 change 範圍。

## Capabilities

### New Capabilities

- `medication-identification`：藥袋影像的結構化辨識、藥證庫校驗、信心度分級、草稿生命週期與確認閘門、PRN 處理、影像不留存

### Modified Capabilities

- `medication-reminders`：提醒規則關聯藥品清單；推播文案帶出該時段應服藥品；藥品層級的停用與療程結束獨立於時段規則；家屬端推播不得顯示適應症

（`backend-architecture` 不列入：新增 `app/services/medication/` 子模組與兩個 repository 只是依循既有的「分層與放置」「依賴注入與組裝點」「測試目錄對齊」條文，沒有任何需求文字改變。）

## Impact

- **CARE**：`app/models/medication.py`、`app/models/prescription.py`（新增）、`app/repositories/medication_repository.py`、`app/repositories/prescription_draft_repository.py`（新增）、`app/services/medication/prescription_ocr_service.py`（新增）、`app/services/medication/drug_catalog_service.py`（新增）、`app/services/medication/medication_service.py`、`app/services/line_messaging/flex/medication_flex.py`、`app/routers/users/medications.py`、`app/db/mongodb.py`、`app/core/config.py`、`app/dependencies.py`、`scripts/build_drug_catalog.py`（新增）、`resources/drug_catalog.json`（新增產出物）、`.env.example`
- **CARE-LIFF**：`src/api/medicationApi.ts`、`src/pages/Medications/index.tsx`、`src/pages/Medications/PrescriptionScanDialog.tsx`（新增）、`src/pages/Medications/PrescriptionDraftForm.tsx`（新增）、`src/i18n/`（新增文案）、`src/types/`
- **API**：新增 `POST /api/users/medications/prescription-scan`（multipart 影像上傳，回傳草稿）、`GET /api/users/medications/prescription-drafts/{draft_id}`、`POST /api/users/medications/prescription-drafts/{draft_id}/commit`；`GET /api/users/medications`（既有）的回應新增 `medications` 欄位
- **測試**：`tests/unit/models/test_medication_models.py`、`tests/unit/models/test_prescription_models.py`（新增）、`tests/unit/repositories/test_medication_repository.py`、`tests/unit/repositories/test_prescription_draft_repository.py`（新增）、`tests/unit/services/medication/test_prescription_ocr_service.py`（新增）、`tests/unit/services/medication/test_drug_catalog_service.py`（新增）、`tests/unit/services/test_medication_service.py`、`tests/unit/services/line_messaging/test_medication_flex.py`、`tests/unit/routers/test_medications_router.py`、`CARE-LIFF/src/tests/medications.test.tsx`
- **設定**：新增 `PRESCRIPTION_SCAN_ENABLED`（預設 `false`，功能開關）、`PRESCRIPTION_SCAN_MAX_IMAGE_BYTES`（預設 8388608）、`PRESCRIPTION_DRAFT_TTL_MINUTES`（預設 60）、`DRUG_CATALOG_PATH`（預設 `resources/drug_catalog.json`）
- **相依**：`GEMINI_API_KEY` 已存在（`app/dependencies.py:71`），沿用既有 `GeminiService`，不新增外部服務。藥證庫為建置期產出的靜態檔，執行期不對外連線
- **不受影響**：`CARE-n8n`（本 change 不觸及 `MEDIA_PARSE_WEBHOOK_URL` 路徑）、`medication_scheduler`（排程器只讀 `slot_type` 與 `scheduled_time`，本 change 不改變其輸入）、`rich-menu`
- **行為變更**：既有提醒的 `medication_ids` 為空，推播版面與現在完全相同。新建的提醒若關聯藥品，推播會多出「本次應服」區塊
- **隱私**：藥袋含姓名、就診機構與適應症。上傳影像 SHALL NOT 落地保存或寫入資料庫，辨識完成即釋放；草稿以 TTL 自動清除；家屬端推播 SHALL NOT 顯示適應症
- **刻意不做**：藥丸本體影像辨識（覆蓋率與圖片品質不足以支撐，見 `design.md` 的替代方案評估）；LINE 聊天室直接傳藥袋照片的自動偵測分流（待本 change 累積辨識準確率數據後另案評估）；Rich Menu 格位調整
