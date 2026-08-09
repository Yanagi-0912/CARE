## ADDED Requirements

### Requirement: 上傳文件問答工具納入代理

系統 SHALL 透過工具註冊將「依使用者上傳文件回答」的工具提供給代理（至少在健康／衛教相關回合可使用）。System prompt SHALL 指示：與使用者先前上傳文件內容相關的問題優先呼叫該工具；一般官方衛教知識仍使用 `get_rag_answer`。

#### Scenario: 工具集包含上傳文件問答

- **WHEN** 代理本輪可使用 RAG 相關工具
- **THEN** 工具集包含上傳文件問答工具（名稱以實作為準，例如 `answer_from_uploaded_document`）

#### Scenario: 媒體上傳回合不強制官方 RAG

- **WHEN** 使用者訊息為媒體抽出前綴格式
- **THEN** 系統仍 SHALL NOT 強制注入 `get_rag_answer`（維持既有 skip）；上傳 ingest 於背景／抽字後執行
