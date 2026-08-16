> 測試一律以依賴注入傳入替身：service 用建構子參數、repository 用 `collection=` 參數、handler 用建構子參數。禁止 `unittest.mock.patch` 修改全域或別處導入的實例。
> 先寫測試再寫實作：每個 `X.Y` 實作任務都有對應的測試任務，測試先紅再綠。
> 完成定義：`./init.sh` 全綠且有清楚的 git commit。
> `SAFETY_ALERT_ENABLED` 在第 6 節之前一律維持 `false`，讓每一節都能獨立合併而不改變線上行為。
> `app/services/media/mutimedia_processor.py` 與 `CARE-n8n` 全程 SHALL NOT 被修改；其既有測試須維持全綠。

## 1. 設定與資料模型

- [x] 1.1 `app/core/config.py` 新增 `SAFETY_ALERT_ENABLED`（預設 `false`）、`SAFETY_ALERT_DEDUPE_HOURS`（預設 24）、`SAFETY_ALERT_TIMEOUT_SECONDS`（預設 20），並同步 `.env.example`
- [x] 1.2 新增 `app/models/safety.py`：`RiskLevel`（`none`／`low`／`high`）、`AcquisitionChannel`（`medical_institution`／`licensed_pharmacy`／`overseas_personal`／`online_marketplace`／`acquaintance`／`tv_shopping`／`unknown`）
- [x] 1.3 `app/models/safety.py` 新增 `DrugMention`（`raw_name` 必填／`source_text`／`channel` 預設 `unknown`／`dispensed_package_markers` 預設空陣列／`catalog_hit` 預設 `False`／`license_number`）
- [x] 1.4 `app/models/safety.py` 新增 `SafetyAlertRecord`（`user_id`／`drug_key`／`risk_level`／`notified_at`／`expires_at`）
- [x] 1.5 `app/db/mongodb.py` 新增 `get_safety_alerts_collection()`
- [x] 1.6 `tests/unit/models/test_safety_models.py`（新增）：列舉合法值；`DrugMention` 除 `raw_name` 外皆可為空；`channel` 未給時為 `unknown`

## 2. 判定規則（純函式，無 I/O）

- [x] 2.1 新增 `app/services/safety/risk_rules.py`，模組內 SHALL NOT 有任何 I/O、類別或模組層級狀態
- [x] 2.2 實作 `detect_foreign_scripts(text: str) -> list[str]`：以 Unicode 區間偵測日文假名（`぀-ゟ`、`゠-ヿ`）、韓文（`가-힯`）、泰文（`฀-๿`）。拉丁字母 SHALL NOT 列入
- [x] 2.3 實作 `assess(mention: DrugMention, foreign_scripts: list[str]) -> RiskLevel`，依 spec「風險判定為藥證庫與取得訊號的複合結果」的五條規則
- [x] 2.4 實作合法調劑包裝規則：`dispensed_package_markers` 齊備時 `channel` 視為 `medical_institution`，且 `low` 的項目不送訊息。SHALL NOT 影響 `high` 的判定
- [x] 2.5 實作 `looks_drug_related(text: str, catalog: DrugCatalogService) -> bool`：小型關鍵詞集合加上藥證庫 n-gram 候選命中。藥證庫**以參數傳入**，SHALL NOT 於模組載入時讀檔
- [x] 2.6 實作 `normalize_drug_key(name: str) -> str`，重用 `DrugCatalogService` 既有的正規化，SHALL NOT 另寫一套
- [x] 2.7 `tests/unit/services/safety/test_risk_rules.py`（新增）：`detect_foreign_scripts` 的表格測試，含「含平假名 → `ja`」「含片假名 → `ja`」「`LIPITOR 10mg` → 空」「純中文 → 空」「含韓文 → `ko`」
- [x] 2.8 `tests/unit/services/safety/test_risk_rules.py` 以 table-driven 窮舉 2.3 的五條規則，**必含指標案例**「合利他命強効錠 EX PLUS + 藥證庫命中 + 日文字符集 → `high`」「藥證庫命中 + 無訊號 → `none`」「藥證庫未命中 + 無訊號 → `low`」
- [x] 2.9 `tests/unit/services/safety/test_risk_rules.py` 補 2.4：「藥袋 OCR + 其中一個藥名未命中 → 不送訊息」「調劑包裝訊號 + 日文字符集 → 仍為 `high`」
- [x] 2.10 `tests/unit/services/safety/test_risk_rules.py` 補 2.5 的擋下案例（日常問候、天氣、行程）與命中案例（含藥名、含「代購」「保健食品」）

