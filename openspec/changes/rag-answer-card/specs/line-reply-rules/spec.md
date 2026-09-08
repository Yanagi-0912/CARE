## MODIFIED Requirements

### Requirement: RAG 回覆前綴

當本輪實際呼叫了 `get_rag_answer` 並依其內容作答，**且最終以純文字送出**時，回覆的首行 SHALL 為依使用者語言本地化後的 RAG 前綴（繁中預設為「以下為 RAG 回應：」）。

**當最終以 Flex 卡片送出時，卡片 SHALL NOT 包含該前綴**；卡片組裝時 SHALL 剝除模型產出文字中的首行前綴，且剝除 SHALL 對所有支援語言的前綴形式生效。

理由：前綴的職責是告知「這段內容有外部資料來源，不是模型自己講的」。卡片以 header 與可點的來源按鈕承擔同一職責，再放一行「以下為…」會與 header 重複。前綴在純文字路徑仍不可省略，因為那條路徑沒有任何其他標記。

剝除 SHALL 在呈現層執行而非改寫 system prompt。理由：prompt 是軟約束，模型不保證照做；而純文字路徑仍需要前綴，把它從 prompt 移除會讓該路徑失去標記。

使用 `find_nearby_hospitals` 產出的院所清單或一般對話 SHALL NOT 加入此前綴。

#### Scenario: 純文字路徑保留前綴

- **WHEN** 本輪呼叫 `get_rag_answer`，但因查無資料、超過大小門檻或卡片組裝失敗而以純文字送出，且語言為 `zh-TW`
- **THEN** 回覆首行為「以下為 RAG 回應：」

#### Scenario: 卡片路徑剝除前綴

- **WHEN** 本輪呼叫 `get_rag_answer` 且最終以 Flex 卡片送出，而模型產出的文字首行含 RAG 前綴
- **THEN** 卡片內容不含該前綴，答案本文自第二行起

#### Scenario: 位置搜尋或一般對話

- **WHEN** 本輪僅使用 `find_nearby_hospitals`、`request_location_quick_reply`，或為一般對話
- **THEN** 回覆不加入 RAG 前綴

### Requirement: 保留參考資料來源

當回覆基於 `get_rag_answer` 且工具輸出含參考來源標題與清單時，系統 SHALL 完整保留該清單的**編號、標籤與網址**，且 SHALL NOT 修改網址本身，SHALL NOT 改以 Markdown 連結呈現。

呈現形式依送出路徑而定：

- **純文字路徑**：SHALL 在回覆最下方保留本地化標題（繁中為「參考資料來源：」）、編號與網址，網址以純文字原樣顯示。
- **卡片路徑**：SHALL 以 LINE Flex 的 URI action 按鈕呈現每一筆來源，按鈕標籤 SHALL 含編號與來源名，`uri` SHALL 為未經改動的原網址。

當工具輸出**不含**參考來源標題時，系統 SHALL NOT 在最終回覆中新增來源標題或網址清單（不得捏造來源），卡片路徑 SHALL NOT 產生任何來源按鈕。

卡片路徑所使用的結構化來源，其編號 SHALL 與純文字清單的編號完全一致，兩者 SHALL NOT 各自獨立編號。

#### Scenario: 純文字保留來源清單

- **WHEN** 回覆使用了 `get_rag_answer` 且工具輸出含參考來源段落，並以純文字送出
- **THEN** 回覆末端完整包含該來源段落，網址以純文字原樣顯示

#### Scenario: 卡片以按鈕呈現來源

- **WHEN** 回覆使用了 `get_rag_answer` 且工具輸出含參考來源段落，並以卡片送出
- **THEN** 卡片為每一筆來源產生一個 URI action 按鈕，`uri` 與工具輸出中的網址逐字相同

#### Scenario: 無真實來源時不捏造

- **WHEN** 本輪呼叫了 `get_rag_answer` 但工具輸出不含參考來源標題
- **THEN** 最終回覆不含參考來源標題與網址清單，卡片不含任何來源按鈕

#### Scenario: 兩種呈現的編號一致

- **WHEN** 同一次 RAG 回答的結構化來源與文字來源清單同時存在
- **THEN** 兩者的編號與筆數完全對應

### Requirement: 依使用者語言設定回覆純文字

系統對使用者的回覆 SHALL 使用該使用者 `settings.language`（經 normalize；未知則 `zh-TW`）對應之語言。

