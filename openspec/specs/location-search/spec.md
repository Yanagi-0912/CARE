# Location Search Spec

## Purpose

定義 CARE 尋找附近醫療院所的雙工作流：無座標時引導使用者以 LINE 位置快速回覆分享位置；取得座標後查詢鄰近院所並附上 Google Maps 連結。實作位於 `app/tools/medical_tools.py`、`app/services/medical/medical_service.py` 與 `app/services/line_messaging/`。
## Requirements
### Requirement: 無座標時請求位置

當使用者想尋找、前往或詢問醫療院所／醫院／診所／藥局的位置，且系統尚未取得其經緯度時，代理 SHALL 呼叫 `request_location_quick_reply` 工具，且 LINE 通道 SHALL 附上「分享位置資訊」的位置快速回覆按鈕。

#### Scenario: 詢問附近醫院但無座標

- **WHEN** 使用者傳送類似「幫我找附近醫院」而系統尚無其座標
- **THEN** 代理呼叫 `request_location_quick_reply`，回覆引導訊息並附上位置快速回覆按鈕

### Requirement: 依座標搜尋鄰近院所

當使用者傳送位置訊息（文字形式為「這是我的目前位置：lat=..., lng=...」）時，代理 SHALL 呼叫 `find_nearby_hospitals(lat, lng)`。系統 SHALL 進行地理空間查詢並回傳最鄰近的醫療院所清單，且每筆院所地址 SHALL 附上經 URL 編碼的 Google Maps 搜尋連結。

#### Scenario: 收到座標回傳院所清單

- **WHEN** 使用者傳送「這是我的目前位置：lat=25.033, lng=121.56」
- **THEN** 系統呼叫 `find_nearby_hospitals` 並回傳最鄰近院所清單，每筆含 Google Maps 連結

### Requirement: 逐級擴大搜尋範圍

鄰近院所搜尋 SHALL 於 5 公里內結果不足目標筆數（預設 5 筆）時，依 10、20、50 公里
逐級放寬，直到湊滿。50 公里為硬上限。

此分級 SHALL 同時適用於不分科別（`find_nearby_hospitals`）與依科別
（`find_nearby_facilities_by_department`）兩條路徑，兩者 SHALL NOT 使用不同的範圍策略。

#### Scenario: 兩條路徑行為一致

- **WHEN** 偏遠地區使用者分別詢問「附近有醫院嗎」與「附近有腸胃科嗎」
- **THEN** 兩者皆逐級放寬至找到院所為止，SHALL NOT 出現一邊找得到、一邊查無資料

#### Scenario: 5 公里內數量不足

- **WHEN** 5 公里內僅 1 家符合，20 公里內共有 5 家
- **THEN** 回傳 5 家，並說明已擴大搜尋範圍

#### Scenario: 50 公里內仍湊不滿

- **WHEN** 50 公里內僅找到 2 家符合的院所
- **THEN** SHALL 回傳該 2 家並說明僅找到 2 間，SHALL NOT 回傳「查無院所」

### Requirement: 查無院所處理

當放寬至 50 公里仍查無醫療院所時，系統 SHALL 回傳明確的無結果訊息，而非空白或錯誤，
並 SHALL 提供緊急聯絡方式。

#### Scenario: 50 公里內沒有院所

- **WHEN** `find_nearby_hospitals` 放寬至 50 公里後結果仍為空
- **THEN** 回傳說明已搜尋範圍的提示訊息，並提醒緊急時撥打 119

### Requirement: 誠實揭露搜尋範圍

回覆 SHALL 說明結果的實際涵蓋範圍。曾放寬範圍時，SHALL 以結果中最遠院所的實際距離
表述，SHALL NOT 以搜尋階梯的級距表述 —— 級距為 50 公里但最遠院所僅 27 公里時，
以級距表述會使使用者高估交通成本。

#### Scenario: 擴大後最遠院所距離小於級距

- **WHEN** 搜尋放寬至 50 公里級距，但找到的院所最遠僅 27.2 公里
- **THEN** 回覆說明「最遠約 28 公里」，而非「已擴大到 50 公里」

### Requirement: 依科別搜尋鄰近院所

當使用者指定科別（例如腸胃科、牙科、中醫）並已取得座標時，代理 SHALL 呼叫
`find_nearby_facilities_by_department(lat, lng, department)`，`department` 傳入使用者的原始說法。