## 3. 抽取服務

- [x] 3.1 新增 `app/services/safety/drug_mention_extractor.py` 的 `DrugMentionExtractor`，建構子注入 `gemini_service` 與逾時設定
- [x] 3.2 定義輸出 schema：`mentions[]`，每筆含 `raw_name`／`source_text`／`channel`／`dispensed_package_markers`。schema **SHALL NOT 含任何風險或安全性欄位**
- [x] 3.3 定義提示詞：輸入可能是使用者打的字，也可能是圖片的 OCR 全文；只記錄文字中實際出現的內容、不判斷安全性、缺漏留空不推測、`raw_name` 保留原文
- [x] 3.4 實作 `extract(text) -> list[DrugMention]`
- [x] 3.5 `raw_name` 為空的項目一律丟棄；`channel` 為列舉外的值時落回 `unknown`，SHALL NOT 讓整次抽取失敗
- [x] 3.6 逾時與所有 Gemini 例外一律轉為回傳空清單並記 log，SHALL NOT 往外拋。log SHALL NOT 含輸入文字內容
- [x] 3.7 `tests/unit/services/safety/test_drug_mention_extractor.py`（新增）：以建構子注入 mock gemini_service，涵蓋 3.4～3.6，含「非列舉 channel → `unknown`」「逾時 → 空清單且不拋例外」「無名稱項目被丟棄」「以藥袋 OCR 文字為輸入時抽出 `dispensed_package_markers`」

## 4. 節流 repository

- [x] 4.1 新增 `app/repositories/safety_alert_repository.py`：`ensure_indexes`（`(user_id, drug_key)` 唯一 + `expires_at` TTL `expireAfterSeconds=0`），沿用既有慣例的 `collection: Optional[Any] = None` 參數
- [x] 4.2 實作 `try_claim(user_id, drug_key, risk_level, ttl_hours) -> bool`：以 `insert_one` 取得通報權，`DuplicateKeyError` 回 `False`。SHALL NOT 以「先查再寫」實作
- [x] 4.3 `app/db/mongodb.py` 的啟動索引流程納入 `safety_alerts` 的兩個索引
- [x] 4.4 `tests/unit/repositories/test_safety_alert_repository.py`（新增）：以 `collection=` 傳入 mock，驗證兩個索引的參數、`try_claim` 走的是 `insert_one`、`DuplicateKeyError` 時回 `False` 且不拋例外

## 5. 通報協調服務與文案

