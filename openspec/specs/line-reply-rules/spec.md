# LINE Reply Rules Spec

## Purpose

定義 CARE 對外（LINE 通道）回覆的內容格式規則，確保在 LINE 上呈現乾淨、可讀且不含 Markdown 的純文字。規則來源為系統提示（`app/services/agent/prompt.py` 的 `SYSTEM_PROMPT`）與回覆組裝流程。

## Requirements

### Requirement: 一律使用繁體中文純文字

系統對使用者的回覆 SHALL 只使用繁體中文，且 SHALL 為純文字與一般換行。系統 SHALL NOT 輸出任何 Markdown 語法，包含 `**粗體**`、`# 標題`、以及 `[文字](網址)` 形式的連結。

#### Scenario: 一般回覆不含 Markdown

- **WHEN** 代理產生任何要送往 LINE 的回覆
- **THEN** 回覆內容為純文字，不含粗體、標題或 Markdown 連結語法；網址以純文字直接顯示

### Requirement: RAG 回覆前綴

當且僅當本輪實際呼叫了 `get_rag_answer` 並依其內容作答時，回覆的首行 SHALL 為「以下為 RAG 回應：」。使用 `find_nearby_hospitals` 產出的院所清單或一般對話 SHALL NOT 加入此前綴。

#### Scenario: 使用 RAG 作答

- **WHEN** 本輪呼叫 `get_rag_answer` 並引用其回傳內容作答
- **THEN** 回覆首行為「以下為 RAG 回應：」

#### Scenario: 位置搜尋或一般對話

- **WHEN** 本輪僅使用 `find_nearby_hospitals`、`request_location_quick_reply`，或為一般對話
- **THEN** 回覆不加入「以下為 RAG 回應：」前綴

### Requirement: 保留參考資料來源

當回覆基於 `get_rag_answer` 時，系統 SHALL 在回覆最下方完整保留工具回傳的「參考資料來源：」標題、編號與網址，且 SHALL NOT 修改網址或改以 Markdown 連結呈現。

#### Scenario: 保留來源清單

- **WHEN** 回覆使用了 `get_rag_answer` 且工具輸出含「參考資料來源：」
- **THEN** 回覆末端完整包含該「參考資料來源：」段落，網址以純文字原樣顯示
