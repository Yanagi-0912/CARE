## ADDED Requirements

### Requirement: Admin 待審列表的參數驗證與分頁

Admin 待審列表端點的 `status` 參數 SHALL 僅接受合法的回報狀態值（`pending`／`reviewing`／`resolved`／`rejected`）；非法值 SHALL 以 422 拒絕，SHALL NOT 以空列表回應。

該端點 SHALL 支援分頁：`limit` 預設 50、下限 1、上限 200，超出範圍 SHALL 以 422 拒絕；`offset` 預設 0。回應 SHALL 除回報列表外一併提供符合篩選條件的總筆數與本次的 `limit`／`offset`，使呼叫端能判斷是否還有未載入的資料。未帶分頁參數的呼叫 SHALL 回傳第一頁。

使用者端的個人回報列表 SHALL NOT 受本需求影響。

#### Scenario: 非法 status 回 422

- **WHEN** admin 以 `status=foo` 查詢待審列表
- **THEN** 回傳 422，不查詢資料庫

#### Scenario: 預設分頁

- **WHEN** admin 未帶分頁參數查詢待審列表
- **THEN** 回傳最多 50 筆，並附上符合條件的總筆數與 `limit=50`／`offset=0`

#### Scenario: 指定分頁位移

- **WHEN** admin 以 `limit=20&offset=20` 查詢
- **THEN** 回傳依建立時間新到舊排序的第 21～40 筆，總筆數不受分頁影響

#### Scenario: 超出上限的 limit

- **WHEN** admin 以 `limit=500` 查詢
- **THEN** 回傳 422
