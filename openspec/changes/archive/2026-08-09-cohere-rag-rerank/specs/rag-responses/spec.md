## MODIFIED Requirements

### Requirement: 檢索上下文與參考來源上限

RAG 檢索 SHALL 先取回最多 `RAG_RETRIEVE_CANDIDATES` 筆關聯文件作為候選（預設 40），經精排（見 `rag-reranking`）後 SHALL 將最多 `RAG_RERANK_TOP_N` 筆（預設 5）內容放入生成 prompt。回答最下方的「參考資料來源」SHALL 只列出最多 3 筆、且依**精排後順位**（降級時則依向量分數順位）最高的網址。當某筆文件只有 `url` 而缺少 `source_name` 時，系統 SHALL 仍顯示該筆來源（以網址呈現），不得因缺名而遺漏。

#### Scenario: 精排後進 prompt、最多三筆來源

- **WHEN** RAG 向量檢索命中多筆文件且完成精排（或降級排序）
- **THEN** 生成 prompt 包含最多 `RAG_RERANK_TOP_N` 筆內容，且回答文字後附「參考資料來源：」並列出最多 3 筆網址，來源順序與精排（或降級）順位一致

#### Scenario: 缺少來源名稱仍顯示

- **WHEN** 命中的文件只有 `url` 沒有 `source_name`
- **THEN** 該筆仍以網址形式顯示於參考來源清單中
