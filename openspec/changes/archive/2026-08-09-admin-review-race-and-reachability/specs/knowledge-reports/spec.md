## ADDED Requirements

### Requirement: 背景 ingest 不得覆蓋期間內的其他變更

背景 ingest 寫回結果時，系統 SHALL 僅在該回報的 ingest 工作仍是本次啟動的那一份時才套用。若期間該回報已被拒絕或已被重新核准而啟動新工作，系統 SHALL 丟棄本次結果，SHALL NOT 覆寫回報的狀態、審核備註或新工作的紀錄。

背景 ingest 寫回時 SHALL 僅更新與該次 ingest 相關的欄位，SHALL NOT 以工作開始時的整份快照覆寫回報。

#### Scenario: 拒絕不被背景工作還原

- **WHEN** 回報於 ingest 進行中被拒絕，而該 ingest 隨後完成
- **THEN** 回報維持 `rejected`，拒絕時寫入的審核備註保留，ingest 結果不被套用

#### Scenario: 重新核准後舊工作的結果不生效

- **WHEN** 回報的 ingest 逾時後被重新核准並啟動新工作，稍後舊工作才完成
- **THEN** 回報反映新工作的紀錄，舊工作的結果被丟棄

### Requirement: 拒絕與進行中的 ingest 互斥

系統 SHALL 拒絕在 ingest 進行中對該回報執行拒絕，並以 409 回應。已逾時的進行中工作 SHALL NOT 阻擋拒絕。

此限制的目的是避免出現「回報已拒絕，但其來源內容已進入向量庫」的狀態；系統不具備反收錄能力。

#### Scenario: ingest 進行中不得拒絕

- **WHEN** admin 對 `ingest_job.status=running` 且未逾時的回報呼叫拒絕
- **THEN** 回傳 409，回報狀態不變

#### Scenario: 逾時後可拒絕

- **WHEN** 回報的進行中工作已超過逾時門檻
- **THEN** 拒絕正常執行

### Requirement: 核准不得清除既有審核備註

核准請求未帶 `resolution`／`reviewer_note` 時，系統 SHALL 保留回報上既有的值，SHALL NOT 將其清為空值。帶值時 SHALL 覆寫。

#### Scenario: 重試保留前次備註

- **WHEN** admin 對先前已寫入審核備註的回報重試核准，且本次未填備註
- **THEN** 既有審核備註保留不變

#### Scenario: 帶值時覆寫

- **WHEN** 核准請求帶有新的審核備註
- **THEN** 以新值取代既有值

### Requirement: 併發核准不得重複啟動 ingest

系統 SHALL 以原子操作登記 ingest 工作，使同一回報在併發核准下 SHALL NOT 啟動一份以上的背景工作。未取得登記的請求 SHALL 以 409 回應。

#### Scenario: 併發核准只有一個成功

- **WHEN** 兩個核准請求同時對同一筆 pending 回報送出
- **THEN** 僅其中一個啟動 ingest，另一個回傳 409

### Requirement: 待審列表回傳各狀態實際筆數

Admin 待審列表 SHALL 於回應中一併提供 `pending` 與 `reviewing` 的實際筆數，且 SHALL NOT 受本次查詢的 `status` 篩選或分頁參數影響，使呼叫端 SHALL NOT 需要以已載入的資料自行推算佇列規模。

#### Scenario: 篩選時仍回傳完整計數

- **WHEN** admin 以 `status=pending` 查詢待審列表
- **THEN** 回應同時包含 pending 與 reviewing 的實際筆數，reviewing 的筆數不因篩選而為零

### Requirement: 待審查詢的索引支援

系統 SHALL 為依狀態篩選並依建立時間排序的待審查詢建立對應索引，使該查詢 SHALL NOT 依賴全集合掃描與記憶體排序。

#### Scenario: 建立索引

- **WHEN** 系統初始化知識回報集合的索引
- **THEN** 存在涵蓋 `status` 與 `created_at` 的複合索引
