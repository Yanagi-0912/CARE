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

系統 SHALL 在 `guardrail` 節點以注入的「文字→bool」分類器判斷使用者訊息是否與健康醫療或醫療場景識詐相關，並據此設定 `allow_rag`。Guardrail SHALL 不綁定特定模型實作（透過 DI 注入分類器）。當使用者訊息為位置座標訊息時 SHALL 快速跳過分類並禁用 RAG。當分類器發生例外時 SHALL 採 fail-open（視為允許）。分類範圍 SHALL 至少涵蓋：健康、醫療、疾病、藥物、營養、運動、心理健康，以及醫療詐騙／假藥／假醫師／假醫院或健保相關可疑訊息、要求因「醫療／檢驗／健保／保險理賠」而匯款或點擊不明連結等情境。

#### Scenario: 健康相關訊息

- **WHEN** 使用者訊息與健康、醫療、疾病、藥物、營養、運動或心理健康相關
- **THEN** `allow_rag` 設為 `True`，代理可使用 `get_rag_answer` 工具

#### Scenario: 醫療詐騙相關訊息

- **WHEN** 使用者訊息涉及假藥、假醫師、假醫院簡訊、保證療效保健品話術，或因醫療／健保名義要求匯款或點連結
- **THEN** `allow_rag` 設為 `True`，代理可使用 `get_rag_answer` 工具

#### Scenario: 位置座標訊息跳過 RAG

- **WHEN** 使用者訊息以「這是我的目前位置」開頭或包含 `lat=`
- **THEN** 直接禁用 RAG（`allow_rag = False`），不呼叫分類器

#### Scenario: 分類失敗採 fail-open

- **WHEN** 分類器呼叫發生例外
- **THEN** 記錄錯誤並回傳允許（`True`），避免暫時性錯誤阻斷使用者流程

### Requirement: 座標進入對話時依科別決定搜尋工具

系統 SHALL 於使用者訊息為座標文字（「這是我的目前位置：lat=…, lng=…」）且模型未主動呼叫工具時，
強制注入院所搜尋工具呼叫，以避免代理回傳空內容或將座標文字當成 RAG 查詢送出。

注入哪一個工具 SHALL 依對話歷史中是否存在科別需求決定：有科別則注入
`find_nearby_facilities_by_department`（並帶入使用者的原始說法），否則注入 `find_nearby_hospitals`。

若歷史中存在院所類型需求（大醫院、診所、藥局），SHALL 一併帶入 `facility_type` 參數，
與科別獨立判斷 —— 兩者可同時存在，亦可只有其中之一。

#### Scenario: 歷史中有科別需求

- **WHEN** 使用者先傳「附近有腸胃科嗎」，本輪傳送座標，且模型未產生 tool_calls
- **THEN** 系統注入 `find_nearby_facilities_by_department`，`args` 含 `lat`、`lng` 與 `department="腸胃科"`

#### Scenario: 歷史中僅有類型需求

- **WHEN** 使用者先傳「附近有大醫院嗎」，本輪傳送座標，且模型未產生 tool_calls
- **THEN** 系統注入 `find_nearby_hospitals`，`args` 含 `lat`、`lng` 與 `facility_type="大醫院"`

#### Scenario: 歷史中同時有科別與類型需求

- **WHEN** 使用者先傳「附近大醫院的腸胃科」，本輪傳送座標，且模型未產生 tool_calls
- **THEN** 系統注入 `find_nearby_facilities_by_department`，`args` 同時含
  `department="腸胃科"` 與 `facility_type="大醫院"`

#### Scenario: 歷史中無科別與類型需求

- **WHEN** 使用者先傳「附近有醫院嗎」，本輪傳送座標，且模型未產生 tool_calls
- **THEN** 系統注入 `find_nearby_hospitals`，`args` 僅含 `lat` 與 `lng`

### Requirement: 代理可用工具集

系統 SHALL 透過 `app/tools/registry.py` 的 `get_all_tools(include_rag_tool)` 組裝工具集。工具集 SHALL 固定包含 `find_nearby_hospitals`、`lookup_medical_facility`、`request_location_quick_reply` 與 `submit_knowledge_report`；`get_rag_answer` SHALL 可依 `include_rag_tool` 參數納入。工具集 SHALL NOT 包含 `search_public_web`。工具實例 SHALL 由 `app/dependencies.py`（composition root）透過 `configure_rag_tool` / `configure_medical_tools` 注入其依賴服務；`WebSearchService` SHALL 注入 `RagAnswerService`（非 agent tool）。

#### Scenario: 納入 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=True)`
- **THEN** 回傳的工具集包含 `get_rag_answer`、`find_nearby_hospitals`、`lookup_medical_facility`、`request_location_quick_reply`、`submit_knowledge_report`，且不含 `search_public_web`

