## MODIFIED Requirements

### Requirement: Admin 可核准或拒絕回報

Admin SHALL 能對選定回報挑選要收錄的來源 URL 後核准，或直接拒絕，並在成功後更新列表狀態。

審核介面 SHALL 將回報的 `user_source_urls` 呈現為可逐一勾選的項目，預設全選。核准請求 SHALL 只送出被勾選的 URL。當沒有任何 URL 被勾選時，介面 SHALL 停用核准動作，SHALL NOT 送出空的選取讓後端回退成全選。

使用者回報時 SHALL NOT 被要求附上來源 URL，因此審核介面 MUST 允許 admin 自行補上來源 URL，並與使用者提供的來源併入同一份勾選清單。無來源 URL 的回報 SHALL NOT 因此變成只能拒絕。admin 補上的 URL 仍 SHALL 受後端白名單約束，未通過時介面 SHALL 顯示後端回傳的原因。

#### Scenario: 挑選部分 URL 核准

- **WHEN** admin 取消勾選部分來源 URL 後按下核准
- **THEN** 前端僅以勾選中的 URL 送出 approve，未勾選者不進入 ingest

#### Scenario: 未選任何 URL

- **WHEN** admin 取消勾選全部來源 URL
- **THEN** 核准動作為停用狀態，不送出請求

#### Scenario: 為無來源的回報補上 URL

- **WHEN** admin 對 `user_source_urls` 為空的回報輸入一個來源 URL 並加入
- **THEN** 該 URL 出現在勾選清單且為勾選狀態，核准動作恢復可用，核准時以該 URL 送出

#### Scenario: 補上的 URL 未通過白名單

- **WHEN** admin 補上非白名單網域的 URL 並核准
- **THEN** 介面顯示後端回傳的白名單錯誤，回報狀態不變

#### Scenario: 拒絕回報

- **WHEN** admin 按下拒絕
- **THEN** 前端呼叫 reject API，成功後該筆自待審列表移除或狀態更新

## ADDED Requirements

### Requirement: Admin 可檢視 ingest 狀態並重試

審核介面 SHALL 顯示回報的 ingest 工作狀態，使 admin 能區分「ingest 進行中」與「ingest 已失敗」，SHALL NOT 讓兩者在待審佇列中呈現為相同外觀。

ingest 失敗時，介面 SHALL 顯示工作層級的錯誤訊息與逐一 URL 的處理結果（狀態與訊息），並 SHALL 提供重試動作。重試 SHALL 沿用核准端點，並帶入當前勾選的 URL。後端因工作進行中而拒絕重試時，介面 SHALL 顯示該錯誤而非視為成功。

#### Scenario: 顯示 ingest 失敗原因

- **WHEN** admin 開啟 ingest 失敗的回報詳情
- **THEN** 介面顯示失敗訊息與各 URL 的處理結果

#### Scenario: 重試失敗的 ingest

- **WHEN** admin 於失敗的回報按下重試
- **THEN** 前端以勾選中的 URL 呼叫核准端點，成功後重新載入佇列

#### Scenario: 重試遇工作進行中

- **WHEN** 後端以 409 回應重試請求
- **THEN** 介面顯示錯誤訊息，佇列狀態不變

### Requirement: 待審佇列分頁載入

審核介面 SHALL 分頁載入待審佇列而非一次取回全部，並 SHALL 呈現符合條件的總筆數。尚有未載入資料時，介面 SHALL 提供載入更多的動作；審核操作成功後 SHALL 重新載入第一頁。

#### Scenario: 載入更多

- **WHEN** 佇列總筆數大於已載入筆數且 admin 觸發載入更多
- **THEN** 介面附加下一頁回報，既有項目不重複

#### Scenario: 全部載入完畢

- **WHEN** 已載入筆數等於總筆數
- **THEN** 介面不提供載入更多動作
