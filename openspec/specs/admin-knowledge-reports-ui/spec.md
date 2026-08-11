# admin-knowledge-reports-ui Specification

## Purpose
TBD - created by archiving change admin-knowledge-reports-liff. Update Purpose after archive.
## Requirements
### Requirement: Admin 可檢視待審知識回報

已登入且 `role=admin` 的使用者 SHALL 可開啟 LIFF Admin 知識回報頁，載入 `GET /api/admin/knowledge-reports` 的 pending／reviewing 佇列，並檢視問題、原因、備註與來源 URL。

#### Scenario: Admin 載入佇列

- **WHEN** admin 開啟審核頁
- **THEN** 系統顯示待審回報列表（可依 pending／reviewing 篩選）

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

### Requirement: 非 Admin 不得進入審核頁

非 admin 使用者 SHALL NOT 使用 Admin 審核頁；前端 MUST 阻擋進入，後端 API 仍以 403 為最終防護。

#### Scenario: 一般使用者開啟審核路由

- **WHEN** `role` 不為 admin 的使用者導向審核頁
- **THEN** 不顯示審核操作，並導離或顯示無權限

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

### Requirement: 篩選後的佇列資料完整可達

佇列篩選 SHALL 由後端執行，SHALL NOT 只對已載入的頁面過濾。套用篩選後，符合條件但尚未載入的回報 SHALL 仍可透過載入更多取得。

當符合篩選條件的資料尚未全部載入時，介面 SHALL NOT 呈現「沒有待審回報」之類的空佇列結論。

篩選頁籤與佇列統計顯示的筆數 SHALL 來自後端回傳的實際筆數，SHALL NOT 由已載入的頁面自行計算——後者只反映已載入的部分。

#### Scenario: 第一頁無命中仍可取得後續資料

- **WHEN** admin 切換到某個狀態篩選，而已載入的第一頁沒有該狀態的回報，但後端仍有符合的資料
- **THEN** 介面不呈現空佇列結論，且可繼續載入取得那些回報

#### Scenario: 切換篩選重新分頁

- **WHEN** admin 切換篩選
- **THEN** 佇列以新條件從第一頁重新載入，載入更多對應新的條件

### Requirement: 重試沿用上次送出的 URL

重新開啟含 ingest 工作紀錄的回報時，介面 SHALL 以該工作實際送出的 URL 作為預設選取內容，SHALL NOT 只依 `user_source_urls` 種入。其中不屬於使用者提供的 URL SHALL 標示為審核者補充。

此需求確保 admin 為無來源回報手動補上的 URL 在重試時不需重新輸入。

#### Scenario: 重試無來源回報

- **WHEN** admin 重新開啟一筆 `user_source_urls` 為空、但曾由 admin 補上 URL 並 ingest 失敗的回報
- **THEN** 該 URL 出現在選取清單且為勾選狀態，重試動作可用

### Requirement: ingest 進行中自動更新

任一已載入回報的 ingest 仍在進行中時，介面 SHALL 自動重新取得佇列，使 ingest 完成後的狀態 SHALL NOT 需要使用者手動重新整理才會呈現。沒有進行中的工作時 SHALL 停止自動重新取得。

#### Scenario: 收錄完成自動反映

- **WHEN** 佇列中有 ingest 進行中的回報，且該工作隨後完成
- **THEN** 介面在無使用者操作的情況下更新為完成後的狀態

#### Scenario: 無進行中工作時不輪詢

- **WHEN** 已載入的回報都沒有進行中的 ingest
- **THEN** 介面不再定期重新取得佇列

### Requirement: 分頁結果不得出現重複回報

合併分頁結果時，系統 SHALL 依回報編號去除重複，使佇列 SHALL NOT 因分頁邊界的資料變動而重複呈現同一筆回報。

#### Scenario: 邊界重複被去除

- **WHEN** 後續頁面回傳了已在前一頁出現過的回報
- **THEN** 佇列只呈現該回報一次

