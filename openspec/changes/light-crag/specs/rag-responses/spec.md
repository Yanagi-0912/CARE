## ADDED Requirements

### Requirement: 檢索評分不足時的回答

當知識庫已檢索到文件，但充足性分級判定不足以回答（含改寫重試後仍不足）時，`get_rag_answer` SHALL 回傳與查無資料同等級的友善提示（請使用者換描述或表示目前無法提供），SHALL NOT 附上「參考資料來源」，且 SHALL NOT 在服務內改呼叫網路搜尋。

#### Scenario: 有文件但評分不足

- **WHEN** 檢索／精排得到文件，且 CRAG 分級判定不足
- **THEN** 回傳無資料／無法提供類訊息，且回答中不含「參考資料來源」

## MODIFIED Requirements

### Requirement: 無命中與失敗處理

當知識庫查無相關資訊，或檢索內容經充足性分級判定不足以回答時，`get_rag_answer` SHALL 回傳提示請使用者換一種描述方式或表示目前無法提供；當 RAG 服務尚未初始化時 SHALL 回傳可稍後再試的提示，而非拋出未處理例外。

#### Scenario: 查無資料

- **WHEN** RAG 檢索未命中任何文件
- **THEN** 回傳訊息提示使用者以不同方式描述問題

#### Scenario: 檢索不足（CRAG）

- **WHEN** 檢索有命中但充足性分級判定不足以回答（含一次改寫後仍不足）
- **THEN** 回傳無資料／無法提供類訊息，且不附參考來源

#### Scenario: 服務未初始化

- **WHEN** `get_rag_answer` 被呼叫但 RAG 服務尚未注入
- **THEN** 回傳「RAG 服務未初始化，請稍後再試。」而非中斷流程
