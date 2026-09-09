## MODIFIED Requirements

### Requirement: 科別搜尋意圖觸發位置請求

系統 SHALL 將「附近的某一科」判定為鄰近院所搜尋意圖並請求位置，即使訊息不含
「醫院／診所／藥局」等字眼。

為避免將純症狀敘述誤判為找院所，此判定 SHALL 同時要求出現鄰近詞
（附近、最近、哪裡有等）與可解析的科別。

症狀敘述搭配掛號科別意圖（例如「我肚子好痛要掛哪一科」）SHALL NOT 視為找院所
意圖，SHALL NOT 因此請求位置。該類問句由 `suggest_department_for_symptom` 處理；
使用者於取得建議後若接續表達鄰近搜尋意圖，SHALL 依「科別意圖跨輪保留」以建議
的部定專科執行搜尋。

#### Scenario: 科別加鄰近詞

- **WHEN** 使用者傳送「附近有腸胃科嗎」且尚無座標
- **THEN** 系統 SHALL 呼叫 `request_location_quick_reply`

#### Scenario: 純症狀敘述

- **WHEN** 使用者傳送「我牙齒痛」
- **THEN** 系統 SHALL NOT 因此請求位置

#### Scenario: 症狀加掛號意圖不請求位置

- **WHEN** 使用者傳送「我肚子好痛要掛哪一科」
- **THEN** 系統 SHALL NOT 請求位置，SHALL 回傳科別建議

#### Scenario: 取得建議後接續找院所

- **WHEN** 使用者於取得「內科」建議後傳送「附近有嗎」
- **THEN** 系統 SHALL 請求位置，並於取得座標後以「內科」呼叫
  `find_nearby_facilities_by_department`
