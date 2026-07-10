# RAG Responses Spec

## Purpose

定義 CARE 知識庫問答（RAG）的行為：何時啟用、如何附上參考來源、以及無命中或失敗時的處理。實作位於 `app/services/rag/`（retrieval、services、client、shared）與 `app/tools/rag_tools.py`。

## Requirements

### Requirement: RAG 工具閘門

系統 SHALL 僅在 guardrail 判定訊息與健康醫療相關（`allow_rag = True`）時，才對代理提供 `get_rag_answer` 工具。位置座標訊息 SHALL NOT 啟用 RAG。

#### Scenario: 非健康問題不啟用 RAG

- **WHEN** guardrail 判定使用者訊息與健康醫療無關
- **THEN** 該輪不提供 `get_rag_answer` 工具給代理

### Requirement: 參考來源上限與顯示

RAG 回答 SHALL 在最下方附上最多 3 筆關聯度最高的參考來源網址。當某筆文件只有 `url` 而缺少 `source_name` 時，系統 SHALL 仍顯示該筆來源（以網址呈現），不得因缺名而遺漏。

#### Scenario: 附上最多三筆來源

- **WHEN** RAG 檢索命中多筆文件
- **THEN** 回答文字後附「參考資料來源：」並列出最多 3 筆網址

#### Scenario: 缺少來源名稱仍顯示

- **WHEN** 命中的文件只有 `url` 沒有 `source_name`
- **THEN** 該筆仍以網址形式顯示於參考來源清單中

### Requirement: 無命中與失敗處理

當知識庫查無相關資訊（`RagNoHitsError`）時，`get_rag_answer` SHALL 回傳提示請使用者換一種描述方式；當 RAG 服務尚未初始化時 SHALL 回傳可稍後再試的提示，而非拋出未處理例外。

#### Scenario: 查無資料

- **WHEN** RAG 檢索未命中任何文件
- **THEN** 回傳訊息提示使用者以不同方式描述問題

#### Scenario: 服務未初始化

- **WHEN** `get_rag_answer` 被呼叫但 RAG 服務尚未注入
- **THEN** 回傳「RAG 服務未初始化，請稍後再試。」而非中斷流程
