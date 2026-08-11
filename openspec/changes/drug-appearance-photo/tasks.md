> 測試一律以依賴注入傳入替身：router 用 `app.dependency_overrides`、service 用建構子參數、repository 用 `collection=` 參數。禁止 `unittest.mock.patch` 修改全域或別處導入的實例。
> 先寫測試再寫實作。
> 第 1～3 節不改變任何使用者可見行為，可獨立合併。
> 這個工作樹與其他人共用；一律只 stage 明確路徑，禁止 `git add -A`／`git add .`／`git commit -a`，每次 commit 後以 `git show --stat` 確認。
> 後端驗證須在乾淨 worktree 執行（工作樹偵測不到「commit 進了別人的檔案」這類錯誤）。

## 1. 藥證庫索引：唯一才給證號

- [ ] 1.1 `DrugCatalogService.__init__` 的 `_by_key` 由「鍵 → 單一條目」改為「鍵 → 條目集合」，移除 `setdefault` 造成的靜默併除
- [ ] 1.2 `DrugCatalogMatch` 新增 `candidates: list[DrugCatalogEntry]`；`license_number` 僅在候選唯一時有值
- [ ] 1.3 `match()` 三階段（完全／含容／相似度）皆改為先取候選集合再判定唯一性；**完全比對命中碰撞鍵時 `license_number` SHALL 為空**
- [ ] 1.4 `tests/unit/services/medication/test_drug_catalog_service.py` 補：唯一鍵回證號；碰撞鍵回候選且證號留空；完全比對命中碰撞鍵仍留空（這是先前錯配的主因，必須有專門測試）；碰撞的兩張藥證都出現在候選中（先前第二張永遠比不到）
- [ ] 1.5 確認 `_verify_against_catalog` 不需修改：信心度只依 `match() is not None`，與 `license_number` 無關。補一個測試釘住「多候選仍為高信心」

## 2. 建表腳本納入外觀欄位

- [ ] 2.1 `scripts/build_drug_catalog.py` 的 `build_entries` 從外觀資料集額外取 `外觀圖檔連結`、`形狀`、`顏色`、`刻痕`、`標註一`、`標註二`、`外觀尺寸`；無外觀記錄者留空
- [ ] 2.2 `DrugCatalogEntry` 新增對應欄位，`load_from_path` 一併載入
- [ ] 2.3 `tests/unit/scripts/test_build_drug_catalog.py` 補：外觀欄位正確映射、缺欄位時留空、外觀資料集只補許可證資料集沒有的品項（既有規則不得改變）
- [ ] 2.4 重新產出 `resources/drug_catalog.json` 並確認 `tests/unit/resources/test_drug_catalog_artifact.py` 仍綠（欄位擴充不得破壞既有守門）

## 3. 縮圖抓取與落地

- [ ] 3.1 `scripts/build_drug_catalog.py` 新增獨立旗標（例如 `--fetch-images`）控制是否抓圖；**預設不抓**——單次完整抓取是 5,727 次請求、15.4 GB
- [ ] 3.2 抓圖必須設定 User-Agent：`mcp.fda.gov.tw` 對 Python 預設 UA 回 403，而資料集主機不擋，很容易誤判成「圖掛了」
- [ ] 3.3 請求間限速；已存在的縮圖預設跳過；可中斷後續跑
- [ ] 3.4 縮圖規格 160px 長邊、JPEG q80（實測平均 3.0 KB，5,727 張約 17 MB）；檔名為證號的 SHA-256 前 16 字元
- [ ] 3.5 產出縮圖並提交；新增 `tests/unit/resources/test_drug_appearance_images.py` 守門：目錄存在、數量在合理範圍、抽樣檔案可被解碼為影像、檔名與藥證庫中的證號雜湊對得起來
- [ ] 3.6 README 補「藥品外觀縮圖」一節，比照既有「藥證庫」寫法：一般開發不需要做任何事，只有要跟上資料更新時才重跑，並寫明 15.4 GB 與限速的理由

