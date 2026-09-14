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

### Requirement: 依科別搜尋鄰近院所

當使用者指定科別（例如腸胃科、牙科、中醫）並已取得座標時，代理 SHALL 呼叫
`find_nearby_facilities_by_department(lat, lng, departments)`。`departments` 為科別清單，
每個元素傳入使用者對一個科別的原始說法；使用者一次列出多科時（例如保底卡按鈕的
「家醫科、內科、不分科」），SHALL 以一次呼叫帶入全部科別，SHALL NOT 拆成多次呼叫。

資料庫 `departments` 僅含衛福部部定專科（55 個值），不含次專科，因此系統 SHALL 先經
`app/services/medical/department_matcher.py` 將使用者說法映射為部定專科後再查詢。
當映射結果與使用者說法不同時，回覆 SHALL 明確說明該對應關係。

多個科別 SHALL 以單一查詢完成：院所具備其中任一科即命中，結果依距離排序。映射到
同一部定專科的說法 SHALL 只查詢一次。部分科別無法映射時，系統 SHALL 以可映射的科別
查詢，並於回覆中說明哪些科別未被搜尋；全部無法映射時比照「無法解析的科別」處理。

科別比對 SHALL 使用 regex 而非精確比對，以涵蓋 `departments` 為「整串科別擠在單一元素」
的院所資料（多為醫學中心）。

#### Scenario: 使用者指定的科別在資料庫不存在

- **WHEN** 使用者要找「腸胃科」
- **THEN** 系統以「內科」查詢，並在回覆中說明「腸胃科」歸類於「內科」

#### Scenario: 無法解析的科別

- **WHEN** 使用者說的科別全部無法映射到任何部定專科
- **THEN** 系統 SHALL 回傳說明訊息並提示常見科別，SHALL NOT 退化為搜尋所有科別

#### Scenario: 一次搜尋多個科別

- **WHEN** 使用者點選保底卡按鈕「搜尋附近的家醫科、內科、不分科」並分享位置
- **THEN** 系統 SHALL 以單一查詢搜尋具備家醫科、內科或不分科任一者的院所，
  結果標題列出三科

#### Scenario: 部分科別無法解析

- **WHEN** 使用者要找「家醫科」與一個無法映射的科別
- **THEN** 系統 SHALL 以家醫科查詢，並於回覆中說明另一科無法對應、未被搜尋

### Requirement: 科別意圖跨輪保留

系統 SHALL 回溯先前的使用者訊息取得科別與院所類型，並沿用該條件執行搜尋。原因是使用者提出需求時
通常尚無座標，系統會先請其分享位置；座標訊息進入對話後，最新的使用者訊息為系統轉出的座標文字，
只讀取最新訊息會遺失科別與類型。

同一則訊息以連接詞列舉多個科別時（「家醫科、內科、不分科」「內科或家醫科」），SHALL 全部
沿用；同一句中僅是順帶提及的科別（「我在內科看過了，附近有皮膚科嗎」的內科）SHALL NOT 納入。

回溯 SHALL 有範圍限制，避免將很久以前、已結束的需求誤套到本次搜尋。

#### Scenario: 先問科別、後分享位置

- **WHEN** 使用者先傳「附近有腸胃科嗎」，收到位置請求後分享位置
- **THEN** 系統 SHALL 呼叫 `find_nearby_facilities_by_department` 並帶入「腸胃科」，
  SHALL NOT 退化為不分科別的 `find_nearby_hospitals`

#### Scenario: 先點保底卡按鈕、後分享位置

- **WHEN** 使用者點選「搜尋附近的家醫科、內科、不分科」，收到位置請求後分享位置
- **THEN** 系統 SHALL 以一次 `find_nearby_facilities_by_department` 呼叫帶入三科，
  SHALL NOT 只帶其中一科

#### Scenario: 先問類型、後分享位置

- **WHEN** 使用者先傳「附近有大醫院嗎」，收到位置請求後分享位置
- **THEN** 系統 SHALL 帶入類型「醫院」執行搜尋，SHALL NOT 退化為不分類型的搜尋