資料庫 `departments` 僅含衛福部部定專科（55 個值），不含次專科，因此系統 SHALL 先經
`app/services/medical/department_matcher.py` 將使用者說法映射為部定專科後再查詢。
當映射結果與使用者說法不同時，回覆 SHALL 明確說明該對應關係。

科別比對 SHALL 使用 regex 而非精確比對，以涵蓋 `departments` 為「整串科別擠在單一元素」
的院所資料（多為醫學中心）。

#### Scenario: 使用者指定的科別在資料庫不存在

- **WHEN** 使用者要找「腸胃科」
- **THEN** 系統以「內科」查詢，並在回覆中說明「腸胃科」歸類於「內科」

#### Scenario: 無法解析的科別

- **WHEN** 使用者說的科別無法映射到任何部定專科
- **THEN** 系統 SHALL 回傳說明訊息並提示常見科別，SHALL NOT 退化為搜尋所有科別

### Requirement: 科別搜尋意圖觸發位置請求

系統 SHALL 將「附近的某一科」判定為鄰近院所搜尋意圖並請求位置，即使訊息不含
「醫院／診所／藥局」等字眼。

為避免將純症狀敘述誤判為找院所，此判定 SHALL 同時要求出現鄰近詞
（附近、最近、哪裡有等）與可解析的科別。

#### Scenario: 科別加鄰近詞

- **WHEN** 使用者傳送「附近有腸胃科嗎」且尚無座標
- **THEN** 系統 SHALL 呼叫 `request_location_quick_reply`

#### Scenario: 純症狀敘述

- **WHEN** 使用者傳送「我牙齒痛」
- **THEN** 系統 SHALL NOT 因此請求位置

### Requirement: 名稱查詢就近優先

依名稱查詢院所且已知使用者座標時，系統 SHALL 優先限縮在 50 公里內搜尋，
避免全台同名院所（仁愛、中山、博愛等）稀釋候選清單。

該範圍內查無結果時，系統 SHALL 自動放寬為全國搜尋，SHALL NOT 直接回傳查無資料。

#### Scenario: 生活圈內有同名院所

- **WHEN** 臺北的使用者查詢「仁愛醫院」
- **THEN** 僅回傳 50 公里內的仁愛醫院，不含外縣市同名院所

#### Scenario: 生活圈內查無，但他縣市有

- **WHEN** 高雄的使用者查詢「臺大醫院」，50 公里內無符合院所
- **THEN** 系統放寬為全國搜尋並回傳最近的臺大醫院分院

### Requirement: 科別意圖跨輪保留

系統 SHALL 回溯先前的使用者訊息取得科別與院所類型，並沿用該條件執行搜尋。原因是使用者提出需求時
通常尚無座標，系統會先請其分享位置；座標訊息進入對話後，最新的使用者訊息為系統轉出的座標文字，
只讀取最新訊息會遺失科別與類型。

回溯 SHALL 有範圍限制，避免將很久以前、已結束的需求誤套到本次搜尋。

#### Scenario: 先問科別、後分享位置

- **WHEN** 使用者先傳「附近有腸胃科嗎」，收到位置請求後分享位置
- **THEN** 系統 SHALL 呼叫 `find_nearby_facilities_by_department` 並帶入「腸胃科」，
  SHALL NOT 退化為不分科別的 `find_nearby_hospitals`

#### Scenario: 先問類型、後分享位置

- **WHEN** 使用者先傳「附近有大醫院嗎」，收到位置請求後分享位置
- **THEN** 系統 SHALL 帶入類型「醫院」執行搜尋，SHALL NOT 退化為不分類型的搜尋

#### Scenario: 需求已過時

- **WHEN** 使用者提過科別或類型後又進行多輪無關對話，才分享位置
- **THEN** 系統 SHALL NOT 沿用該條件

### Requirement: 依院所類型篩選

系統 SHALL 支援依院所類型篩選鄰近搜尋結果，類型分為醫院、診所、藥局三類。
使用者說法 SHALL 先經 `app/services/medical/facility_type_matcher.py` 映射為
資料庫 `type` 欄位的實際值集合後再查詢。

類型比對 SHALL 使用精確值集合比對（`$in`），SHALL NOT 使用子字串比對 ——
`type` 僅 17 個乾淨值且互相包含（「醫院」為「綜合醫院」「中醫醫院」的子字串），
子字串比對無法區分「大醫院」與「中醫診所」。

#### Scenario: 使用者要找大醫院

