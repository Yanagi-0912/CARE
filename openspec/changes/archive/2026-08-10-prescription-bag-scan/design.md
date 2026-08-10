## Context

CARE 的用藥提醒（`openspec/specs/medication-reminders/spec.md`）已經有相當完整的排程語意：惰性展開執行紀錄、三階遞進推播、多實例下的原子搶佔、`(reminder_id, scheduled_at)` 唯一索引、停機後不補推播、錯過時段的彙整通知。這些條文大多是為了修正實際踩過的併發與時序問題而寫的，**任何會改變排程器輸入的變更都必須重新驗證這一整組行為**。

同時，這套機制底下的資料模型是空的：`app/models/medication.py:57` 的 `MedicationReminder` 沒有藥品欄位。這是本次變更真正要補的洞。

本設計的核心約束有四個：

1. **不改變排程器的輸入。** 藥品關聯必須是附加在規則上的資訊，不能參與展開判定。
2. **不動 LINE 影像訊息的共用路徑。** `media_handler` → `mutimedia_processor` → `MEDIA_PARSE_WEBHOOK_URL`（n8n）這條路是其他人維護的，且 `mutimedia_processor.py:66` 用一個 `except Exception` 把所有錯誤吞成同一句話——藥袋辨識需要區分失敗原因，塞進去只會讓兩邊都變糟。
3. **可測試，且不得使用 monkey patch。** 依 `openspec/config.yaml` 的 `rules.tasks`，測試替身一律以依賴注入傳入。這排除了所有「直接 import module-level singleton」的作法。
4. **辨識結果不可直接成為用藥指示。** 這是醫療情境，錯誤的提醒會導致錯誤的服藥行為。

## Goals / Non-Goals

**Goals：**

- 讓一次藥袋拍照能產生一份可核對的草稿，核對後一次建立整個療程的藥品與提醒關聯
- 引入藥品實體，讓「早上吃 A+B、中午只吃 A」與「單獨停掉一種藥」成為可表達的狀態
- 以外部藥證資料偵測視覺模型的錯讀，並讓低信心結果強制走人工核對
- 全部新增邏輯落在 `app/services/medication/` 之下，且可獨立單元測試

**Non-Goals：**

- 藥丸本體的影像辨識
- LINE 聊天室直接傳照片的自動偵測分流
- Rich Menu 版面調整
- 從辨識結果推導交互作用、重複用藥或劑量安全性檢查——本能力只做「把藥袋上寫的東西變成結構化資料」，不做臨床判斷
- 修正 `mutimedia_processor` 既有的錯誤吞噬行為（可獨立提案）

## Decisions

### 決策 1：影像從 LIFF 直接上傳，不走 LINE 訊息路徑

**選擇**：LIFF 以 `<input type="file" accept="image/*" capture="environment">` 取得影像，multipart 上傳到 `POST /api/medications/prescription-scan`，同步回傳草稿。（前綴是 `/api/medications`：router 檔案位於 `app/routers/users/medications.py`，但 `app/main.py:102` 掛載於 `/api/medications`。）

**替代方案 A — 在 `media_handler` 內分流**：使用者於聊天室傳照片，handler 判斷是不是藥袋。體驗最自然（拍了就傳），但要動共用路徑、要為每張圖多付一次分類呼叫、且 `media_handler.py:11` 是 module-level singleton 直接 import，加測試就必須先做 DI 重構。否決：改動面與風險都落在別人維護的檔案上。

**替代方案 B — 在 `dispatcher` 層攔截**：以 postback 建立「等待藥袋照片」狀態，`dispatcher.py:153` 的影像分支先查狀態再決定交給誰。`media_handler` 本身不動，但仍要改 dispatcher、要新增一個狀態機 collection、要自己實作 LINE content 下載。否決：為了保留「在聊天室拍」而付出的複雜度，換不到對應價值——使用者反正都得先點入口。

**選 LIFF 直傳的理由**：LIFF 已在本次範圍內（草稿逐欄編輯本來就需要一個真正的表單），選它等於把入口與編輯合併在同一個畫面，少一次上下文切換；影像不進 LINE 訊息紀錄，隱私較好；請求／回應同步，不需要狀態機；`dispatcher`、`media_handler`、`mutimedia_processor` 三支檔案零改動。

**代價**：失去聊天室內直接拍照的路徑。入口掛在 Rich Menu 既有的「用藥提醒」格（已指向 `{LIFF_URL}/medications`，見 `openspec/specs/rich-menu/spec.md:21`），不需要新增格位。

### 決策 2：視覺模型直接輸出結構，不接傳統 OCR