## 4. 靜態資源服務

- [ ] 4.1 新增縮圖的靜態服務路徑（比照既有 TTS 音檔以 `PUBLIC_BASE_URL` 對外的作法）
- [ ] 4.2 `app/core/config.py` 新增縮圖目錄與對外路徑設定，同步 `.env.example`
- [ ] 4.3 由證號解析出對外 URL 的純函式；證號無對應縮圖時回 `None`
- [ ] 4.4 對應單元測試：有縮圖回 URL、無縮圖回 `None`、URL 不含連續或可預測的識別碼

## 5. 草稿與提交攜帶候選

- [ ] 5.1 `RecognizedDrug` 新增候選清單欄位；`_verify_against_catalog` 把 `match()` 的候選寫入
- [ ] 5.2 `CommitDrugItem` 接受使用者挑定的 `license_number`
- [ ] 5.3 提交時驗證帶回的證號在該筆候選清單內；不在清單內 SHALL 拒絕且 SHALL NOT 建立任何藥品或提醒
- [ ] 5.4 未挑選 SHALL NOT 阻擋提交；該筆以 `license_number` 為空建立
- [ ] 5.5 `Medication` 新增外觀欄位（形狀／顏色／刻痕／標註），建立時自藥證庫帶入
- [ ] 5.6 `tests/unit/services/medication/test_prescription_scan_service.py` 補 5.1～5.5，至少含「帶回候選外的證號 → 拒絕且無任何寫入」與「未挑選仍能提交」

## 6. 推播 Flex 的縮圖列

- [ ] 6.1 `_medication_list_block` 的每一列在該藥品證號已確定且有縮圖時改為「縮圖 + 藥名」，否則維持純文字列
- [ ] 6.2 `build_caregiver_alert_flex` 與 `build_caregiver_missed_summary_flex` **不加**縮圖
- [ ] 6.3 `medication_scheduler` 組裝文案時解析縮圖 URL；**沿用既有的批次快取，不得新增每筆 log 的額外查詢**，也不得改動展開／搶佔路徑
- [ ] 6.4 `tests/unit/services/line_messaging/test_medication_flex.py` 補：`medication_ids` 為空時版面與本變更前完全相同（比照既有快照測試，快照取自本 change 之前的 commit）；有縮圖時含圖；圖文混排不破版；家屬卡片不含縮圖

## 7. CARE-LIFF：消歧與外觀呈現

- [ ] 7.1 型別與 API client 補上候選清單、挑定證號、外觀欄位
- [ ] 7.2 核對畫面：候選多於一張時逐筆呈現候選的縮圖與外觀描述供挑選；挑選後釘定該筆證號
- [ ] 7.3 未挑選不得阻擋提交；介面 SHALL 說明未挑選的後果是「不會顯示藥丸照片」，而不是任何功能受限
- [ ] 7.4 藥品清單與提醒卡片依證號呈現照片與外觀描述；無照片時仍呈現文字描述
- [ ] 7.5 藥名被編輯時證號與照片一併失效（既有行為，補測試釘住）
- [ ] 7.6 文案全走 i18n，六語系同步
- [ ] 7.7 `CARE-LIFF/src/tests/prescriptionScan.test.tsx` 補 7.2～7.5

## 8. 收尾

- [ ] 8.1 後端在乾淨 worktree 執行 `import app.main` 與 `pytest -q` 全綠
- [ ] 8.2 `CARE-LIFF` `npm run test` 與 `npm run build` 全綠
- [ ] 8.3 `openspec validate drug-appearance-photo --strict` 通過
- [ ] 8.4 實際推一則帶縮圖的提醒到 LINE，確認渲染結果，回填 design.md 的 Open Question（縮圖長寬比）
- [ ] 8.5 以真實藥袋辨識結果觀察候選集合大小分布，回填 design.md 的 Open Question（候選呈現上限與排序準則）
