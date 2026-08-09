# RAG Responses Spec

## Purpose

定義 CARE 知識庫問答（RAG）與公開網路搜尋工具的行為：何時啟用、如何附上參考來源、以及無命中或失敗時的處理。實作位於 `app/services/rag/`（`answer_service`、`web_search_service`、`retriever`）與 `app/tools/rag_tools.py`、`app/tools/web_tools.py`。
## Requirements
### Requirement: RAG 與網路搜尋工具閘門

系統 SHALL 僅在 guardrail 判定訊息與健康醫療相關（`allow_rag = True`）時，才對代理提供 `get_rag_answer` 與 `search_public_web` 工具。位置座標訊息 SHALL NOT 啟用上述工具。

#### Scenario: 非健康問題不啟用 RAG 與 web tool

- **WHEN** guardrail 判定使用者訊息與健康醫療無關
- **THEN** 該輪不提供 `get_rag_answer` 與 `search_public_web` 給代理

### Requirement: RAG 僅查知識庫

`get_rag_answer` / `RagAnswerService` SHALL 只檢索內部知識庫，SHALL NOT 在內部自動呼叫 Firecrawl 或網路 fallback。需要公開網頁補充時，SHALL 由代理另行呼叫 `search_public_web`。

#### Scenario: 知識庫無命中不觸發網路

- **WHEN** RAG 檢索未命中任何文件
- **THEN** 回傳提示請使用者換一種描述方式，且不進行網路搜尋

### Requirement: 公開網路搜尋工具

`search_public_web` SHALL 透過 `WebSearchService` 搜尋允許網域、抓取頁面、以模型生成回答，並在回答前加上「以下參考網路公開資料」，來源清單以「網路：」標示。無可用頁面、服務失敗或模型無法回答時 SHALL 回傳友善提示，而非拋出未處理例外。

#### Scenario: 網路搜尋成功附來源

- **WHEN** 代理呼叫 `search_public_web` 且找到允許網域的可用頁面
- **THEN** 回傳含「以下參考網路公開資料」的回答，並附最多 3 筆網路來源

#### Scenario: 網路搜尋服務未初始化

- **WHEN** `search_public_web` 被呼叫但服務尚未注入
- **THEN** 回傳「網路搜尋服務未初始化，請稍後再試。」而非中斷流程

### Requirement: 檢索上下文與參考來源上限

RAG 檢索 SHALL 取回最多 10 筆關聯文件，並將這 10 筆內容全部放入生成 prompt。回答最下方的「參考資料來源」SHALL 只列出最多 3 筆關聯度最高的網址。當某筆文件只有 `url` 而缺少 `source_name` 時，系統 SHALL 仍顯示該筆來源（以網址呈現），不得因缺名而遺漏。

#### Scenario: 多筆進 prompt、最多三筆來源

- **WHEN** RAG 檢索命中多筆文件（例如 10 筆）
- **THEN** 生成 prompt 包含最多 10 筆內容，且回答文字後附「參考資料來源：」並列出最多 3 筆網址

#### Scenario: 缺少來源名稱仍顯示

- **WHEN** 命中的文件只有 `url` 沒有 `source_name`
- **THEN** 該筆仍以網址形式顯示於參考來源清單中

### Requirement: 無命中與失敗處理

當知識庫查無相關資訊時，`get_rag_answer` SHALL 回傳提示請使用者換一種描述方式；當 RAG 服務尚未初始化時 SHALL 回傳可稍後再試的提示，而非拋出未處理例外。

#### Scenario: 查無資料

- **WHEN** RAG 檢索未命中任何文件
- **THEN** 回傳訊息提示使用者以不同方式描述問題

#### Scenario: 服務未初始化

- **WHEN** `get_rag_answer` 被呼叫但 RAG 服務尚未注入
- **THEN** 回傳「RAG 服務未初始化，請稍後再試。」而非中斷流程

### Requirement: Web fallback 成功後觸發知識回報

當 `RagAnswerService` 因知識庫不足（空檢索、CRAG `incorrect`、或 `ambiguous` 且 rewrite 後仍不足）而成功取得白名單網路回答時，系統 SHALL 將該次查詢與引用來源 URL 交給知識回報流程建立 pending（見 knowledge-reports）。此步驟 SHALL NOT 改變已回傳給代理的網路答案內容；觸發失敗時 SHALL 僅記錄錯誤。

#### Scenario: CRAG incorrect 網路成功後建報

- **WHEN** CRAG 評為 incorrect、web fallback 成功並附白名單來源
- **THEN** 代理仍收到網路答案，且系統建立對應 pending 知識回報

#### Scenario: 僅知識庫答案不建報

- **WHEN** 知識庫檢索充足並直接生成答案（未走 web fallback）
- **THEN** 系統不因此建立知識回報

