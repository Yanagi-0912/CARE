## ADDED Requirements

### Requirement: 剝離無依據的 RAG 來源區塊

系統在組裝最終文字回覆時，若本輪曾呼叫 `get_rag_answer` 且該工具輸出不含參考來源標題，而代理最終文字卻含參考來源標題，系統 SHALL 移除該來源標題及其後清單，僅保留正文。

#### Scenario: 後置剝離亂編來源

- **WHEN** `get_rag_answer` 工具內容無來源標題，且代理回覆含來源標題
- **THEN** 回傳給使用者的文字已去除來源標題與後續清單
