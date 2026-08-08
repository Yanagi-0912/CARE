## MODIFIED Requirements

### Requirement: 座標進入對話時依科別決定搜尋工具

系統 SHALL 於使用者訊息為座標文字（「這是我的目前位置：lat=…, lng=…」）且模型未主動呼叫工具時，
強制注入院所搜尋工具呼叫，以避免代理回傳空內容或將座標文字當成 RAG 查詢送出。

注入哪一個工具 SHALL 依對話歷史中是否存在科別需求決定：有科別則注入
`find_nearby_facilities_by_department`（並帶入使用者的原始說法），否則注入 `find_nearby_hospitals`。

若歷史中存在院所類型需求（大醫院、診所、藥局），SHALL 一併帶入 `facility_type` 參數，
與科別獨立判斷 —— 兩者可同時存在，亦可只有其中之一。

#### Scenario: 歷史中有科別需求

- **WHEN** 使用者先傳「附近有腸胃科嗎」，本輪傳送座標，且模型未產生 tool_calls
- **THEN** 系統注入 `find_nearby_facilities_by_department`，`args` 含 `lat`、`lng` 與 `department="腸胃科"`

#### Scenario: 歷史中僅有類型需求

- **WHEN** 使用者先傳「附近有大醫院嗎」，本輪傳送座標，且模型未產生 tool_calls
- **THEN** 系統注入 `find_nearby_hospitals`，`args` 含 `lat`、`lng` 與 `facility_type="大醫院"`

#### Scenario: 歷史中同時有科別與類型需求

- **WHEN** 使用者先傳「附近大醫院的腸胃科」，本輪傳送座標，且模型未產生 tool_calls
- **THEN** 系統注入 `find_nearby_facilities_by_department`，`args` 同時含
  `department="腸胃科"` 與 `facility_type="大醫院"`

#### Scenario: 歷史中無科別與類型需求

- **WHEN** 使用者先傳「附近有醫院嗎」，本輪傳送座標，且模型未產生 tool_calls
- **THEN** 系統注入 `find_nearby_hospitals`，`args` 僅含 `lat` 與 `lng`