#### Scenario: 排除 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=False)`
- **THEN** 回傳的工具集仍包含 `submit_knowledge_report` 與醫療／位置相關工具，且不含 `get_rag_answer` 與 `search_public_web`

### Requirement: 最終回覆組裝

系統 SHALL 從流程結果的最後一則 AI 訊息取得文字作為回覆，並偵測本輪是否呼叫了 `request_location_quick_reply`，於回傳結果中提供 `call_request_location` 旗標。醫療與入口類工具（`find_nearby_hospitals`、`find_nearby_facilities_by_department`、`lookup_medical_facility`、`request_location_quick_reply`、`open_official_site`）的輸出 SHALL 直接作為送往 LINE 的內容，不得由模型改寫，以免 Flex Message JSON 被破壞。當本輪呼叫過 `get_rag_answer` 且工具輸出含「參考資料來源：」但最終回覆遺漏時，系統 SHALL 以防禦性後置處理自動補回參考資料來源段落。

#### Scenario: 回覆遺漏參考來源時自動補回

- **WHEN** 本輪呼叫過 `get_rag_answer`，其輸出含「參考資料來源：」，但代理最終回覆未包含該段
- **THEN** 系統將工具輸出中的「參考資料來源：」段落附加到最終回覆末端

#### Scenario: 觸發位置快速回覆旗標

- **WHEN** 本輪呼叫過 `request_location_quick_reply`
- **THEN** 回傳結果的 `call_request_location` 為 `True`

#### Scenario: 科別搜尋回傳 Flex Message

- **WHEN** 本輪呼叫過 `find_nearby_facilities_by_department` 且其輸出為 Flex Message JSON
- **THEN** 系統以該工具輸出作為最終回覆，不經模型改寫

### Requirement: 醫療識詐與健康查詢必須優先使用 RAG

當本輪工具集已包含 `get_rag_answer`，且使用者問題屬於健康衛教（症狀、疾病、用藥、保健等）或醫療場景識詐查證時，代理 SHALL 先呼叫 `get_rag_answer` 再依工具結果回答，SHALL NOT 僅依模型自身知識逕行給出衛教建議或識詐結論。純寒暄或與健康／醫療識詐無關的短句可不呼叫該工具。系統提示（`SYSTEM_PROMPT`）SHALL 載明上述規則，並說明代理可協助辨識可疑醫療訊息，但不是執法人員、不代替報案；遇急著匯款或點不明連結時 SHALL 強烈勸阻並提示可向官方管道（例如 165 反詐騙諮詢專線）查證。

#### Scenario: 症狀問題先查 RAG

- **WHEN** `allow_rag` 為 `True` 且使用者詢問症狀或衛教建議
- **THEN** 代理呼叫 `get_rag_answer` 後再回答

#### Scenario: 疑似醫療詐騙先查 RAG

- **WHEN** `allow_rag` 為 `True` 且使用者詢問某則醫療相關訊息是否為詐騙／假藥
- **THEN** 代理呼叫 `get_rag_answer` 後再回答，並在適當時提示官方查證管道

#### Scenario: 寒暄可不查 RAG

- **WHEN** 使用者僅寒暄且與健康或醫療識詐無關
- **THEN** 代理可不呼叫 `get_rag_answer`

### Requirement: 剝離無依據的 RAG 來源區塊

系統在組裝最終文字回覆時，若本輪曾呼叫 `get_rag_answer` 且該工具輸出不含參考來源標題，而代理最終文字卻含參考來源標題，系統 SHALL 移除該來源標題及其後清單，僅保留正文。

#### Scenario: 後置剝離亂編來源

- **WHEN** `get_rag_answer` 工具內容無來源標題，且代理回覆含來源標題
- **THEN** 回傳給使用者的文字已去除來源標題與後續清單

### Requirement: 已走位置／院所工具時不強制 RAG

當系統在 `allow_rag=True` 下準備強制注入 `get_rag_answer` 時，若本輪對話訊息中已存在 `request_location_quick_reply`、`find_nearby_hospitals` 或 `lookup_medical_facility` 的工具結果，系統 SHALL NOT 強制注入 `get_rag_answer`。

#### Scenario: 請分享位置後不再 force RAG

- **WHEN** `allow_rag=True`、模型本步未產生 tool_calls，且訊息中已有 `request_location_quick_reply` 的 ToolMessage
- **THEN** 系統不注入 `get_rag_answer`，亦不標記 `force_rag=True`

#### Scenario: 健康問句仍可 force

