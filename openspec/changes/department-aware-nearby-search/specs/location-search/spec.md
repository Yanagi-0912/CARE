## ADDED Requirements

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

#### Scenario: 院所科別為髒資料

- **WHEN** 某醫學中心的 `departments` 為 `['家醫科、內科、外科、…']`（整串擠在單一元素）
- **THEN** 以「內科」搜尋時 SHALL 命中該院所

#### Scenario: 無法解析的科別

- **WHEN** 使用者說的科別無法映射到任何部定專科
- **THEN** 系統 SHALL 回傳說明訊息並提示常見科別，SHALL NOT 退化為搜尋所有科別

### Requirement: 逐級擴大搜尋範圍

鄰近院所搜尋 SHALL 於 5 公里內結果不足目標筆數（預設 5 筆）時，依 10、20、50 公里
逐級放寬，直到湊滿。50 公里為硬上限。

此分級 SHALL 同時適用於不分科別（`find_nearby_hospitals`）與依科別
（`find_nearby_facilities_by_department`）兩條路徑，兩者 SHALL NOT 使用不同的範圍策略。

#### Scenario: 5 公里內數量不足

- **WHEN** 5 公里內僅 1 家符合，20 公里內共有 5 家
- **THEN** 回傳 5 家，並說明已擴大搜尋範圍

#### Scenario: 50 公里內仍湊不滿

- **WHEN** 50 公里內僅找到 2 家符合的院所
- **THEN** SHALL 回傳該 2 家並說明僅找到 2 間，SHALL NOT 回傳「查無院所」

#### Scenario: 兩條路徑行為一致

- **WHEN** 偏遠地區使用者分別詢問「附近有醫院嗎」與「附近有腸胃科嗎」
- **THEN** 兩者皆逐級放寬至找到院所為止，SHALL NOT 出現一邊找得到、一邊查無資料

### Requirement: 誠實揭露搜尋範圍

回覆 SHALL 說明結果的實際涵蓋範圍。曾放寬範圍時，SHALL 以結果中最遠院所的實際距離
表述，SHALL NOT 以搜尋階梯的級距表述 —— 級距為 50 公里但最遠院所僅 27 公里時，
以級距表述會使使用者高估交通成本。

湊不滿目標筆數時 SHALL 改以搜尋上限表述，因該情境的重點為「已搜尋至多遠」。

#### Scenario: 擴大後最遠院所距離小於級距

- **WHEN** 搜尋放寬至 50 公里級距，但找到的院所最遠僅 27.2 公里
- **THEN** 回覆說明「最遠約 28 公里」，而非「已擴大到 50 公里」

### Requirement: 科別意圖跨輪保留

系統 SHALL 回溯先前的使用者訊息取得科別，並沿用該科別執行搜尋。原因是使用者提出科別需求時
通常尚無座標，系統會先請其分享位置；座標訊息進入對話後，最新的使用者訊息為系統轉出的座標文字，
只讀取最新訊息會遺失科別。

回溯 SHALL 有範圍限制，避免將很久以前、已結束的科別需求誤套到本次搜尋。

#### Scenario: 先問科別、後分享位置

- **WHEN** 使用者先傳「附近有腸胃科嗎」，收到位置請求後分享位置
- **THEN** 系統 SHALL 呼叫 `find_nearby_facilities_by_department` 並帶入「腸胃科」，
  SHALL NOT 退化為不分科別的 `find_nearby_hospitals`

#### Scenario: 科別需求已過時

- **WHEN** 使用者提過科別後又進行多輪無關對話，才分享位置
- **THEN** 系統 SHALL NOT 沿用該科別

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

## MODIFIED Requirements

### Requirement: 查無院所處理

當放寬至 50 公里仍查無醫療院所時，系統 SHALL 回傳明確的無結果訊息，而非空白或錯誤，
並 SHALL 提供緊急聯絡方式。訊息 SHALL NOT 宣稱功能仍在建置中。

#### Scenario: 50 公里內沒有院所

- **WHEN** `find_nearby_hospitals` 放寬至 50 公里後結果仍為空
- **THEN** 回傳說明已搜尋範圍的提示訊息，並提醒緊急時撥打 119
