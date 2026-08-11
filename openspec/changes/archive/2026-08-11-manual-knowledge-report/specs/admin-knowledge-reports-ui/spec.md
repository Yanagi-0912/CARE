## MODIFIED Requirements

### Requirement: Admin 可核准或拒絕回報

Admin SHALL 能對選定回報挑選要收錄的來源 URL 後核准，或直接拒絕，並在成功後更新列表狀態。

審核介面 SHALL 將回報的 `user_source_urls` 呈現為可逐一勾選的項目，預設全選。核准請求 SHALL 只送出被勾選的 URL。當沒有任何 URL 被勾選時，介面 SHALL 停用核准動作，SHALL NOT 送出空的選取讓後端回退成全選。

使用者透過手動回報表單送出的回報 SHALL 已附上至少一個通過白名單的來源 URL。但仍會存在無來源 URL 的回報——本需求生效前建立的舊資料，以及 agent tool 路徑建立的回報（該路徑的來源 URL 維持選填）。因此審核介面 MUST 保留讓 admin 自行補上來源 URL 的能力，並與使用者提供的來源併入同一份勾選清單。無來源 URL 的回報 SHALL NOT 因此變成只能拒絕。admin 補上的 URL 仍 SHALL 受後端白名單約束，未通過時介面 SHALL 顯示後端回傳的原因。

審核介面 SHALL 呈現回報的建立來源（手動表單／agent tool／web fallback），使 admin 能區分「使用者親手貼上的網址」與「語言模型代填的網址」。後者可能為模型生成而非實際存在的頁面，即使通過白名單亦然。來源不明的舊資料 SHALL NOT 因缺少此標記而無法審核。

#### Scenario: 挑選部分 URL 核准

- **WHEN** admin 取消勾選部分來源 URL 後按下核准
- **THEN** 前端僅以勾選中的 URL 送出 approve，未勾選者不進入 ingest

#### Scenario: 未選任何 URL

- **WHEN** admin 取消勾選全部來源 URL
- **THEN** 核准動作為停用狀態，不送出請求

#### Scenario: 為無來源的回報補上 URL

- **WHEN** admin 對 `user_source_urls` 為空的回報輸入一個來源 URL 並加入
- **THEN** 該 URL 出現在勾選清單且為勾選狀態，核准動作恢復可用，核准時以該 URL 送出

#### Scenario: agent tool 回報仍可由 admin 補 URL

- **WHEN** admin 開啟一筆由 agent tool 建立、無來源 URL 的回報
- **THEN** 補上 URL 的介面仍可用，該回報不因缺少來源而只能拒絕

#### Scenario: 補上的 URL 未通過白名單

- **WHEN** admin 補上非白名單網域的 URL 並核准
- **THEN** 介面顯示後端回傳的白名單錯誤，回報狀態不變

#### Scenario: 標示代理提供的來源

- **WHEN** admin 開啟一筆建立來源為 agent tool 的回報
- **THEN** 介面標示其來源，使 admin 知悉該網址由語言模型提供

#### Scenario: 拒絕回報

- **WHEN** admin 按下拒絕
- **THEN** 前端呼叫 reject API，成功後該筆自待審列表移除或狀態更新
