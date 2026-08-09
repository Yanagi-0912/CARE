## ADDED Requirements

### Requirement: 官網入口 Flex Tool

系統 SHALL 提供 Agent tool `open_official_site`。呼叫時 SHALL 回傳 LINE Flex Message 的 JSON 字串（非 Markdown）。Flex SHALL 引導使用者開啟 CARE 官方入口。

LIFF 與官網對使用者而言是同一個目的地，因此 Flex SHALL 只渲染**單一**入口按鈕，其 URI 依下列優先序決定：

1. `LIFF_URL`（有非空白值時優先）
2. `PUBLIC_BASE_URL`

當兩者皆空白時，工具 SHALL 回傳簡短純文字說明（不得拋未處理例外）。

#### Scenario: 雙 URL 皆設定時優先 LIFF

- **WHEN** `LIFF_URL` 與 `PUBLIC_BASE_URL` 皆非空且呼叫 `open_official_site`
- **THEN** 回傳 Flex JSON，內容僅含一個 URI action，指向 `LIFF_URL`

#### Scenario: 僅官網

- **WHEN** 僅 `PUBLIC_BASE_URL` 非空
- **THEN** 回傳 Flex JSON，其唯一 URI action 指向 `PUBLIC_BASE_URL`，且不含空白 URI

#### Scenario: 皆未設定

- **WHEN** `LIFF_URL` 與 `PUBLIC_BASE_URL` 皆為空
- **THEN** 回傳純文字提示，說明官方入口尚未設定

### Requirement: 官網意圖強制工具

系統 SHALL 偵測使用者訊息中表達「要開啟官網／官方網站／網站入口／如何開啟 LIFF」的短意圖。命中時：

- SHALL NOT 強制注入 `get_rag_answer`
- 若模型未產生 tool_calls，SHALL 強制注入 `open_official_site`

媒體抽出全文前綴（image／video／audio／file）的訊息 SHALL 不套用此強制邏輯。

#### Scenario: 使用者說打開官網

- **WHEN** 使用者訊息為「打開官網」或語意等同的短意圖
- **THEN** Agent 路徑強制呼叫 `open_official_site`，且不 force RAG

#### Scenario: 媒體全文不強制官網

- **WHEN** 訊息以媒體抽出前綴開頭
- **THEN** 即使內文含「官網」字樣，亦不強制 `open_official_site`
