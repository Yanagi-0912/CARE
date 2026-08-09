## MODIFIED Requirements

### Requirement: 檢索上下文與參考來源上限

RAG 檢索 SHALL 先取回最多 `RAG_RETRIEVE_CANDIDATES` 筆關聯文件作為候選（預設 40），經精排後 SHALL 將最多 `RAG_RERANK_TOP_N` 筆（預設 5）內容放入生成 prompt，且每筆 SHALL 帶有編號與出處標頭（來源名與標題）。回答最下方的「參考資料來源」SHALL 只列出**實際被引用**的來源，最多 3 筆，依首次引用順序連續重編號。當某筆來源缺少 `url` 時，系統 SHALL 以「來源名｜標題」呈現，不得因缺 url 而靜默丟棄。當模型未輸出任何引用編號時，系統 SHALL NOT 附上參考來源清單。

#### Scenario: 只列出實際被引用的來源

- **WHEN** 生成的答案引用了第 3 筆與第 1 筆內容
- **THEN** 參考來源只列這兩筆，依首次引用順序重編為 [1]、[2]，且答案內文中的編號一併改寫為對應的新編號

#### Scenario: 缺少 url 的來源仍顯示

- **WHEN** 被引用的文件有 `source_name` 與 `original_title` 但 `url` 為空
- **THEN** 該筆以「來源名｜標題」形式列於參考來源清單中

#### Scenario: 完全沒有引用時不附來源

- **WHEN** 生成的答案不含任何引用編號
- **THEN** 回覆不附「參考資料來源：」段落，並記錄 `citation_missing` log
