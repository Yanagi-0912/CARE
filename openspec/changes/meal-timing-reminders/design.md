## Context

現況（見 `medication-reminders` spec）：

- 規則 `MedicationReminder` = 一位用藥者的一個時段（`slot_type`）＋一個 `scheduled_time`＋`medication_ids`；「同一位使用者的同一個時段永遠只有一份規則」是排程器不重複推播的前提（`find_or_create_reminder`、`update_reminder` 的 409 檢查）。
- 排程器每個 tick 以 `scheduled_time <= 現在` 撈規則、以 `(reminder_id, scheduled_at)` upsert 當日紀錄；T+0 依 `scheduled_at`、T+20 依 `scheduled_at + 20min`、T+30 依 `timeout_at` 推播。紀錄是展開當下的快照，三階查詢不回頭 join 規則。
- 推播卡片列出規則 `medication_ids` 中當日有效的藥品，只有一顆【我已用藥】；確認走 postback `action=confirm_medication&log_id=`。
- LIFF 新增表單只勾時段，時間由後端套預設值；編輯視窗可改單一時間。藥品只由藥袋辨識提交建立，沒有列出／手動新增的端點。
- 主線剛合併「改排程後當日紀錄的對齊」（`resync_pending_by_reminder`）與推播重試上限，本設計必須與之相容。

LINE 平台限制：Flex 訊息送出後不可修改，沒有勾選框元件；按鈕只能觸發 postback，回應必須是另一則訊息。

## Goals / Non-Goals

**Goals**

- 同一時段可分別設定飯前、飯後的時間與藥品，並在**同一則**推播裡呈現。
- 逐顆藥確認，全部確認後紀錄才算 `taken`；不會用的長輩仍可一鍵整批確認。
- 新增提醒時就能自訂時間。
- 既有規則、既有紀錄、藥袋辨識路徑、排程器的併發保證與停機補償全部不受影響。

**Non-Goals**

- 使用者上傳藥品照片；從藥袋解析飯前飯後；拖拉互動；手動加藥的藥證比對。

## Decisions

### 1. 條目放在規則裡，不拆成多筆規則

規則新增 `entries: list[ReminderEntry]`，`ReminderEntry = {meal_timing, scheduled_time, medication_ids}`，`meal_timing ∈ {before_meal, after_meal, none}`，同一規則內每種至多一個、至少一個條目。

不採「飯前一筆規則、飯後一筆規則」：那會把唯一性的 key 從 `(user, slot)` 改成 `(user, slot, meal_timing)`，`find_or_create_reminder`、409 檢查、前端「已設定」判斷、藥袋提交全部要跟著改；而且兩筆規則就是兩則推播、兩筆紀錄，做不到使用者要的「同一個時段一則訊息」。條目內聚在一筆規則裡，唯一性不變量與紀錄的唯一識別都原樣保留。

### 2. `scheduled_time` 與 `timeout_anchor_time` 是派生欄位，仍落地

- `scheduled_time` := 條目中最早的時刻。排程器撈規則的查詢（`scheduled_time <= 現在`）與紀錄的 `scheduled_at` 完全不動，T+0 訊息就在最早條目的時刻送出。
- `timeout_anchor_time` := 條目中最晚的時刻。T+20 催促與 T+30 家屬警報以它為基準——飯前 07:30、飯後 08:30 時，08:00 不該催、08:00 更不該通知家屬漏吃。

兩者皆由 repository 寫入前的 `normalize` 重算，任何寫入條目的路徑（建立、`entries` 更新、藥袋提交連結）都經過同一支函式；不允許呼叫端自己算。單一 `none` 條目時兩者相等，等同現況。

### 3. `medication_ids` 保留為條目聯集，唯讀

現有讀者（排程器藥名快取、`GET /reminders` 的解析、`list_medication_names_for_log`）都讀 `medication_ids`；改成聯集派生值後這些讀者不必動。寫入只能透過條目：`link_medications_to_reminder`（藥袋提交）改為把藥掛到 `none` 條目（缺席時以規則現行 `scheduled_time` 建立），再由 `normalize` 重算聯集。API 不接受直接寫 `medication_ids`。

### 4. 紀錄新增 `urgent_at` 與 `taken_medication_ids`

- `urgent_at` := 當日 `timeout_anchor_time + 20min`，`timeout_at` := `+30min`。T+20 的待推播查詢改讀 `urgent_at`；舊紀錄沒有這個欄位，查詢加一條 `urgent_at` 缺席時退回 `scheduled_at + 20min` 的分支（部署當下最多只有幾筆 pending 紀錄，不做回填）。
- `taken_medication_ids: list[str]`，逐藥確認累積於此（`$addToSet`）。「全部到齊」的判定：該規則於紀錄當日有效的藥品集合 ⊆ `taken_medication_ids` 時，狀態轉 `taken` 並記 `taken_at`。判定用的集合在確認當下重算，不快照——使用者在訊息送出後又加了一顆藥，舊訊息按完仍差一顆，此時【全部已服用】仍可收尾；反之藥品被停用則自動不再計入。
- 「全部已服用」（不帶 `medication_id` 的 postback）行為與現在相同：直接 `mark_as_taken`，並把當時有效的藥品全部寫進 `taken_medication_ids`，讓用藥歷史能一致地回答「那次吃了什麼」。

### 5. 改條目後當日紀錄的對齊，沿用 `resync_pending_by_reminder` 並擴充