- **WHEN** 使用者傳送「附近有大醫院嗎」並提供座標
- **THEN** 結果 SHALL 僅含 `type` 為醫院、綜合醫院、精神科醫院、中醫醫院之院所，
  SHALL NOT 含任何診所

#### Scenario: 使用者要找診所

- **WHEN** 使用者傳送「附近有診所嗎」並提供座標
- **THEN** 結果 SHALL 僅含診所類院所，SHALL NOT 含醫院類院所

#### Scenario: 未指定類型

- **WHEN** 使用者傳送「附近有醫療院所嗎」，未表達類型偏好
- **THEN** 系統 SHALL NOT 套用類型過濾，行為與未加此功能前一致

### Requirement: 類型與科別可疊加

系統 SHALL 允許類型與科別兩個過濾條件同時成立，兩者為獨立維度。

#### Scenario: 大醫院的特定科別

- **WHEN** 使用者要找「附近大醫院的腸胃科」
- **THEN** 結果 SHALL 僅含醫院類且 `departments` 含「內科」之院所

#### Scenario: 科別詞隱含的類型詞

- **WHEN** 使用者要找「附近的牙醫診所」
- **THEN** 系統 SHALL 以科別「牙科」與類型「診所」同時過濾

### Requirement: 類型解析失敗處理

當使用者的類型說法無法映射到任何已知類型時，系統 SHALL 回傳說明訊息，
SHALL NOT 靜默退化為不套用類型過濾 —— 後者會使使用者誤以為系統理解了其需求。

#### Scenario: 無法解析的類型

- **WHEN** 使用者要找的院所類型無法對應到醫院、診所或藥局
- **THEN** 系統 SHALL 說明可用的類型選項

### Requirement: 藥局資料缺口的誠實回應

系統 SHALL 於使用者搜尋藥局而查無結果時，提供可行動的替代建議（如改詢問特定藥局名稱），
SHALL NOT 僅回傳與其他類型相同的通用「查無院所」訊息。原因是資料庫僅含 116 家藥局，
遠低於實際數量，此情境的失敗率遠高於其他類型。

#### Scenario: 附近查無藥局

- **WHEN** 使用者搜尋附近藥局，放寬至 50 公里仍無結果
- **THEN** 回覆 SHALL 說明藥局資料有限並建議改以名稱查詢

### Requirement: 營業狀態分級

系統 SHALL 依台灣時間將院所的當下營業狀態判定為以下之一：營業中、今日尚未開診、
午休中、今日已結束、今日休診、設有急診、請電洽、無資料。

「今日尚未開診」、「午休中」與「今日已結束」SHALL 三者分開 ——
對使用者的決定完全不同（再等等／下午再來／改天再來）。

「午休中」SHALL 僅適用於當下落在兩個時段之間（今日已有時段結束且尚有後續時段）。
第一個時段之前 SHALL 判定為「今日尚未開診」，SHALL NOT 判定為「午休中」。

#### Scenario: 時段之間的空檔

- **WHEN** 現在 13:00，該院所今日時段為 08:00–12:00 與 14:00–17:30
- **THEN** 狀態 SHALL 為「午休中」，SHALL NOT 為「今日已結束」

#### Scenario: 第一個時段之前

- **WHEN** 現在凌晨 03:00，該院所今日時段自 08:00 起
- **THEN** 狀態 SHALL 為「今日尚未開診」，SHALL NOT 為「午休中」

#### Scenario: 當日僅一個時段

- **WHEN** 該院所今日僅 09:00–17:00 一個時段，現在 08:00
- **THEN** 狀態 SHALL 為「今日尚未開診」；現在 18:00 時 SHALL 為「今日已結束」

#### Scenario: 當日最後時段已過

- **WHEN** 現在 19:00，該院所今日時段僅 08:00–12:00 與 14:00–17:30
- **THEN** 狀態 SHALL 為「今日已結束」

#### Scenario: 當日公休

- **WHEN** 該院所今日 `isClosed` 為真
- **THEN** 狀態 SHALL 為「今日休診」

#### Scenario: 無營業時間資料

- **WHEN** 該院所七天皆無任何時段資料
- **THEN** 狀態 SHALL 為「無資料」，SHALL NOT 誤判為休診

### Requirement: 下次開診時間

當院所當下非營業中時，系統 SHALL 計算並顯示下一次開診的時間，
包含跨日與跨週（最多往後查詢七天）的情形。

七天內查無任何開診時段時，SHALL 省略下次開診資訊而非顯示錯誤或空值。