**選擇**：沿用既有 `GeminiService`（`app/dependencies.py:70`）與 `GEMINI_API_KEY`，以 schema 約束的 structured output 一次取得結構化結果。

**替代方案 — OCR（Tesseract／Cloud Vision）+ 規則解析**：需要自行處理版面分析與欄位歸屬。台灣的藥袋沒有統一版型，每家醫院、診所、社區藥局的排版、字體、欄位順序都不同，規則解析在跨機構時會大量失效。否決。

**附帶好處**：不新增外部服務、不新增金鑰、不新增計費來源。

**約束**：`用法原文` 必須原樣保留。正規化後的頻次代碼是系統要用的，但使用者核對時要看的是藥袋上實際印的字串——只給正規化結果，使用者無從判斷正規化本身有沒有錯。

### 決策 3：藥證庫是離線靜態檔，不在執行期呼叫外部 API

**選擇**：`scripts/build_drug_catalog.py` 從全部藥品許可證資料集與藥品外觀資料集建出 `resources/drug_catalog.json`，執行期只讀本地檔。

**理由**：資料每 7 日更新，沒有即時性需求；執行期零外部相依，辨識延遲不受政府站台可用性影響；比對邏輯是純函式，好測。

**比對方式**：以正規化後的中文品名與英文品名建索引（去除引號、廠商前綴、全半形與空白差異），分三階段：完全比對（score 1.0）→ 含容比對（查詢字串與藥證品名互為子字串）→ 相似度比對（`SequenceMatcher`，取候選集合中的最高分，低於門檻視為未命中）。含容比對是以實測資料量測後才補上的階段，理由見下方「已量測並決定」。候選集合來自建構子裡建一次的字元 n-gram 反向索引，不對全部鍵做線性掃描；細節見 `drug_catalog_service.py` 模組 docstring 與各方法註解。

**已量測並決定（原 Open Question：藥證庫模糊比對的相似度門檻要訂在哪？）**：

用食藥署全部藥品許可證資料集實際建出的藥證庫（66,478 筆條目、112,230 個正規化鍵）量測後發現，**純相似度門檻是選錯的機制，不是門檻數字選錯**：

- `脈優錠5毫克`、`冠脂妥膜衣錠10毫克` 這類藥袋上完整印出品名的查詢，完全比對命中，score 1.000。
- `普拿疼`、`LIPITOR` 這類藥袋只印短品牌名、藥證卻連劑型劑量都在內的查詢，雖然字面上明明是藥證品名的子字串，`SequenceMatcher.ratio()` 卻因為長度差被拉到 0.5 上下（`'普拿疼'` 對 `'普拿疼錠500毫克'` 只有 0.500；`'LIPITOR'` 對 `'LIPITORF.C.TABLETS10MG'` 只有 0.483）。沒有任何門檻能同時保留這種命中又擋掉真正形近的錯讀——調低門檻到能接住這兩者，雜訊也會一起被接住。這類查詢在真實藥袋上很常見，後果是幾乎每份草稿都被判為 `medium`，一鍵確認形同虛設，低信心標記也因為到處都是而失去意義。

**決定**：改為三階段比對（完全 → 含容 → 相似度），相似度門檻維持保守值（`0.88`）不變——含容比對已經接住了「短名子字串」這個真正的問題，門檻不需要再為了接住它而調鬆，繼續擋形近錯讀。含容比對命中不只一張藥證時（例如「普拿疼」同時是好幾個普拿疼系列產品品名的子字串），藥名視為已驗證但 `license_number` 留空，不臆測是哪一個品項——見 `DrugCatalogMatch` 的說明與 `drug_catalog_service.py` 的單元測試。

**藥證庫缺席時的行為**：不讓應用啟動失敗，但所有名稱信心度降為低——這會讓每一份草稿都強制人工核對，是安全的退化方向。

### 決策 4：藥品關聯不參與排程展開

**選擇**：`medication_ids` 只是規則上的一個附加欄位。排程器（`app/services/medication/medication_scheduler.py`）的展開判定、搶佔、狀態轉移完全不讀它；只有組裝推播文案時才解析。

**理由**：`medication-reminders` 的併發條文（原子搶佔、唯一索引、停機補償）是這個系統裡最容易寫錯的部分。把藥品關聯排除在展開路徑外，本次變更就不需要重新驗證那些行為，回歸測試範圍也只落在文案組裝。

**代價**：「某時段所有藥都失效了」不會讓該時段停止推播。這是刻意的——規則可能是使用者手動建的，靜默停用會移除他明確設定過的東西。

### 決策 5：草稿是獨立 collection，不是 `medications` 的 pending 狀態

