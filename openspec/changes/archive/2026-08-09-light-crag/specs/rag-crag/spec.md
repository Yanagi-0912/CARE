## ADDED Requirements

### Requirement: 檢索充足性分級

系統在知識庫 RAG 於精排（或向量降級 top-n）完成後、生成回答前，SHALL 對「使用者問題 + 候選文件」執行充足性分級，結果為 `correct`、`ambiguous` 或 `incorrect` 之一。

#### Scenario: 充足則生成

- **WHEN** 分級結果為 `correct`
- **THEN** 系統以該批候選文件生成回答並依既有規則附上參考來源

#### Scenario: 不足則不生成

- **WHEN** 分級結果為 `incorrect`，或精排後無可用文件
- **THEN** 系統 SHALL NOT 以該批文件生成知識庫答案

> 不足之後對使用者回應什麼，由 `rag-responses` 規定，不屬本 capability 的範圍。
> 本 capability 只規定「分級與改寫」這套機制本身。

### Requirement: 模糊時最多一次改寫重試

當分級結果為 `ambiguous` 時，系統 SHALL 至多產生一次改寫後的查詢，並重新執行檢索→精排→分級。若第二次仍非 `correct`，SHALL 依不足路徑處置，且 SHALL NOT 再發動第三次改寫。

#### Scenario: 改寫後變充足

- **WHEN** 首次分級為 `ambiguous`，改寫查詢後第二次分級為 `correct`
- **THEN** 系統以第二次候選文件生成回答

#### Scenario: 改寫後仍不足

- **WHEN** 首次分級為 `ambiguous`，且第二次分級仍非 `correct`
- **THEN** 系統依不足路徑處置，且不得再發動第三次改寫

### Requirement: Grader 失敗時降級

當充足性分級或 query 改寫呼叫失敗（逾時、模型例外、結構化輸出錯誤）時，系統 SHALL NOT 中斷整條 RAG；SHALL 降級為略過 CRAG、直接以當前候選文件生成（與未啟用 CRAG 行為一致），並 SHALL 留下可觀測的降級日誌。

#### Scenario: 分級服務失敗

- **WHEN** grader 拋出例外
- **THEN** 系統仍嘗試以既有精排結果生成回答，並留下可觀測的降級日誌

#### Scenario: 改寫服務失敗

- **WHEN** 分級為 `ambiguous` 但 query 改寫拋出例外
- **THEN** 系統仍以首次的候選文件生成回答，並留下可觀測的降級日誌
