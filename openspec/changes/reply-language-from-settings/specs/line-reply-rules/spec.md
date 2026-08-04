## MODIFIED Requirements

### Requirement: 依使用者語言設定回覆純文字

系統對使用者的回覆 SHALL 使用該使用者 `settings.language`（經 normalize；未知則 `zh-TW`）對應之語言，且 SHALL 為純文字與一般換行。系統 SHALL NOT 輸出任何 Markdown 語法，包含 `**粗體**`、`# 標題`、以及 `[文字](網址)` 形式的連結。

#### Scenario: 一般回覆不含 Markdown

- **WHEN** 代理產生任何要送往 LINE 的回覆
- **THEN** 回覆內容為純文字，不含粗體、標題或 Markdown 連結語法；網址以純文字直接顯示

#### Scenario: 回覆語言跟隨設定

- **WHEN** 使用者 `settings.language` 為 `en` 且代理產生一般文字回覆
- **THEN** 回覆使用英文（而非強制繁體中文）

### Requirement: RAG 回覆前綴

當且僅當本輪實際呼叫了 `get_rag_answer` 並依其內容作答時，回覆的首行 SHALL 為依使用者語言本地化後的 RAG 前綴（繁中預設為「以下為 RAG 回應：」）。使用 `find_nearby_hospitals` 產出的院所清單或一般對話 SHALL NOT 加入此前綴。

#### Scenario: 使用 RAG 作答

- **WHEN** 本輪呼叫 `get_rag_answer` 並引用其回傳內容作答，且語言為 `zh-TW`
- **THEN** 回覆首行為「以下為 RAG 回應：」

#### Scenario: 位置搜尋或一般對話

- **WHEN** 本輪僅使用 `find_nearby_hospitals`、`request_location_quick_reply`，或為一般對話
- **THEN** 回覆不加入 RAG 前綴

### Requirement: 保留參考資料來源

當回覆基於 `get_rag_answer` 時，系統 SHALL 在回覆最下方完整保留工具回傳的參考來源標題（依語言本地化；繁中為「參考資料來源：」）、編號與網址，且 SHALL NOT 修改網址或改以 Markdown 連結呈現。

#### Scenario: 保留來源清單

- **WHEN** 回覆使用了 `get_rag_answer` 且工具輸出含參考來源段落
- **THEN** 回覆末端完整包含該來源段落，網址以純文字原樣顯示
