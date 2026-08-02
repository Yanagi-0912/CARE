## MODIFIED Requirements

### Requirement: 保留參考資料來源

當回覆基於 `get_rag_answer` 且工具輸出含參考來源標題與清單時，系統 SHALL 在回覆最下方完整保留該標題、編號與網址，且 SHALL NOT 修改網址或改以 Markdown 連結呈現。

當工具輸出**不含**參考來源標題時，系統 SHALL NOT 在最終回覆中新增來源標題或網址清單（不得捏造來源）。

#### Scenario: 保留來源清單

- **WHEN** 回覆使用了 `get_rag_answer` 且工具輸出含參考來源段落
- **THEN** 回覆末端完整包含該來源段落，網址以純文字原樣顯示

#### Scenario: 無真實來源時不捏造

- **WHEN** 本輪呼叫了 `get_rag_answer` 但工具輸出不含參考來源標題
- **THEN** 最終回覆不含參考來源標題與網址清單
