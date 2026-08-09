## ADDED Requirements

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
