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

### Requirement: 查無院所處理

當座標附近查無醫療院所時，系統 SHALL 回傳明確的無結果訊息，而非空白或錯誤。

#### Scenario: 附近沒有院所

- **WHEN** `find_nearby_hospitals` 查詢結果為空
- **THEN** 回傳「查無院所」類型的提示訊息
