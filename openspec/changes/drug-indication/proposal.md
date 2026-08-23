## Why

`Medication.indication` 目前**從來沒有被顯示過**。它由藥袋辨識讀出、存進資料庫、在提交 payload 裡傳遞，但整個 `CARE-LIFF` 沒有任何元件渲染它（全前端搜尋 `.indication` 只命中 `types/` 的型別宣告與 `PrescriptionDraftForm.tsx:207` 的資料映射）。`medication-identification` spec 那條「適應症 SHALL 僅對用藥者本人與其族譜成員於 LIFF 中可見」描述的是一道權限邊界，而這道邊界目前是空的——沒有東西可見。

同時，食藥署的權威適應症就在手邊卻沒被取用。`scripts/build_drug_catalog.py` 已經在下載全部藥品許可證資料集（dataset 9122），該資料集有 28 個欄位，建表腳本只取了 3 個（`許可證字號`、`中文品名`、`英文品名`）。腳本自己的 docstring 就記著這筆待辦：

> 同資料集另有「適應症」與「主成分略述」，可用來取代辨識結果中同名欄位（權威來源優於模型讀出的字串），值得後續評估。

本提案做了那次評估，結論是**不取代**，理由見下。

### 實測（2026-08-24，許可證資料集 72,013 列 / 66,459 個不重複證號）

**填答率不是問題：**

| 子集 | 有適應症 |
| --- | --- |
| 全部藥證 66,459 | 99.9% |
| 未註銷 22,535 | 100.0% |
| 未註銷 + 口服固體 10,722 | 100.0% |
| 有藥丸照片那批 6,266（證號確定時實際查得到的） | 100.0% |

**內容才是問題。** 長度中位數 28 字、平均 43，但尾巴很長：90% 分位 83 字、99% 分位 301 字、最長 3,305 字。在「有藥丸照片」那 6,266 筆中：夾雜英文藥學名詞 16.9%、超過 100 字 10.7%、多行 5.3%、含編號清單 4.8%。

```
好的：  緩解便祕。
        神經痛、關節痛、腰酸背痛、牙痛、頭痛、月經痛
壞的：  本品應與內皮素受體拮抗劑(endothelin receptor antagonist, ERA)及/或
        第五型磷酸二酯酶(phosphodiesterase type 5, PDE 5)抑制劑合併使用…（188 字）
        好氧性革蘭氏陽性菌：Corynebacterium Species、Micrococcus Luteus…（384 字）
```

以機械規則（≤40 字、單行、無英文、無編號）篩選有 65.4% 通過，但那是**上界**——「葡萄球菌、鏈球菌、肺炎雙球菌、腦膜炎球菌及其他具有感受性細菌引起之感染症。」通過了全部篩選，對長輩一樣沒有意義。

### 為什麼「權威來源優於模型讀出的字串」在這個欄位上不成立

**兩者回答的是不同問題。** 仿單適應症回答「這個藥**核准**用於哪些適應症」（監管範疇）；病人想知道的是「**我**為什麼要吃這個」。藥袋上醫師或藥師印的，通常已經是針對這位病人挑過的那一個。用仿單取代藥袋那行，不是把精確度提高，是答非所問。

**而且會擴大病情揭露範圍。** 一張藥證常涵蓋多個適應症，病人只因其中一個服藥。家屬看到「癲癇症、三叉神經痛、腎原性尿崩症及雙疾性疾患」會不知道長輩到底是哪一種，比原本只顯示藥袋那行揭露得更多、也更容易誤解。這與 `medication-identification` 對適應症的隱私處置是同一種考量。

**再者它時有時無。** 仿單適應症只在 `license_number` 確定時查得到，而證號確定率本身就是既有瓶頸（見 `drug-appearance` spec 的「證號唯一才可信」）。取代不了一個藥袋上一定讀得到的欄位。

因此本提案把仿單適應症用在兩個它真正有優勢的地方：**當錯讀的核對線索**，以及**當「這是什麼藥」的補充**——而不是取代「你為什麼吃這個」。

## What Changes

- **建表腳本新增 `--fetch-indications`**：沿用 `--fetch-images` 已建立的模式（獨立旗標、預設不跑、可中斷續跑）。從已下載的許可證資料集取 `適應症`，產出 `resources/drug_indications.json`，以證號為鍵。
- **需要摘要的才呼叫 LLM**：判定條件為 >40 字、多行、含 4 字以上英文、或含編號清單——實測整份藥證庫 12,172 筆（18%）符合，共約 104 萬中文字。其餘 `summary` 為 null，呈現面直接用原文，SHALL NOT 為此浪費 LLM 呼叫。
- **摘要以原文 sha256 做冪等**：重跑時原文未變即跳過。資料每 7 日更新但多數不變，與 `--fetch-images` 的「已存在即跳過」同一個設計。
- **新增 `DrugIndicationService`**：負責證號 → 適應症查詢，以及藥袋適應症與仿單的比對。**`DrugCatalogService` 完全不動**——它的職責是藥名比對，其 gram 反向索引是效能敏感結構（當初正是為了修「未命中卡住事件迴圈 400~750ms」而建），SHALL NOT 讓與比對無關的資料進入。
- **比對結果只記錄，SHALL NOT 影響信心度**：`RecognizedDrug` 新增一個記錄比對結果的欄位，`confidence_level` 與 `all_names_verified` 的計算維持不變。理由見下方風險段。
- **LIFF 首次呈現適應症**：藥品詳情顯示藥袋讀到的那行（主要位置），食藥署仿單另置於預設收合的次要區塊，明確標示來源；有摘要時顯示摘要並可展開原文，無摘要時直接顯示原文。
- **適應症仍 SHALL NOT 進入任何推播訊息**：`medication-identification` 既有條文不變，本提案新增的仿單欄位同受此限。