- **WHEN** `allow_rag=True`、模型未產生 tool_calls，且尚未執行 RAG 或位置／院所工具
- **THEN** 系統仍可強制注入 `get_rag_answer`

### Requirement: 找院所意圖不強制 RAG，改強制請位置

當最新使用者訊息屬於「尋找附近醫院／診所／藥局或要去看醫生」意圖，且模型本步未產生 tool_calls、且尚未執行過位置／院所工具時，系統 SHALL NOT 強制注入 `get_rag_answer`，SHALL 改強制注入 `request_location_quick_reply`。

若訊息同時含查詢特定院所位置的線索（例如「在哪」「地址」），系統 SHALL NOT 套用上述強制請位置（留給院所查詢工具路徑）。

#### Scenario: 「我要看醫院」強制請位置

- **WHEN** 使用者訊息為「我要看醫院」，`allow_rag` 可為 True，且模型未產生 tool_calls
- **THEN** 系統注入 `request_location_quick_reply`，且不注入 `get_rag_answer`／不標記 `force_rag=True`

#### Scenario: 健康症狀仍可 force RAG

- **WHEN** 使用者訊息為健康症狀描述且非找院所意圖，`allow_rag=True`，模型未產生 tool_calls
- **THEN** 系統仍可強制注入 `get_rag_answer`

### Requirement: 媒體抽出全文不強制 RAG

當最新使用者訊息為 `LineMediaHandler` 媒體抽出格式（以「以下為使用者傳送的{image|video|audio|file}媒體內容：」開頭）時，即使 `allow_rag` 為 True 且模型本步未產生 tool_calls，系統 SHALL NOT 強制注入 `get_rag_answer`。

系統 SHALL 讓模型依抽出內容直接回答或摘要；SHALL NOT 將整份媒體全文當作知識庫查詢字串強制檢索。

一般非媒體衛教文字在 `allow_rag=True` 且無 tool_calls 時，既有 force RAG 行為 SHALL 維持不變。

#### Scenario: 飲食指南 PDF 媒體全文不 force RAG

- **WHEN** 使用者訊息為「以下為使用者傳送的file媒體內容：」加上飲食指南抽出全文，`allow_rag=True`，且模型未產生 tool_calls
- **THEN** 系統不注入 `get_rag_answer`、不標記 `force_rag=True`，也不注入 `request_location_quick_reply`

#### Scenario: 一般衛教文字仍可 force RAG

- **WHEN** 使用者訊息為非媒體前綴的健康症狀描述，`allow_rag=True`，模型未產生 tool_calls
- **THEN** 系統仍可強制注入 `get_rag_answer`

### Requirement: 上傳文件問答工具納入代理

系統 SHALL 透過工具註冊將「依使用者上傳文件回答」的工具提供給代理（至少在健康／衛教相關回合可使用）。System prompt SHALL 指示：與使用者先前上傳文件內容相關的問題優先呼叫該工具；一般官方衛教知識仍使用 `get_rag_answer`。

#### Scenario: 工具集包含上傳文件問答

- **WHEN** 代理本輪可使用 RAG 相關工具
- **THEN** 工具集包含上傳文件問答工具（名稱以實作為準，例如 `answer_from_uploaded_document`）

#### Scenario: 媒體上傳回合不強制官方 RAG

- **WHEN** 使用者訊息為媒體抽出前綴格式
- **THEN** 系統仍 SHALL NOT 強制注入 `get_rag_answer`（維持既有 skip）；上傳 ingest 於背景／抽字後執行

### Requirement: 官網意圖時強制入口工具

當 guardrail／agent 路徑判定為官網／LIFF 入口意圖時，系統 SHALL 優先確保呼叫 `open_official_site`，且 SHALL NOT 因 `allow_rag` 強制注入 `get_rag_answer`。

#### Scenario: 官網意圖不 force RAG

- **WHEN** 使用者訊息命中官網入口意圖
- **THEN** 不注入 `get_rag_answer`；必要時注入 `open_official_site`

### Requirement: System prompt 依使用者語言組裝

系統 SHALL 以 `build_system_prompt(language)`（或同等）依 normalize 後的使用者語言組裝 system prompt。Prompt SHALL 指示模型以該語言回覆，且 SHALL NOT 再硬性要求「只能使用繁體中文」。RAG 前綴與參考來源標題的指示 SHALL 使用該語言對應字串。

#### Scenario: 英文使用者的 prompt 要求英文回覆

- **WHEN** `user_profile.settings.language` 為 `en` 且進入 `agent` 節點
- **THEN** 傳給模型的 system prompt 要求以英文回覆

#### Scenario: 缺省語言

- **WHEN** profile 無 language 或為未知代碼
- **THEN** system prompt 以 `zh-TW`（繁體中文）規則組裝

