## Why

用藥提醒目前以「早／中／晚／睡前」四個時段為最小單位，一個時段一筆規則、一個時間、一則推播、一顆【我已用藥】。這個粒度對長輩的實際藥袋來說太粗：同一個早上常同時有「飯前」與「飯後」兩批藥，時間相差半小時到一小時，而現在只能擇一提醒，或把兩批藥混在同一則訊息裡讓長輩自己記得先後。

三個具體缺口：

1. **沒有飯前／飯後的概念。** 規則與推播都只有時段名稱，藥袋上「飯前 30 分鐘」「飯後」這類最關鍵的服藥指示無處落地。
2. **新增時不能設定時間。** 後端 `POST /reminders` 早已接受 `slot_times`，但 LIFF 的新增表單沒有時間欄位，使用者得先建立再逐張卡片改時間。
3. **一則訊息只能整批確認。** 同一時段有三種藥時，長輩沒有辦法表達「飯前那顆吃了、飯後那兩顆還沒」；家屬也看不出是哪一顆漏了。

## What Changes

- **規則內建「條目」**：`MedicationReminder` 新增 `entries` 陣列，每個條目為 `(meal_timing, scheduled_time, medication_ids)`，`meal_timing` 為 `before_meal` / `after_meal` / `none`，一筆規則內每種至多一個。規則仍維持「一位用藥者一個時段一筆」的不變量；`scheduled_time` 改為派生值（條目中最早的時刻），新增派生欄位 `timeout_anchor_time`（最晚的時刻）。既有規則讀回時合成單一 `none` 條目，行為不變。
- **一時段一則 Flex、逐藥確認**：T+0 推播於最早條目時刻送出，訊息內依「飯前 → 飯後 → 其他」分區列出各自的時間與藥品，每顆藥一顆【已吃】按鈕，底部保留【全部已服用】。每按一顆以 reply token 回覆進度；全部到齊回覆完成卡並將紀錄轉為 `taken`。T+20 催促與 T+30 家屬警報改以最晚條目時刻為基準，且催促只列尚未確認的藥。
- **時間自由設定**：新增表單每個勾選的時段直接可改時間；「詳細設定」整頁流程可為每個時段設定飯前／飯後各自的時間並指派藥品，亦可只填藥名手動新增藥品。
- **新增兩個藥品端點**：`GET /medications`（列出用藥者的藥品，供指派）與 `POST /medications`（手動新增，`source=manual`）。
- **藥袋辨識提交**維持既有行為：連結到該時段規則的 `none` 條目（沒有就建立），不解析藥袋上的飯前飯後。

## Out of Scope

- 使用者上傳藥品照片（目前沒有任何使用者上傳影像的儲存機制；另開 change）。
- 從藥袋辨識結果自動判定飯前／飯後。
- 手動新增藥品的藥證比對與 OTC 成分警示（`drug-safety-alert` 只掛在辨識路徑，明文「不產生用藥資料」；手動加藥沒有影像也沒有藥證，兩者不相交）。
- 「拖拉指派」互動。改以點選按鈕移入飯前／飯後區塊，理由見 design 決策 7。

## Impact

- **後端 `CARE/`**：`app/models/medication.py`、`app/repositories/medication_repository.py`、`app/services/medication/medication_service.py`、`app/services/medication/medication_scheduler.py`、`app/services/medication/prescription_scan_service.py`、`app/routers/users/medications.py`、`app/services/line_messaging/flex/medication_flex.py`、`app/services/line_messaging/dispatcher/dispatcher.py`、`app/i18n/messages.py`。
- **前端 `CARE-LIFF/`**：`src/pages/Medications/*`（新增表單、卡片、編輯視窗、新的詳細設定頁）、`src/api/medicationApi.ts`、`src/types/medication.ts`、`src/lib/queryClient.ts`、`src/i18n/medicationMessages.ts`、`src/App.tsx` 路由、vitest 與 Playwright。
- **Specs**：`medication-reminders`（規則條目、推播版面、逐藥確認、催促與警報基準）、`medication-identification`（提交時連結到 `none` 條目）。
- **資料**：不需回填。舊規則與舊紀錄的缺席欄位一律於讀取時合成預設值。
- **CARE-n8n**：不動。