## Capabilities

### New Capabilities

- `drug-indication`：仿單適應症的來源與建置期落地、摘要的生成約束與降級、藥袋適應症與仿單的比對規則與其結果的處置邊界

### Modified Capabilities

（無。）

`medication-identification` 不列入：`RecognizedDrug` 雖新增一個記錄比對結果的欄位，但「結構化辨識輸出」規範的是模型該輸出什麼，該欄位由辨識之後的比對步驟填入，不在其約束範圍；「信心度分級決定確認方式」的判定條件也一字不動——本 change 要保證的正是它不變，這條保證寫在 `drug-indication` 內即可。

`medication-reminders` 不列入：它規範的是提醒規則與推播行為，不含 LIFF 的藥品資訊呈現（藥丸照片的呈現規則同樣是放在 `drug-appearance` 而非這裡）。仿單適應症不得進入推播的禁令寫在 `drug-indication` 內，與既有「功能開關與隱私」對藥袋適應症的禁令並存而不覆寫——兩份 MODIFIED 打同一條 Requirement 會在 archive 時互相取代，這是刻意避開的失敗模式。

## Impact

- **CARE**：`scripts/build_drug_catalog.py`、`resources/drug_indications.json`（新增產出物）、`app/services/medication/drug_indication_service.py`（新增）、`app/models/medication.py`、`app/models/prescription.py`、`app/services/medication/prescription_scan_service.py`、`app/services/medication/medication_service.py`、`app/core/config.py`、`app/dependencies.py`、`.env.example`
- **CARE-LIFF**：`src/types/medication.ts`、`src/pages/Medications/`（適應症區塊元件）、`src/i18n/medicationMessages.ts`
- **API**：`GET /api/medications/reminders` 的藥品物件新增仿單適應症欄位。**欄位新增，無 breaking change**
- **測試**：`tests/unit/services/medication/test_drug_indication_service.py`（新增）、`tests/unit/services/medication/test_prescription_scan_service.py`、`tests/unit/resources/test_drug_indications_artifact.py`（新增）、`tests/unit/routers/test_medications_router.py`、`CARE-LIFF/src/tests/medications.test.tsx`
- **體積**：`drug_catalog.json` 維持 15.9 MB 不變；新增 `drug_indications.json` 約 8 MB（原文約 6.5 MB + 摘要約 1.5 MB）。若併入 catalog 會使其漲到 22.2 MB（+39%），這正是本提案採獨立檔案的原因之一
- **相依**：建表腳本目前是純 `requests`、零 LLM 相依；`--fetch-indications` 會讓「重建藥證庫」在需要摘要時額外需要 `GEMINI_API_KEY`。取用既有的 `MODEL_NAME`（現行 `gemini-2.5-flash`），不新增外部服務。**未帶該旗標時腳本行為與現在完全相同**
- **執行期不對外連線**：仿單資料為建置期靜態檔，與藥證庫、藥丸縮圖同一慣例（`prescription-bag-scan` design 決策 3）

### 風險：比對規則的誤判率

比對採確定性的中文字 2-gram 重疊（排除無鑑別度的常見字），零重疊即視為「完全不相干」。實測：

| 情境 | 結果 |
| --- | --- |
| 隨機配對（＝兩個不相干的藥）2,000 組 | 88.2% 零重疊 → 抓得到 |
| 同成分不同廠牌的兩張藥證（各自撰寫）2,000 組 | 7.2% 零重疊 → 誤判 |

但 7.2% 偏樂觀，它比的是兩份長仿單。真實情境是「藥袋上一句短語 vs 仿單長文」，短語的 gram 少很多。以截短模擬：

| 藥袋片語長度 | 誤判率 | 抓到率 |
| --- | --- | --- |
| 6 字 | 25.4% | 96.7% |
| 10 字 | 20.3% | 95.7% |
| 15 字 | 18.3% | 95.3% |
| 整個片語 | 17.3% | 94.5% |

真值落在 7.2%~25.4% 之間：模擬用的是跨廠商各自撰寫的兩份仿單，而真實情況是藥袋 vs 該品項自己的仿單，藥局多半沿用同一份說法，實際重疊應該更高。**但沒有真實藥袋標註資料可以證實，因此不得假設它落在區間的哪一端。**

這正是「只記錄、不影響信心度」的理由：`scan()` 的 `all_names_verified` 要求**全部**藥品通過，一顆誤判就讓整份草稿失去一鍵確認。若每顆誤判率 20%，一張三種藥的藥袋維持 high 的機率只剩 `0.8³ ≈ 51%`——一半的正確辨識會被一個測不準的規則拖垮既有的一鍵確認路徑。先累積真實資料量出實際誤判率，確認安全後再以另一個 change 接上信心度。

## 刻意不做

- **以仿單適應症取代 `Medication.indication`**（理由見 Why：答非所問、擴大病情揭露、時有時無）。
- **以比對結果影響 `confidence_level`**（理由見上方風險段；待真實資料後另案評估）。
- **取用許可證資料集的其他欄位**——`主成分略述`、`用法用量`、`劑型`、`藥品類別`、`管制藥品分類級別`、`包裝與國際條碼` 都有各自的用途（例如管制藥的提醒策略、以條碼取代藥名辨識），但各自需要獨立評估，混進本 change 只會讓一次變更同時動到太多決策。
- **在推播中顯示任何適應症**（`medication-identification` 既有條文，本提案不放寬）。