- [x] 5.1 新增 `app/services/safety/safety_alert_service.py` 的 `SafetyAlertService`，建構子注入 `extractor`／`catalog_service`／`alert_repository`／`family_tree_repository`／`replier`／`user_profile_service`／`dedupe_hours`
- [x] 5.2 實作 `check(user_id, text)`：`looks_drug_related` 前置篩選 → `detect_foreign_scripts` → 抽取 → 逐筆 `catalog_service.match()` 補 `catalog_hit`／`license_number` → `assess()` → 分流
- [x] 5.3 `none` 不送任何訊息；`low` 只 push 當事人；`high` 先 `try_claim`，取得通報權才 push 族譜成員並 push 當事人（含「已告知家人」）
- [x] 5.4 `high` 未取得通報權時 SHALL NOT 推播族譜成員，亦 SHALL NOT 推播當事人
- [x] 5.5 族譜為 `None` 或成員為空時，`high` 仍 push 當事人，SHALL NOT 拋例外
- [x] 5.6 全流程以 `try/except Exception` 包覆，任何例外記 log 後靜默結束，SHALL NOT 往外拋。log SHALL NOT 含輸入文字或抽取到的姓名與機構
- [x] 5.7 SHALL NOT 呼叫任何 medication 相關的 repository 或 service（不建檔）；SHALL NOT 接觸任何影像
- [x] 5.8 新增 `app/services/line_messaging/flex/safety_flex.py` 的 `build_family_alert_flex(patient_name, drug_name, risk_reason, language, font_size)`；內容 SHALL 只含姓名、藥名與風險類型說明
- [x] 5.9 `app/i18n/messages.py` 新增全部文案：`low` 給當事人、`high` 給當事人（含「已請家人一起看看」）、`high` 給家人的卡片標題與說明；沿用既有多語與字級慣例。給當事人的訊息一律純文字，SHALL NOT 輸出 Markdown（見 `openspec/specs/line-reply-rules/spec.md`）
- [x] 5.10 `tests/unit/services/safety/test_safety_alert_service.py`（新增）：以建構子注入全部替身，涵蓋 5.2～5.7，至少含「`none` 零推播」「`low` 只推當事人」「`high` 推全部族譜成員 + 當事人」「`try_claim` 回 `False` 時零推播」「無族譜時仍推當事人」「抽取拋例外時不拋出且零推播」「前置篩選未通過時零呼叫抽取」「全程未觸及 medication repository」
- [x] 5.11 `tests/unit/services/line_messaging/test_safety_flex.py`（新增）：卡片含藥名與姓名、**不含**傳入的原始文字、字級與語言參數生效

## 6. Handler 接入與組裝

- [x] 6.1 `app/dependencies.py` 組裝 `DrugMentionExtractor`、`SafetyAlertService` 與 `SafetyAlertRepository`；重用既有的 `DrugCatalogService` 實例，SHALL NOT 重新載入藥證庫
- [x] 6.2 `app/services/line_messaging/handler/message_handler.py` 的 `BaseLineMessageHandler` 新增可選的 `safety_alert_service` 建構子參數，預設 `None`
- [x] 6.3 於 `_process_and_reply()` 內，主回覆流程之外以 `asyncio.create_task` 併行呼叫 `check(user_id, user_text)`。此處同時涵蓋文字與圖片兩種輸入
- [x] 6.4 任務參考 SHALL 被持有至完成（保留任務集合並於完成時移除），例外 SHALL 於任務內捕捉
- [x] 6.5 `SAFETY_ALERT_ENABLED` 為 `false` 或 `safety_alert_service is None` 時 SHALL NOT 建立該任務
- [x] 6.6 `app/services/line_messaging/handler/media_handler.py` 的 `LineMediaHandler.__init__` 把 `safety_alert_service` 往 `super().__init__()` 傳。**SHALL NOT 有任何邏輯變更**
- [x] 6.7 既有管線回傳錯誤字串時（`media_handler` 會拋 `LineValidationError`），SHALL NOT 進入風險評估
- [x] 6.8 `tests/unit/services/line_messaging/test_message_handler.py` 補：以建構子注入 fake service，涵蓋「開關關閉時零呼叫」「開啟時主回覆內容與時序不變」「service 拋例外時主回覆仍送出」
- [x] 6.9 `tests/unit/services/line_messaging/test_media_handler.py` 補：「圖片的 OCR 文字有進入 `check()`」「`media_processor_service` 的呼叫參數與次數與變更前完全相同」「既有管線回錯誤字串時零呼叫 `check()`」「檔案的既有 ingest 行為不變」
- [ ] 6.10（待部署後）開啟 `SAFETY_ALERT_ENABLED`，觀察誤報率與 `looks_drug_related` 的命中率

## 7. 收尾

- [x] 7.1 執行 `./init.sh`，`pytest` 全綠
- [x] 7.2 `openspec validate drug-safety-alert --strict` 通過
- [x] 7.3 確認 `CARE-n8n` 與 `CARE-LIFF` 零改動、`mutimedia_processor.py` 零改動、其既有測試全綠、藥袋掃描三支端點的測試全綠
- [ ] 7.4（待 6.10 的數據）依 6.10 的數據回填 `design.md` 的四個 Open Question（歐美代購的訊號、`low` 這一格是否過於保守、`SAFETY_ALERT_DEDUPE_HOURS` 的值、OCR 前綴是否需要剝除）
