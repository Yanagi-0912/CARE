# LINE Reply Rules Spec

## Purpose

定義 CARE 對外（LINE 通道）回覆的內容格式規則，確保在 LINE 上呈現乾淨、可讀且不含 Markdown 的純文字。規則來源為系統提示（`app/services/agent/prompt.py` 的 `SYSTEM_PROMPT`）與回覆組裝流程。
## Requirements
### Requirement: RAG 回覆前綴

當且僅當本輪實際呼叫了 `get_rag_answer` 並依其內容作答時，回覆的首行 SHALL 為依使用者語言本地化後的 RAG 前綴（繁中預設為「以下為 RAG 回應：」）。使用 `find_nearby_hospitals` 產出的院所清單或一般對話 SHALL NOT 加入此前綴。

#### Scenario: 使用 RAG 作答

- **WHEN** 本輪呼叫 `get_rag_answer` 並引用其回傳內容作答，且語言為 `zh-TW`
- **THEN** 回覆首行為「以下為 RAG 回應：」

#### Scenario: 位置搜尋或一般對話

- **WHEN** 本輪僅使用 `find_nearby_hospitals`、`request_location_quick_reply`，或為一般對話
- **THEN** 回覆不加入 RAG 前綴

### Requirement: 保留參考資料來源

當回覆基於 `get_rag_answer` 且工具輸出含參考來源標題與清單時，系統 SHALL 在回覆最下方完整保留該標題（依語言本地化；繁中為「參考資料來源：」）、編號與網址，且 SHALL NOT 修改網址或改以 Markdown 連結呈現。

當工具輸出**不含**參考來源標題時，系統 SHALL NOT 在最終回覆中新增來源標題或網址清單（不得捏造來源）。

#### Scenario: 保留來源清單

- **WHEN** 回覆使用了 `get_rag_answer` 且工具輸出含參考來源段落
- **THEN** 回覆末端完整包含該來源段落，網址以純文字原樣顯示

#### Scenario: 無真實來源時不捏造

- **WHEN** 本輪呼叫了 `get_rag_answer` 但工具輸出不含參考來源標題
- **THEN** 最終回覆不含參考來源標題與網址清單

### Requirement: 處理訊息時顯示 Loading Animation

系統收到有效使用者訊息並開始處理時，SHALL 在呼叫 Agent 前對該一對一聊天的 `chatId`（LINE user id）呼叫 Loading Animation API，且 `loadingSeconds` SHALL 為 10。Loading 失敗時系統 SHALL NOT 中斷後續 Agent 處理與最終回覆。

#### Scenario: 進入處理前顯示 loading

- **WHEN** LINE webhook 開始處理一則有效訊息
- **THEN** 系統先對該使用者顯示 Loading Animation（10 秒），再呼叫 Agent

#### Scenario: Loading 失敗不阻擋回覆

- **WHEN** Loading Animation API 呼叫失敗
- **THEN** 系統仍繼續呼叫 Agent 並完成對使用者的回覆

### Requirement: 醫療識詐回覆邊界

當回覆涉及疑似醫療詐騙、假藥或要求因醫療名義匯款／點連結時，系統（經由系統提示約束代理）SHALL 以繁體中文純文字提醒使用者提高警覺、強烈勸阻在查證前匯款或提供個資／驗證碼，並可提示官方查證管道（例如 165 反詐騙諮詢專線或相關政府單位公開資訊）。回覆 SHALL NOT 宣稱代理具執法或個案法律判定效力，SHALL NOT 使用 Markdown。若本輪有呼叫 `get_rag_answer`，仍須遵守既有 RAG 前綴與參考資料來源規則。

#### Scenario: 急匯款醫療話術勸阻

- **WHEN** 使用者描述收到要求先轉帳才能「退費／領藥／健保／保險理賠」的醫療相關訊息
- **THEN** 回覆以純文字勸阻查證前匯款，並提示可向官方管道查證，且不宣稱代理可代替報案

#### Scenario: 識詐仍保留 RAG 格式

- **WHEN** 本輪呼叫 `get_rag_answer` 回答醫療識詐問題且工具含參考來源
- **THEN** 回覆首行為「以下為 RAG 回應：」，末端保留「參考資料來源：」純文字網址

### Requirement: 官網入口 Flex 原樣輸出

當本輪呼叫 `open_official_site` 且工具回傳 LINE Flex Message JSON 時，代理最終回覆 SHALL 原樣輸出該 JSON，嚴禁修改、重寫、摘要或另加問候語／Markdown。

#### Scenario: Tool 回傳 Flex 時原樣輸出

- **WHEN** `open_official_site` 回傳合法 Flex JSON 字串
- **THEN** 最終送往 LINE 的內容為該 Flex（經既有 reply 解析路徑），代理不得改寫為純文字網址列表

### Requirement: 依使用者語言設定回覆純文字

系統對使用者的回覆 SHALL 使用該使用者 `settings.language`（經 normalize；未知則 `zh-TW`）對應之語言，且 SHALL 為純文字與一般換行。系統 SHALL NOT 輸出任何 Markdown 語法，包含 `**粗體**`、`# 標題`、以及 `[文字](網址)` 形式的連結。

#### Scenario: 一般回覆不含 Markdown

- **WHEN** 代理產生任何要送往 LINE 的回覆
- **THEN** 回覆內容為純文字，不含粗體、標題或 Markdown 連結語法；網址以純文字直接顯示

#### Scenario: 回覆語言跟隨設定

- **WHEN** 使用者 `settings.language` 為 `en` 且代理產生一般文字回覆
- **THEN** 回覆使用英文（而非強制繁體中文）