#### Scenario: 當日稍後開診

- **WHEN** 現在 13:00，今日下一時段為 14:00
- **THEN** 顯示的下次開診時間 SHALL 為今日 14:00

#### Scenario: 跨日開診

- **WHEN** 現在週三 19:00，週三已結束，週四時段自 08:00 起
- **THEN** 顯示的下次開診時間 SHALL 為週四 08:00

#### Scenario: 跨週開診

- **WHEN** 現在週日 10:00，該院所僅週一至週五營業
- **THEN** 顯示的下次開診時間 SHALL 為週一的首個時段

### Requirement: 急診院所豁免營業狀態

`departments` 含急診醫學科的院所 SHALL 標示為「設有急診」，SHALL NOT 顯示「休診」類狀態。

該標示 SHALL NOT 宣稱 24 小時營業或任何具體營業時間 —— 資料庫僅記載該院所設有急診科別，
未記載急診的實際開放時間，宣稱營業時間屬於編造。

#### Scenario: 深夜搜尋急診醫院

- **WHEN** 現在凌晨 03:00，某醫院設有急診醫學科但門診時間為 08:00–17:00
- **THEN** 狀態 SHALL 顯示「設有急診」，SHALL NOT 顯示「今日已結束」或「休診」

#### Scenario: 不得宣稱營業時間

- **WHEN** 系統標示某院所設有急診
- **THEN** 文案 SHALL NOT 包含「24 小時」等未經資料證實的營業時間描述

### Requirement: 營業狀態不得排除急診院所

系統 SHALL NOT 因營業狀態而將設有急診醫學科的院所排除於搜尋結果之外，任何情況皆然。

理由：`clinicTime` 記載的是門診時間而非急診時間。實測 197 家設有急診的院所中，
深夜 03:00 依 `clinicTime` 判定為營業中者僅 1 家；若依此篩選，急需急診的使用者
會被告知附近沒有院所。

#### Scenario: 深夜要求只看營業中的院所

- **WHEN** 使用者於凌晨要求「附近現在有開的醫院」
- **THEN** 結果 SHALL 保留設有急診的院所

### Requirement: 揭露院所補充註記

系統 SHALL 於院所卡片顯示 `notes` 欄位原文（如有）。該欄位含使用者需要知道的資訊
（春節休診、須先電話洽詢、無急診等），目前完全未揭露。

`notes` 對狀態標籤的影響 SHALL 依是否含日期樣式分兩層處理：

- 含日期樣式（如「春節假期2／17~2／22休診」）：SHALL 僅顯示原文，SHALL NOT 變更狀態標籤。
  因該類註記綁定特定日期，全年降級會使標籤失去意義。
- 不含日期樣式（如「如需看診請先電話洽詢」）：SHALL 將狀態標籤降級為「請電洽」。

#### Scenario: 含日期的休診註記

- **WHEN** 院所 `notes` 為「春節假期2／17~2／22休診」，且 `clinicTime` 判定當下營業中
- **THEN** 狀態 SHALL 維持「營業中」，並 SHALL 同時顯示該註記原文

#### Scenario: 長期性註記

- **WHEN** 院所 `notes` 為「如需看診請先電話洽詢」
- **THEN** 狀態 SHALL 為「請電洽」

#### Scenario: 無註記

- **WHEN** 院所 `notes` 為空
- **THEN** 卡片 SHALL NOT 顯示註記區塊

### Requirement: 營業中篩選的觸發與退回

系統 SHALL 一律顯示營業狀態，但 SHALL NOT 預設以營業狀態篩選搜尋結果。
僅當使用者明確要求（如「現在有開的」「還在看診的」）時才套用篩選。

套用篩選後結果為 0 時，系統 SHALL 退回顯示最近的院所及其下次開診時間，
SHALL NOT 回覆「查無院所」。

#### Scenario: 未明確要求營業中

- **WHEN** 使用者傳送「附近有診所嗎」
- **THEN** 系統 SHALL 回傳最近的院所並標示各自狀態，SHALL NOT 篩掉休診的院所

#### Scenario: 明確要求營業中

- **WHEN** 使用者傳送「附近現在有開的診所嗎」
- **THEN** 系統 SHALL 僅回傳營業中的院所

#### Scenario: 篩選後無結果

- **WHEN** 使用者於深夜要求「附近現在有開的診所」，範圍內無任何營業中院所
- **THEN** 系統 SHALL 回傳最近的院所並附上下次開診時間，並說明目前均未開診

