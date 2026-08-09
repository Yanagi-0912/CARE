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

**比對方式**：以正規化後的中文品名與英文品名建索引（去除引號、廠商前綴、全半形與空白差異），先做完全比對，再退回相似度比對並取最高分；相似度低於門檻視為未命中。門檻值列為 Open Question。

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

- 藥證庫模糊比對的相似度門檻要訂在哪？需要以實際藥袋樣本量測誤判率後決定，暫以保守值起步（寧可判為未命中而多一次人工核對）。
- 推播藥品清單的顯示上限訂多少？需要看實際處方的藥品數分布。
- 頻次代碼 `QD` 預設映射到 `morning` 是否合適？部分一日一次的藥（如降血脂）臨床上習慣睡前服用。目前依賴使用者在核對畫面覆寫，是否值得依藥品分類給更好的預設值，留待有實際資料後評估。