**選擇**：新增 `prescription_drafts`，帶 TTL 索引自動過期。

**替代方案 — 直接建 `medications` 並標 `status=draft`**：會讓每個讀取藥品的地方都得記得過濾狀態，漏一處就會有未確認的辨識結果洩漏到推播裡。否決。

**冪等**：草稿以 `committed_at` 與寫入結果的 id 記錄提交狀態；重複提交回傳既有結果。TTL 過期後提交回 410。

### 決策 6：分層與組裝

- `app/models/prescription.py`：草稿與辨識結果的 Pydantic 模型
- `app/repositories/prescription_draft_repository.py`：草稿的存取
- `app/services/medication/prescription_ocr_service.py`：影像 → 結構化結果（依賴注入 `GeminiService`）
- `app/services/medication/drug_catalog_service.py`：純比對，建構子接受已載入的藥證庫資料
- `app/services/medication/prescription_scan_service.py`：協調上述三者、對象比對、頻次映射、提交時寫入
- 全部在 `app/dependencies.py` 組裝

`drug_catalog_service` 的建構子接受資料而非路徑，是為了讓測試直接餵小型固定資料集，不需要碰檔案系統，也不需要 monkey patch。

### 決策 7：錯誤分類

辨識路徑的失敗一律轉為三種可區分的原因：`unreadable`（影像判讀失敗，建議重拍）、`not_prescription`（不是藥袋）、`service_unavailable`（外部呼叫失敗或逾時，建議稍後再試）。三者對使用者的下一步指示完全不同，合併成同一則訊息會讓使用者重複做無效的重拍。

這條規則只適用於新增的路徑；`mutimedia_processor` 既有的吞噬行為不在本次範圍內。

## Risks / Trade-offs

- **視覺模型讀錯藥名，但自述信心度很高** → 藥證庫比對是唯一的偵測手段；比對未命中一律降為低信心並強制人工核對。這也是為什麼藥證庫缺席時要把所有結果降級，而不是放行。
- **藥證庫覆蓋率不足** → 藥品外觀資料集實測 6,269 筆，遠少於全部核准藥證數量；因此比對主資料源是全部藥品許可證資料集，外觀資料集僅作補充。仍會有未命中的正確藥名被降級成低信心——這個方向的錯誤只增加核對成本，不會產生錯誤提醒，可接受。
- **使用者在核對畫面一路按確認** → 高信心才給一鍵確認；只要有任一藥名未通過校驗或必要欄位為空，就移除一鍵確認、強制逐筆檢視。
- **單張影像含多個藥袋** → 以「出現多個病患姓名或多份調劑日期」為訊號提示重拍，同時仍回傳已辨識到的項目，避免使用者白拍一次。
- **`PRN` 被誤判為定時頻次** → 頻次無法明確歸類時一律落到 `OTHER` 並要求使用者指定時段，不臆測。反方向的誤判（定時藥被讀成 `PRN`）會導致漏建提醒，由核對畫面上的 `用法原文` 對照攔截。
- **藥品數量多時推播過長** → 藥品清單設顯示上限，超出收斂為單行計數，與既有「錯過時段彙整通知」的處理方式一致。
- **新增欄位對既有資料的相容性** → `medication_ids` 缺欄位時讀為空陣列，不需要資料遷移；推播版面在空陣列時與變更前完全相同。
- **模糊比對未命中時卡住事件迴圈** → 用實際藥證庫（112,230 個正規化鍵）量測發現，`SequenceMatcher` 對全部鍵線性掃描一次要 400~750ms；`match()` 是同步呼叫，卻被包在 `async def scan` 裡執行，一次掃描就讓整個行程停擺，而用藥提醒排程器跑在同一個行程——一張藥袋上有五個未命中的藥名，就是兩三秒的排程延遲，且延遲隨藥證庫成長只會變嚴重。解法是在建構子裡建一次字元 n-gram 反向索引，含容比對與模糊比對都只在索引narrow 出來的候選集合上跑，不再對全部鍵掃描；未命中的查詢也會先被索引擋掉候選（候選集合為空或極小），不會退化成全掃描。以此量測 `脈優錠5毫克`、`冠脂妥膜衣錠10毫克`、`普拿疼`、`LIPITOR`、`這絕對不是一個藥名`、`XYZQWERTY` 六個查詢（含兩個刻意的未命中），單次查詢皆在 1ms 以內，候選集合大小介於 1～2,006 筆之間，遠低於全庫的 112,230 筆。

## Migration Plan

