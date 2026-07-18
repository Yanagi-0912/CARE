## ADDED Requirements

### Requirement: 參考來源連續編號

系統在附加「參考資料來源」時，SHALL 僅對實際輸出的來源項目從 1 起連續編號。系統 SHALL NOT 使用檢索結果的原始索引作為顯示編號。跳過缺少 `url` 或重複 `url` 的項目後，剩餘來源的編號 MUST 仍為連續的 `[1]`、`[2]`、`[3]`（最多 3 筆）。

#### Scenario: 跳過無 URL 後編號連續

- **WHEN** 前 3 筆候選中有一筆缺少 `url`，其餘兩筆有有效且不重複的 `url`
- **THEN** 參考資料來源僅列出兩筆，編號為 `[1]` 與 `[2]`，不得出現單獨的 `[3]`

#### Scenario: 跳過重複 URL 後編號連續

- **WHEN** 前幾筆候選含重複 `url`，去重後剩 2 筆有效來源
- **THEN** 參考資料來源編號為 `[1]` 與 `[2]`

### Requirement: 無法回答時不附知識庫來源

當知識庫有檢索結果，但生成答案判定為無法依該內容回答（例如明確表示不知道、無法提供、找不到相關資訊）時，系統 SHALL NOT 附加知識庫的「參考資料來源」。此時系統 SHALL 依 Web Fallback 規則嘗試上網補齊（見「Web Fallback」）。

#### Scenario: 內容不足不附 KB 來源

- **WHEN** 檢索回傳至少一筆文件，但生成答案判定為無法回答
- **THEN** 回覆不包含來自知識庫的「參考資料來源」清單

### Requirement: Web Fallback

當符合下列任一條件時，`get_rag_answer` SHALL 嘗試 Web Fallback，而非僅回傳「請換方式描述」：

1. 知識庫檢索結果為空；或
2. 知識庫有結果，但生成答案判定為無法回答。

Web Fallback SHALL 使用 Firecrawl Search + Scrape，並僅保留符合白名單網域的結果。白名單 SHALL 寫死於程式碼，至少包含：`gov.tw`、`hpa.gov.tw`、`cdc.gov.tw`、`mohw.gov.tw`（網域比對時，子網域符合上述後綴者視為允許）。同一則回答 SHALL NOT 混用知識庫來源與網路來源。Web 來源成功用於回答時，系統 SHALL 在答案中註明參考網路公開資料，並在「參考資料來源」中標註來源類型為網路，且最多列出 3 筆、編號連續。Web Fallback 永久啟用，SHALL NOT 以 feature flag 關閉。

#### Scenario: 知識庫無命中改走 Web

- **WHEN** RAG 檢索未命中任何文件，且 Web Fallback 取得至少一筆白名單內來源並成功生成答案
- **THEN** 回傳含網路公開資料標註的答案，並附最多 3 筆標為網路的參考資料來源

#### Scenario: 內容不足後改走 Web

- **WHEN** 知識庫有命中但答案判定為無法回答，且 Web Fallback 成功
- **THEN** 回傳網路來源答案（含標註），且不附知識庫來源

#### Scenario: 非白名單網域被過濾

- **WHEN** Web 搜尋回傳的結果網域不在白名單內
- **THEN** 該結果不得用於生成答案或列入參考資料來源

#### Scenario: KB 與 Web 皆失敗

- **WHEN** 知識庫無可用答案，且 Web Fallback 無白名單結果或抓取／生成失敗
- **THEN** 回傳清楚說明無法回答的訊息，且 SHALL NOT 附加任何參考資料來源

## MODIFIED Requirements

### Requirement: 檢索上下文與參考來源上限

RAG 檢索 SHALL 取回最多 10 筆關聯文件，並將這 10 筆內容全部放入生成 prompt。回答最下方的「參考資料來源」SHALL 只列出最多 3 筆實際輸出的來源（編號連續，見「參考來源連續編號」）。當某筆文件只有 `url` 而缺少 `source_name` 時，系統 SHALL 仍顯示該筆來源（以網址呈現），不得因缺名而遺漏。當答案判定為無法依知識庫內容回答時，系統 SHALL NOT 附加知識庫來源（見「無法回答時不附知識庫來源」）。

#### Scenario: 多筆進 prompt、最多三筆來源

- **WHEN** RAG 檢索命中多筆文件（例如 10 筆）且答案可依知識庫內容回答
- **THEN** 生成 prompt 包含最多 10 筆內容，且回答文字後附「參考資料來源：」並列出最多 3 筆網址，編號從 1 連續

#### Scenario: 缺少來源名稱仍顯示

- **WHEN** 命中的文件只有 `url` 沒有 `source_name`，且該筆被納入實際輸出來源
- **THEN** 該筆仍以網址形式顯示於參考來源清單中

### Requirement: 無命中與失敗處理

當知識庫查無相關資訊時，`get_rag_answer` SHALL 先嘗試 Web Fallback（見「Web Fallback」）。僅當 Web Fallback 亦無法提供可用答案時，系統 SHALL 回傳無法回答的提示，且不附任何來源。當 RAG 服務尚未初始化時 SHALL 回傳可稍後再試的提示，而非拋出未處理例外。

#### Scenario: 查無資料且 Web 亦失敗

- **WHEN** RAG 檢索未命中任何文件，且 Web Fallback 無法取得可用白名單內容
- **THEN** 回傳無法回答的提示，且不附加「參考資料來源」

#### Scenario: 服務未初始化

- **WHEN** `get_rag_answer` 被呼叫但 RAG 服務尚未注入
- **THEN** 回傳「RAG 服務未初始化，請稍後再試。」而非中斷流程