- 最早時刻改變 → 既有邏輯：舊 `scheduled_at` 的 pending 紀錄註銷，新時刻由排程器展開；改到已超過補推期限的時刻預先寫入 `cancelled`。
- 最早時刻不變、最晚時刻改變 → 就地改寫該 pending 紀錄的 `urgent_at` 與 `timeout_at`（比照「時刻相同只換時段名稱」的改標路徑）。
- 只改藥品指派、時刻都不變 → 不動紀錄；推播與確認判定都在當下讀規則。

### 6. 推播版面與逐藥確認

T+0 卡片：標題仍是時段名稱；本文依「飯前 → 飯後 → 其他」的固定順序，每區一行「飯前　07:30」小標，其下每顆藥一列（縮圖規則不變）＋一顆【已吃】按鈕（`action=confirm_medication&log_id=…&medication_id=…`）；已在 `taken_medication_ids` 的藥不出現按鈕（T+20 催促據此只列尚未確認的藥）。底部維持【全部已服用】。只有 `none` 一個條目時不顯示區塊小標，版面與現在只差每列多一顆按鈕；`medication_ids` 為空時版面與現在完全相同。既有「超過顯示上限收斂為單行計數」維持，超出者沒有按鈕。

逐藥 postback 的回覆（reply token，不耗推播額度）：未到齊時回一則純文字「已記錄：X。還有 N 種：Y、Z」；到齊時回覆現有的完成卡（`disabled=True`）。重複按同一顆是冪等的，回覆相同。家屬的逾時警報與錯過彙整通知不列藥品、不列飯前飯後，維持現狀。

### 7. 指派互動用點選，不用拖拉

LINE 內嵌瀏覽器的觸控拖拉在 iOS 上會與頁面捲動打架，長輩的手指也不穩；而且拖拉需要額外引入函式庫並補無障礙備援。改為每張藥品卡上兩顆 ≥44px 的【飯前】【飯後】切換鈕，按下即移入該區塊、再按移回；視覺上仍是「方塊在區塊之間移動」。

### 8. 詳細設定是整頁路由

多層（時段 → 飯前/飯後 → 藥品）塞進 dialog 在手機上會變成 dialog 裡再捲動，且要處理焦點鎖定的巢狀問題。改為 `/medications/detailed?target=<userId>` 整頁，第一層四張時段卡（顯示既有條目摘要），點進去是該時段的編輯面：飯前、飯後各一組「啟用＋時間」，下方藥品卡清單（`GET /medications`）與「新增藥品」輸入框。儲存時該時段沒規則走 `POST`（帶 `slot_entries`），有規則走 `PUT`（帶 `entries`）。

新增表單（簡易模式）維持 dialog：每個勾選的時段展開一個時間欄位，預設帶 `DEFAULT_SLOT_TIMES`，送出帶 `slot_times`；底部一顆「詳細設定」導向整頁。

### 9. API 形狀

- `POST /medications/reminders`：既有 `slots` / `slot_times` 不變；新增 `slot_entries: dict[slot, list[EntryInput]]`，有給的時段以它為準。目標時段已有規則時回 409（現況是靜默建第二筆，本 change 一併收緊——否則詳細頁重複送出就會製造重複推播）。
- `PUT /medications/reminders/{id}`：新增 `entries: list[EntryInput]`（整份取代）。`scheduled_time` 對多條目規則回 400（不知道要改哪一個時刻），單條目規則維持現況。
- `EntryInput.medication_ids` 必須全部屬於該用藥者，否則 400。
- `GET /medications?user_id=`：回該用藥者的藥品（含停用者，帶 `enabled`），縮圖與適應症欄位比照 `GET /reminders` 的解析。`POST /medications`：`{user_id, name}`，`source=manual`。授權與提醒端點相同（對用藥者 GENERAL 的讀／寫）。
- `GET /reminders` 的每筆回應多出 `entries`、`timeout_anchor_time`；每個條目的藥品由前端以 `medications` 陣列按 id 對照。

## Risks / Trade-offs

- **舊訊息上的按鈕在規則改動後可能對不上**（藥被停用、時間改了）：確認一律以紀錄與規則的當下狀態判定，冪等回覆，最壞情況是回覆「已記錄」而不再列它；不會造成錯誤狀態。
- **Flex 大小**：每列多一顆按鈕會放大 JSON，既有的顯示上限（收斂為單行計數）是天花板；實作時以最大上限的卡片對照 LINE 的 bubble 大小限制驗證一次。
- **`urgent_at` 查詢的雙分支**只為過渡期的舊紀錄存在；紀錄的三階時間窗最長 30 分鐘，部署後隔天即可移除分支（列入 tasks 的收尾項）。
- **「全部到齊」用當下集合而非快照**：規則在訊息送出後被加藥時，按完舊訊息的按鈕仍是 pending。接受此行為，因為反向（快照）會讓停用的藥永遠擋住完成。

## Migration Plan

不需要資料回填。`entries` 缺席 → 由 `(scheduled_time, medication_ids)` 合成單一 `none` 條目；`timeout_anchor_time` 缺席 → 等於 `scheduled_time`；紀錄的 `urgent_at` 缺席 → 查詢退回 `scheduled_at + 20min`；`taken_medication_ids` 缺席 → 空陣列。後端先部署（舊前端送的請求形狀完全相容），前端隨後。

## Open Questions

- 飯前／飯後條目的時刻是否要強制「飯前 < 飯後」？目前不強制，只固定顯示順序；若使用者反映設反了，再加驗證。