1. 先落地資料模型與 repository（`medication_ids` 預設空陣列、`prescription_drafts` 與其 TTL 索引），此時無任何行為變更
2. 建置藥證庫並提交 `scripts/build_drug_catalog.py`；`resources/drug_catalog.json` 是產出物
3. 辨識與比對服務（純邏輯，可完整單元測試）
4. API 端點與 `app/dependencies.py` 組裝，`PRESCRIPTION_SCAN_ENABLED` 預設 `false`
5. 推播文案的藥品區塊（`medication_flex.py`）
6. LIFF 掃描與草稿編輯頁
7. 開啟 `PRESCRIPTION_SCAN_ENABLED`

**回滾**：把 `PRESCRIPTION_SCAN_ENABLED` 設回 `false` 即可停用整條路徑；已建立的藥品與關聯不受影響，推播的藥品區塊在藥品失效或關聯為空時自動退回原版面。無需資料回滾。

## Open Questions

- **推播藥品清單的顯示上限（`MEDICATION_LIST_MAX_ITEMS`，目前為 `5`）訂多少才對？** 這個數字是憑直覺選的，不是量測結果，維持開放。會解決這題的量測是：`PRESCRIPTION_SCAN_ENABLED` 對真實使用者開啟一段時間後，統計每個時段實際掛載的藥品數分布（例如中位數、第 90／99 百分位）——這個上限只影響「推播訊息裡一次列出幾個藥名」的顯示層，不影響藥品或提醒本身有沒有被建立；超過上限的部分已經收斂成一行計數（`_medication_list_rows`，`medication_flex.py:78`），不是被截斷丟失。在拿到這組分布之前，任何具體數字都是編出來的，寧可讓這題繼續開著。

## Decisions（追加）

### 決策 8：`QD` 的預設時段不是「該不該換一個更好的猜測」，而是既有的 `timing` 欄位沒被使用（原 Open Question 2，已由第一次真實環境執行結果回答）

**原提問**：「頻次代碼 `QD` 預設映射到 `morning` 是否合適？部分一日一次的藥（如降血脂）臨床上習慣睡前服用。目前依賴使用者在核對畫面覆寫，是否值得依藥品分類給更好的預設值，留待有實際資料後評估。」

**量測方式**：這個功能合併後第一次針對真實 Gemini API 的端到端執行（先前只被注入假物件的單元測試覆蓋過），以一張自行產生的、寫實的台灣藥袋影像實測：

```
藥品名稱：冠脂妥膜衣錠10毫克        （rosuvastatin，降血脂用藥）
用法用量：每日一次  每次一錠  睡前服用
          QD HS      共 28 天
```

模型正確辨識出：

```json
{"name": "冠脂妥膜衣錠10毫克", "frequency_code": "QD", "timing": "bedtime",
 "duration_days": 28, "usage_raw": "每日一次 每次一錠 睡前服用 QD HS 共 28 天"}
```

**發現這個問題本身問錯了方向**：不是「`QD → morning` 這個預設值準不準」，而是 `RecognizedDrug.timing` 從辨識階段就正確抽出了「睡前服用」，一路帶到草稿、顯示給使用者核對，卻在 `_resolve_slots` 決定時段時完全沒被讀取——`grep -c timing app/services/medication/prescription_scan_service.py` 在修正前回傳 `0`。因此這顆睡前藥的預設提醒被排到 08:00（`morning`），使用者只能靠事後在核對畫面手動改，而不是系統本來就該用已經有的資訊做對。「要不要依藥品分類給 `QD` 一個更聰明的預設時段」是個假問題：在還沒用到已經辨識出來的 `timing` 之前，不需要引入藥品分類這種額外的知識來源。

**決定**：`CommitDrugItem` 新增 `timing` 欄位（前端把草稿上核對過的 timing 原樣帶進提交請求），`_resolve_slots` 在頻次代碼隱含「一日單一劑量」（目前僅 `QD`；`HS` 已經映射到 `bedtime`，不受影響）且 `timing == "bedtime"` 時，把預設時段由 `morning` 改為 `bedtime`。`before_meal`／`after_meal`／`empty_stomach` 不指向任何特定時段，一律不影響映射。`BID`／`TID`／`QID` 等多劑量頻次不受 `timing` 影響，維持原有映射——多劑量藥袋上的「睡前」通常只限定最後一次劑量，頻次代碼在「一天吃幾次」這件事上是更明確的陳述，貿然用單一 timing 值覆寫整組時段反而會引入新的錯誤。使用者明確覆寫過的 `slots` 一如既往優先於一切自動映射，包含這條新規則。

見 `openspec/specs/medication-identification/spec.md` 的「頻次代碼映射至時段」條文與 `app/services/medication/prescription_scan_service.py::_resolve_slots`。
