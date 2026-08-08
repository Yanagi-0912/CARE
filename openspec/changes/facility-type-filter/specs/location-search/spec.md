## ADDED Requirements

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

## MODIFIED Requirements

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
