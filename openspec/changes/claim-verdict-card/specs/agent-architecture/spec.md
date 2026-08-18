## MODIFIED Requirements

### Requirement: 代理工具集

系統 SHALL 於 guardrail 判定訊息與健康醫療相關（`allow_rag = True`）時，對代理提供知識庫相關工具。工具集 SHALL 包含 `verify_claim` 與 `get_rag_answer` 兩者，由代理依問句形態自行選擇——該選擇即為查核型與衛教型的分流，系統 SHALL NOT 另設獨立的意圖分類步驟。

`CLAIM_VERIFICATION_ENABLED` 為 false 時，工具集 SHALL NOT 包含 `verify_claim`，其餘行為不變。

#### Scenario: 查核型問句

- **WHEN** 使用者問「網傳 X 是真的嗎」且 guardrail 放行
- **THEN** 工具集含 `verify_claim`，代理可選用之

#### Scenario: 衛教型問句不受影響

- **WHEN** 使用者問「糖尿病可以吃水果嗎」
- **THEN** 代理選用 `get_rag_answer`，行為與本 change 之前完全相同

#### Scenario: 功能關閉時回到原行為

- **WHEN** `CLAIM_VERIFICATION_ENABLED` 為 false
- **THEN** 工具集不含 `verify_claim`
