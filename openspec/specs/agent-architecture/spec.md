# Agent Architecture Spec

## Purpose

定義 CARE 對話代理的編排方式：以 LangGraph 的原子化節點模式（atomic node pattern）串接 guardrail、agent 決策與工具執行，並定義代理可用的工具集合與最終回覆的組裝方式。實作位於 `app/services/agent/`（`agent.py`、`utils/nodes.py`、`utils/state.py`、`prompt.py`）與 `app/tools/`。

## Requirements

### Requirement: LangGraph 決策流程

系統 SHALL 以 LangGraph `StateGraph` 編排一次對話，節點固定為 `guardrail`、`agent`、`tools`，流程為 `START → guardrail → agent`，並在 `agent` 之後依 `tools_condition` 分派；當代理不需工具時 SHALL 直接進入 `END`。共享狀態（State）SHALL 至少包含 `messages` 與 `allow_rag`，且每次 `invoke` 的 `allow_rag` 初始值為 `False`。

#### Scenario: 直接回答不使用工具

- **WHEN** 使用者輸入一般訊息且代理判斷不需呼叫任何工具
- **THEN** 流程走 `START → guardrail → agent → END`，回傳代理產生的文字回覆

#### Scenario: 呼叫工具後回到代理

- **WHEN** 代理在 `agent` 節點決定呼叫工具
- **THEN** 流程進入 `tools` 節點執行該工具，完成後 SHALL 回到 `agent` 節點，讓代理根據工具結果繼續產生最終回覆

### Requirement: Guardrail 決定是否啟用 RAG

系統 SHALL 在 `guardrail` 節點以注入的「文字→bool」分類器判斷使用者訊息是否與健康醫療相關，並據此設定 `allow_rag`。Guardrail SHALL 不綁定特定模型實作（透過 DI 注入分類器）。當使用者訊息為位置座標訊息時 SHALL 快速跳過分類並禁用 RAG。當分類器發生例外時 SHALL 採 fail-open（視為允許）。

#### Scenario: 健康相關訊息

- **WHEN** 使用者訊息與健康、醫療、疾病、藥物、營養、運動或心理健康相關
- **THEN** `allow_rag` 設為 `True`，代理可使用 `get_rag_answer` 工具

#### Scenario: 位置座標訊息跳過 RAG

- **WHEN** 使用者訊息以「這是我的目前位置」開頭或包含 `lat=`
- **THEN** 直接禁用 RAG（`allow_rag = False`），不呼叫分類器

#### Scenario: 分類失敗採 fail-open

- **WHEN** 分類器呼叫發生例外
- **THEN** 記錄錯誤並回傳允許（`True`），避免暫時性錯誤阻斷使用者流程

### Requirement: 代理可用工具集

系統 SHALL 透過 `app/tools/registry.py` 的 `get_all_tools(include_rag_tool)` 組裝工具集。工具集 SHALL 固定包含 `find_nearby_hospitals` 與 `request_location_quick_reply`；`get_rag_answer` SHALL 可依 `include_rag_tool` 參數納入。工具實例 SHALL 由 `app/dependencies.py`（composition root）透過 `configure_rag_tool` / `configure_medical_tools` 注入其依賴服務。

#### Scenario: 納入 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=True)`
- **THEN** 回傳的工具集包含 `get_rag_answer`、`find_nearby_hospitals`、`request_location_quick_reply`

#### Scenario: 排除 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=False)`
- **THEN** 回傳的工具集僅包含 `find_nearby_hospitals` 與 `request_location_quick_reply`

### Requirement: 最終回覆組裝

系統 SHALL 從流程結果的最後一則 AI 訊息取得文字作為回覆，並偵測本輪是否呼叫了 `request_location_quick_reply`，於回傳結果中提供 `call_request_location` 旗標。當本輪呼叫過 `get_rag_answer` 且工具輸出含「參考資料來源：」但最終回覆遺漏時，系統 SHALL 以防禦性後置處理自動補回參考資料來源段落。

#### Scenario: 回覆遺漏參考來源時自動補回

- **WHEN** 本輪呼叫過 `get_rag_answer`，其輸出含「參考資料來源：」，但代理最終回覆未包含該段
- **THEN** 系統將工具輸出中的「參考資料來源：」段落附加到最終回覆末端

#### Scenario: 觸發位置快速回覆旗標

- **WHEN** 本輪呼叫過 `request_location_quick_reply`
- **THEN** 回傳結果的 `call_request_location` 為 `True`
