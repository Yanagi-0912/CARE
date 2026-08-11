## Why

藥袋掃描上線後，時段推播已經能列出「本次應服」的藥品**名稱**（`_medication_list_block`，`app/services/line_messaging/flex/medication_flex.py:90`）。但長輩手上是三到五顆藥丸，他要回答的問題是「該吃**哪一顆**」，而藥名回答不了這件事——沒有人是靠「Amlodipine Besylate」或「脈優錠」認藥的，認的是白色圓形那顆、還是紅白膠囊那顆。

**外觀是病人辨識用藥的實際錨點，這點有文獻支持。** Kesselheim 等人發現學名藥替換造成藥丸顏色或形狀改變後，病人停藥的機率顯著上升（心血管用藥：Ann Intern Med. 2014;161(2):96-103；抗癲癇藥：JAMA Intern Med. 2013;173(3):202-208）。病人把外觀當成藥品的身分本身，外觀一變就認不得、就不吃了。反過來說，在提醒裡補上正確的外觀，是補上他真正在用的那個識別特徵。

（證據強度要說清楚：「外觀是辨識錨點」有上述實證；「在提醒訊息裡放藥丸照片能提升服藥遵囑性」則沒有直接的 RCT 證據，是從前者推論的合理假設。本 change 的成敗判準因此不放在遵囑率，而放在覆蓋率與**錯誤率**——見下方。）

食藥署的**藥品外觀資料集**（`data.fda.gov.tw/data/opendata/export/42/json`）本來就在 `scripts/build_drug_catalog.py:33` 被下載，但目前只取品名欄位，`外觀圖檔連結`、`形狀`、`顏色`、`刻痕`、`標註一`、`標註二`、`外觀尺寸` 全部丟掉。實測（2026-08-11）：

| 項目 | 實測 |
| --- | --- |
| 外觀資料集筆數 | 6,295，其中 6,273 筆（99.7%）有 `外觀圖檔連結` |
| 圖檔可用性 | HTTP 200、`image/png`、約 300 KB、582×546 與 657×656 |
| 協定 | HTTPS 可直連（LINE 要求 HTTPS + TLS 1.2，已符合） |
| 覆蓋率：未註銷 + 錠／膠囊 | 5,727 / 10,741 = **53.3%** |
| 覆蓋率：再限處方藥 | 4,703 / 8,372 = **56.2%** |
| 外觀圖屬已註銷藥證者 | 29 筆 |

覆蓋率看起來只有一半，但分母已經排除針劑、藥膏、輸液這些本來就不需要「認藥丸」的劑型；對真正需要辨識的口服固體藥而言，官方照片涵蓋超過一半。

**真正的阻礙不是覆蓋率，是證號解析不出來。** 要查圖就得知道 `license_number`，而 `DrugCatalogService` 在含容比對命中多張藥證時**刻意回傳 `license_number=None`**（`app/services/medication/drug_catalog_service.py:74-86`）——「普拿疼」同時是好幾個普拿疼品項的子字串，選一個就是編造。取 400 個確定有官方照片的真實藥品、模擬藥袋只印品牌短名的情形，跑真正的 `match()`：

| 結果 | 比例 |
| --- | --- |
| 拿到正確證號 | 45.5% |
| 證號留空（含容多命中，設計如此） | 33.5% |
| **比到別張藥證** | **4.2%** |
| 完全比不到 | 16.8% |

端到端只有 `0.533 × 0.455 ≈ 24%` 的藥能拿到照片（此為下界：藥袋若印完整品名含劑量會走完全比對，證號是準的；印短名才掉到 45.5%）。而那 4.2% 是安全問題——「得胎隆膜衣錠10毫克」比到另一張「得胎隆」藥證，同品牌不同劑量或廠商，**藥丸長相就是不一樣**。長輩比對後的結論會是「這不是我的藥」而不吃，或兩顆藥之間拿錯。**貼錯照片比不貼照片危險得多**，所以本 change 的核心不是「把圖接上去」，而是**在無法確定是哪一張藥證時，一律不顯示照片**。

那 33.5% 不需要放棄：含容比對其實**已經找到候選集合了**，只是在回傳前把它丟掉。把候選交給核對畫面上那個手裡正握著藥袋的人挑一次，就從「機器猜不出來」變成「人看一眼就知道」——這與本專案既有的 PRN、`OTHER` 頻次、用藥對象的處理方式是同一套原則：不臆測，問使用者。

## What Changes

- **`DrugCatalogMatch` 改為可攜帶候選清單**：含容比對命中多張藥證時，不再只回一個 `license_number=None` 的空殼，而是附上候選藥證（證號、中文品名、外觀欄位）。`match()` 回傳非 `None` 仍然只代表「藥名已驗證為真實核准藥品」，**信心度的判定邏輯完全不變**——候選清單是新增的資訊，不是新的判定依據。
- **藥證庫納入外觀欄位**：`scripts/build_drug_catalog.py` 從外觀資料集額外取出 `外觀圖檔連結`、`形狀`、`顏色`、`刻痕`、`標註一`、`標註二`、`外觀尺寸`，寫入 `resources/drug_catalog.json`。無外觀記錄的藥證這些欄位留空。
- **照片在建置期落地成自有靜態資源**：建表腳本一併下載、縮圖、存成專案自有的靜態檔，**執行期不 hot-link `mcp.fda.gov.tw`**。LINE 是在推播渲染當下才去抓圖，直連政府主機等於把推播的可用性綁在政府站台上，且每則提醒都打一次外部主機。
- **核對畫面新增藥證消歧**：候選超過一張時，逐筆藥品顯示候選藥丸照片與外觀描述，讓使用者挑「藥袋裡的那一顆」。挑選後該筆的 `license_number` 才被釘定並隨草稿提交。**未挑選 SHALL NOT 阻擋提交**——照片是附加價值，不是建藥品的必要條件，缺照片只是沒照片。
- **`Medication` 呈現藥丸照片**：LIFF 藥品清單與提醒卡片依 `license_number` 顯示官方照片與外觀描述（形狀／顏色／刻痕／標註）。文字描述在照片載入失敗或缺席時仍然有用，兩者都要。
- **推播 Flex 帶出藥丸縮圖**：`_medication_list_block` 的每一列從純文字改為「縮圖 + 藥名」並排。**沒有照片的藥品維持純文字列，版面 SHALL NOT 因此破掉**；一個時段藥品數多時仍受既有的 `MEDICATION_LIST_MAX_ITEMS` 收斂。
- **藥名被編輯時一併清除照片**：核對畫面改藥名已經會清掉 `license_number`（`CARE-LIFF/src/pages/Medications/PrescriptionDraftForm.tsx:183`），照片依附於證號，因此自動一起消失。此行為要在 spec 中明文化，避免日後有人「順手」保留舊照片。

