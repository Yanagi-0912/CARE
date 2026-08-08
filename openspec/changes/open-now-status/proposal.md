## Why

`clinicTime` 覆蓋率 100%，且 `_get_business_status()` 已能算出「營業中／休診」，
但這個結果**只用來在卡片上顯示一個標籤**。使用者真正在問的問題不是「這家有開嗎」，
而是「**我什麼時候能去**」，而系統目前答不出來。

探測資料庫後，原本「把營業中做成篩選條件」的想法被三組數據推翻：

**1. `clinicTime` 記錄的是門診時間，不是「有沒有人」**

```
有急診醫學科的 197 家醫院，深夜 03:00 判定為「營業中」：1 家
醫院類 464 家，          深夜 03:00 判定為「營業中」：1 家
```

急診室 24 小時開放，但 `clinicTime` 記的是門診 08:00–17:00。若以營業狀態篩選，
凌晨搜尋急診會把 197 家醫院全部藏起來並回覆「附近沒有院所」。**這是安全問題，非體驗問題。**

**2. 兩個時段會讓篩選近乎清空結果**

```
平日上午 週三 10:00   81.7%        平日午休 週三 13:00   11.5%
平日晚間 週三 19:30   74.0%        週日上午      10:00   10.4%
週六上午      10:00   79.3%        平日深夜 週三 03:00    0.2%
```

午休時段砍掉 88%，但那多半是「14:00 就開」而非「今天不看了」——
預設篩選會在每天中午刪掉最有用的答案。

**3. `notes` 欄位完全沒被使用，且會使營業判斷失準**

```
有 notes                                    1,304 家
notes 提及 休診/停診/春節/國定假日             691 家
  └─ 其中 clinicTime 判定為「營業中」          617 家   ← 可能實際休診
```

`clinicTime` 不知道春節。目前這個誤差只造成「標籤看起來怪」；
一旦成為篩選條件，錯誤方向會變成兩邊都糟：該藏的沒藏（白跑一趟），不該藏的藏了。

## What Changes

- 新增 `app/services/medical/business_hours.py`：將營業狀態判斷從 Flex 呈現層抽出成
  獨立的領域邏輯（目前錯置於 `facility_brief_flex_message.py`，違反分層慣例且難以測試）。
- **新增「下次開診時間」**：休診時回答「明天 08:00 開診」而非只說「休診中」。
  這是本 change 的核心價值 —— 不需新資料、算錯代價低，且直接回答使用者的真實問題。
- **區分「午休中」與「今日已結束」**：同一份 `clinicTime`，判斷今天是否還有後續時段即可。
  對「我等一下要去」與「我明天再去」是完全不同的決定。
- **急診豁免**：`departments` 含急診醫學科的院所 SHALL NOT 顯示「休診」，改標「設有急診」。
  刻意**不標「24 小時」** —— 資料並未如此記載，宣稱營業時間屬於編造。
- **負面硬規則**：營業狀態 SHALL NOT 將設有急診的院所排除於搜尋結果之外，任何情況皆然。
- **顯示 `notes` 原文**（1,304 家），並以「是否含日期樣式」兩層處理其對狀態標籤的影響：
  含日期者（如「春節假期2／17~2／22休診」）僅顯示、不動標籤；
  不含日期的長期性註記（如「如需看診請先電話洽詢」47 家）才可將標籤降級為「請電洽」。
- **營業狀態一律顯示，篩選僅在使用者明確要求時觸發**（「附近現在有開的診所」）。
  即使篩選，結果為 0 時 SHALL 退回顯示最近院所與其下次開診時間，SHALL NOT 回「查無資料」。

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `location-search`：新增下次開診時間、營業狀態分級（營業中／午休中／今日已結束／今日休診）、
  急診豁免與不得排除、`notes` 揭露、營業中篩選的觸發條件與 0 筆退回等需求。

## Impact

- **新增程式**：`app/services/medical/business_hours.py`
- **修改程式**：`app/schemas.py`（`MedicalFacility` 新增 `notes`）、
  `app/repositories/medical_facility_repository.py`（映射 `notes`）、
  `app/services/medical/medical_service.py`（`open_now` 過濾與 0 筆退回）、
  `app/tools/medical_tools.py`（`open_now` 參數）、
  `app/services/agent/utils/nodes.py`（「現在有開」意圖偵測）、
  `app/services/agent/prompt.py`、`app/i18n/messages.py`、
  `resources/flex_messages/medical_messages/facility_brief_flex_message.py`、
  `resources/flex_messages/medical_messages/facility_detail_flex_message.py`
- **API/route**：無新增或變更 route。行為變更僅在 LINE webhook（`/line/callback`）的對話與卡片呈現層。
- **資料庫**：無 schema 變更、無新增索引。`notes` 與 `clinicTime` 皆為既有欄位。
  營業狀態於應用層計算（`clinicTime` 為嵌套結構，不適合下推為 Mongo 查詢條件）。
- **相容性**：`open_now` 為選填，省略時搜尋結果與現狀完全一致；
  卡片新增狀態文字與 `notes`，不移除既有欄位。
- **測試計畫**：
  - `tests/unit/services/medical/test_business_hours.py`：狀態分級、下次開診（跨日／跨週）、
    急診豁免、notes 兩層規則、無時段院所
  - `tests/unit/services/medical/test_open_now_filter.py`：`open_now` 過濾、0 筆退回、
    急診不得被排除
  - `tests/unit/services/agent/test_open_now_intent.py`：「現在有開的」意圖偵測與跨輪保留
  - 既有 804 項須維持全綠
