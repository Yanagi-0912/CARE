## ADDED Requirements

### Requirement: 白名單網頁入庫

系統 SHALL 提供可程式化的入庫能力，將單一公開網頁寫入既有 Mongo 向量 collection。入庫前 SHALL 以既有白名單（`is_allowed_url`）驗證 URL；非允許網域 SHALL NOT 抓取或寫入。抓取 SHALL 重用既有 web client（Firecrawl scrape）。文字 SHALL 切成一或多個 chunk，並以與查詢路徑相同的 embedding 模型／維度產生向量後寫入。寫入文件 SHALL 至少包含文字欄（`MONGODB_TEXT_FIELD`）、向量欄（`MONGODB_VECTOR_FIELD`）、`source_name`、`url`。

#### Scenario: 白名單 URL 成功入庫

- **WHEN** 呼叫入庫並傳入允許網域的 URL，且抓取得到非空正文
- **THEN** 系統寫入一筆以上 chunk，每筆含文字、embedding、url，以及可用的 source_name（標題或呼叫端提供）

#### Scenario: 非白名單拒絕

- **WHEN** URL 不在允許網域
- **THEN** 系統不抓取、不寫入，並回傳可觀測的拒絕結果或錯誤

#### Scenario: 抓取無內文

- **WHEN** URL 通過白名單但 scrape 結果為空
- **THEN** 系統不寫入任何文件

### Requirement: 同 URL 可重跑取代

對同一 `url` 再次入庫時，系統 SHALL 以新產生的 chunk 集合取代該 url 既有文件（先準備齊全再替換），避免重複累積舊 chunk。

#### Scenario: 重跑同一 URL

- **WHEN** 同一允許 URL 成功入庫第二次
- **THEN** collection 中該 url 的文件數等於本次新 chunk 數，而非舊＋新加總

### Requirement: 營運 CLI

系統 SHALL 提供腳本，讓操作者在人工確認來源後以命令列觸發單一 URL 入庫。腳本 SHALL 支援 dry-run（只報告將寫入的 chunk 數／預覽，不寫 Mongo）。

#### Scenario: dry-run 不寫庫

- **WHEN** 以 dry-run 執行入庫腳本且 URL 合法
- **THEN** 輸出將產生的 chunk 資訊，且不對 Mongo 執行寫入