## Capabilities

### New Capabilities

- `drug-appearance`：藥品外觀資料（官方照片與形狀／顏色／刻痕／標註）的來源、建置期落地、藥證消歧規則、**證號無法確定時不得顯示照片**的安全邊界，以及照片缺席時的降級呈現

### Modified Capabilities

- `medication-identification`：藥證庫比對在多候選時回傳候選清單而非丟棄；核對畫面新增藥證消歧步驟且不得阻擋提交；藥名被編輯時證號與照片一併失效
- `medication-reminders`：時段推播的藥品清單區塊得帶出藥丸縮圖；無照片的藥品維持純文字且版面不變

（`backend-architecture` 不列入：本 change 只是在既有 `app/services/medication/` 下擴充，沒有需求文字變動。）

## Impact

- **CARE**：`app/services/medication/drug_catalog_service.py`（`DrugCatalogMatch` 結構、含容分支）、`app/services/medication/prescription_scan_service.py`（候選傳遞）、`app/models/prescription.py`、`app/models/medication.py`（外觀欄位）、`app/services/line_messaging/flex/medication_flex.py`（`_medication_list_block`）、`app/routers/users/medications.py`、`app/core/config.py`、`scripts/build_drug_catalog.py`、`resources/drug_catalog.json`（結構擴充）、新增靜態圖片資源目錄
- **CARE-LIFF**：`src/types/prescription.ts`、`src/types/medication.ts`、`src/pages/Medications/PrescriptionDraftForm.tsx`（消歧 UI）、`src/pages/Medications/index.tsx`、`src/api/medicationApi.ts`、`src/i18n/medicationMessages.ts`
- **API**：`POST /api/medications/prescription-scan` 的草稿回應中，每筆藥品新增候選藥證清單；`POST /api/medications/prescription-drafts/{draft_id}/commit` 的 `CommitDrugItem` 接受使用者挑定的 `license_number`；`GET /api/medications/reminders` 的藥品物件新增外觀欄位。**皆為欄位新增，無 breaking change**
- **測試**：`tests/unit/services/medication/test_drug_catalog_service.py`、`tests/unit/services/medication/test_prescription_scan_service.py`、`tests/unit/services/line_messaging/test_medication_flex.py`、`tests/unit/resources/test_drug_catalog_artifact.py`、`tests/unit/routers/test_medications_router.py`、`CARE-LIFF/src/tests/prescriptionScan.test.tsx`
- **設定**：新增靜態圖片資源的服務路徑設定；`DRUG_CATALOG_PATH` 沿用
- **建置產出物**：`resources/drug_catalog.json` 體積增加（新增外觀欄位）；新增約 5,700 張縮圖。需在 design 階段決定縮圖尺寸與是否納入 git（目前 `drug_catalog.json` 8.9 MB 是進 repo 的）
- **相依**：新增影像處理套件（縮圖用，建置期）。執行期仍不對外連線
- **不受影響**：`medication_scheduler`（排程輸入不變）、藥袋辨識的 Gemini 呼叫（prompt 與 schema 不動）、`CARE-n8n`、`rich-menu`
- **隱私**：藥丸照片洩漏的資訊不多於已在推播中的藥名，不觸及「適應症 SHALL NOT 出現在推播」的既有規則。但自有靜態圖片的 URL 會被 LINE 伺服器抓取並快取，**SHALL NOT 使用可枚舉的識別碼**，也不得讓 URL 反映使用者或藥品以外的資訊
- **刻意不做**：
  - **使用者自拍藥丸照片**。官方照片路徑有 ground truth——使用者是從藥名比對出的候選中挑選，選項本身受藥名約束；自拍路徑沒有任何第二來源可供校驗，照片可以是任何東西。而「拿錯顆」這種錯誤，遠端家人看手機照片也無從判斷（他不在現場、手上沒有藥袋），加一道家人確認只會產生「已驗證」的假權威，讓錯照片更危險。此外專案目前**完全沒有影像持久化**（無 GridFS／S3／StaticFiles，`app/models/` 無任何影像欄位），且藥袋掃描的 spec 明文要求上傳影像不落地；自拍照片會是第一張必須長期保存的使用者影像，需要儲存層、保留政策、刪藥連帶刪除與族譜範圍存取控制。另案評估。
  - **藥丸本體的影像辨識**（沿用 `2026-08-10-prescription-bag-scan` 的 design 決策）。
  - **已註銷藥證的過濾**（僅 29 筆有圖，且沿用既有「註銷不影響藥名為真」的判斷）。