**模型產出的回覆內容** SHALL 為純文字與一般換行，SHALL NOT 輸出任何 Markdown 語法，包含 `**粗體**`、`# 標題`、以及 `[文字](網址)` 形式的連結。此約束針對模型輸出，SHALL NOT 解讀為禁止系統以 Flex Message 呈現回覆。

系統 MAY 在呈現層將模型產出的純文字組裝為 Flex Message。已採用此呈現的功能為：`open_official_site`、`verify_claim`、`get_rag_answer` 與 `answer_from_uploaded_document`。卡片內的文字 SHALL 同樣不含 Markdown 語法。

卡片的所有文字尺寸 SHALL 取自 `resolve_theme()`，即依該使用者的 `settings.font_size` 解析，SHALL NOT 寫死 size keyword。

#### Scenario: 一般回覆不含 Markdown

- **WHEN** 代理產生任何要送往 LINE 的回覆
- **THEN** 回覆內容為純文字，不含粗體、標題或 Markdown 連結語法

#### Scenario: 回覆語言跟隨設定

- **WHEN** 使用者 `settings.language` 為 `en` 且代理產生一般文字回覆
- **THEN** 回覆使用英文（而非強制繁體中文）

#### Scenario: 卡片字級跟隨設定

- **WHEN** 使用者 `settings.font_size` 為 `xlarge` 且本輪回覆以卡片送出
- **THEN** 卡片各文字元件的 size 為 `_SIZE_SCALE` 中 `xlarge` 一欄對應的值

## ADDED Requirements

### Requirement: RAG 回覆的卡片化與降級

當本輪呼叫 `get_rag_answer` 或 `answer_from_uploaded_document` 且該工具輸出**含可呈現的答案內容**時，系統 SHALL 以 Flex 卡片送出回覆。

當工具輸出為失敗訊息（`is_rag_fail()` 為真）時，系統 SHALL 以純文字送出，SHALL NOT 組裝卡片。

系統 SHALL 在送出前量測卡片的上線位元組，量測方式 SHALL 與 LINE SDK 實際送出的序列化一致（`json.dumps` 預設參數，非 ASCII 字元轉義）。超過門檻時 SHALL 退回純文字送出，SHALL NOT 送出可能被 LINE 拒收的卡片。

卡片組裝拋出例外時 SHALL 退回純文字送出，SHALL NOT 讓使用者收到例外或空白回覆。

存入對話歷史的 SHALL 為純文字內容，SHALL NOT 為 Flex JSON。

#### Scenario: 有內容的衛教回答走卡片

- **WHEN** `get_rag_answer` 回傳含答案本文與參考來源的內容
- **THEN** 使用者收到 Flex 卡片，卡片字級依其 `settings.font_size` 解析

#### Scenario: 查不到時走純文字

- **WHEN** `get_rag_answer` 回傳 `[RAG_ERR:KB_EMPTY]` 前綴的失敗訊息
- **THEN** 使用者收到純文字回覆，且該回覆首行含 RAG 前綴

#### Scenario: 超過大小門檻退回純文字

- **WHEN** 組裝後的卡片上線位元組超過門檻
- **THEN** 使用者收到內容相同的純文字回覆，SHALL NOT 收到空白或無回應

#### Scenario: 對話歷史存純文字

- **WHEN** 本輪以卡片送出 RAG 回覆
- **THEN** 存入對話歷史的 `ai_reply` 為純文字，不含 Flex JSON

### Requirement: 卡片路徑的語音回覆

當使用者 `settings.voice_reply_enabled` 為真且本輪回覆以 Flex 卡片送出時，系統 SHALL 一併附加語音訊息。合成用的文字 SHALL 取卡片組裝前的純文字內容，SHALL NOT 為 Flex JSON。

Quick Reply 掛在訊息陣列最後一則的既有行為 SHALL NOT 改變。

#### Scenario: 卡片仍有語音

- **WHEN** 使用者已開啟語音回覆且收到 RAG 卡片
- **THEN** 卡片之後附有對應的 `AudioMessage`，其內容為答案的純文字朗讀

#### Scenario: 未開啟語音不附加

- **WHEN** 使用者 `voice_reply_enabled` 為假且收到 RAG 卡片
- **THEN** 僅送出卡片，不附加 `AudioMessage`
