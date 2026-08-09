## ADDED Requirements

### Requirement: 無法回答啟發式不得誤殺可用答案

系統用來判定生成內容「無法回答」的啟發式 SHALL NOT 使用過於寬泛、會匹配一般敘事的單一標記（例如單獨的「無法」）。啟發式 SHALL 使用足以表達拒答意圖的片語。當生成內容為可用衛教且僅因敘述出現「無法透過加熱破壞」這類非拒答用法時，系統 SHALL NOT 回傳 `MODEL_REFUSE`。

#### Scenario: 河魨毒素敘述不被誤殺

- **WHEN** 生成答案含「無法透過加熱破壞」且其餘內容為可用衛教說明
- **THEN** 系統不以 MODEL_REFUSE 丟棄該答案

#### Scenario: 明確拒答片語仍攔截

- **WHEN** 生成答案含「無法提供」或「我不知道」等拒答意圖片語
- **THEN** 系統仍回傳 MODEL_REFUSE
