## ADDED Requirements

### Requirement: 核准前的內容預覽

系統 SHALL 提供需 admin 身分的內容預覽資源，使營運端能在核准前看到將被收錄的實際內容。

啟動預覽時系統 SHALL 先同步驗證：回報存在、目標 URL 經正規化後全部通過白名單、數量未超過上限。驗證失敗 SHALL 以既有錯誤碼拒絕且不進行任何抓取；白名單驗證 SHALL 一次回報全部不合格的 URL，SHALL NOT 只回報第一個。

驗證通過後系統 SHALL 立即回應並標示預覽為進行中，實際抓取 SHALL 於回應送出後才執行。預覽端點 SHALL NOT 在 HTTP 回應中等待外部抓取服務完成。

抓取結果 SHALL 逐 URL 記錄狀態（成功／空內容／失敗）、頁面標題、字數與內容雜湊，並將抓取到的原文快照保存於伺服器端。抓取過程發生未預期例外時，系統 SHALL 仍將預覽狀態收斂為失敗，SHALL NOT 讓其停留在進行中。

回傳給呼叫端的內容 SHALL 是抓取到的原文（超過長度上限時截斷並標示已截斷），SHALL NOT 以模型摘要取代原文——否則核准的對象與收錄的對象不是同一份文字。

#### Scenario: 啟動預覽立即回應

- **WHEN** admin 對一筆回報的白名單 URL 啟動預覽
- **THEN** 端點立即回應且預覽狀態為進行中，抓取尚未在回應中完成

#### Scenario: 非白名單 URL 於啟動階段即拒絕

- **WHEN** 啟動預覽的請求包含非白名單 URL
- **THEN** 回傳 400 且不進行任何抓取，回應列出全部不合格的 URL

#### Scenario: 抓取完成可取得內容

- **WHEN** 背景抓取完成且至少一個 URL 成功
- **THEN** 查詢預覽可取得逐 URL 的狀態、標題、字數、內容雜湊與原文

#### Scenario: 抓取失敗不停在進行中

- **WHEN** 抓取過程中外部服務失敗或拋出例外
- **THEN** 預覽狀態收斂為失敗並可查得錯誤訊息，SHALL NOT 停留在進行中

### Requirement: 預覽快照的保存期限與取代

預覽快照 SHALL 有保存期限，逾期後 SHALL 自動失效且 SHALL NOT 再作為核准依據。

同一筆回報 SHALL 只保留最新一份預覽。以不同的 URL 集合或明確要求重新抓取而啟動的預覽 SHALL 取代既有預覽並取得新的預覽識別碼，舊識別碼 SHALL 隨即失效。

當該回報已有未逾期、URL 集合相同且已就緒的預覽時，再次啟動預覽 SHALL 直接回傳既有預覽而 SHALL NOT 重新抓取，除非請求明確要求重新抓取。此規則避免瀏覽佇列時對外部抓取服務產生重複請求。

#### Scenario: 逾期預覽不可用

- **WHEN** 預覽建立時間已超過保存期限
- **THEN** 查詢預覽回傳查無資料，且該預覽不能用於核准

#### Scenario: 重新抓取取代舊預覽

- **WHEN** admin 對同一回報要求重新抓取
- **THEN** 系統產生新的預覽識別碼並取代舊預覽，舊識別碼失效

#### Scenario: 期限內不重複抓取

- **WHEN** admin 再次開啟一筆已有未逾期且已就緒預覽的回報，URL 集合未變且未要求重新抓取
- **THEN** 系統回傳既有預覽，不對外部服務發出新的抓取請求

### Requirement: 核准綁定預覽快照

核准 SHALL 綁定一份具體的預覽快照，使核准的對象是「這份內容」而非「這個網址」。

核准請求 SHALL 指明所依據的預覽識別碼，並對每個選定 URL 指明呼叫端所檢視內容的雜湊值。系統 SHALL 驗證：該預覽是這筆回報最新且未逾期的一份、每個選定 URL 在該預覽中且抓取成功、且其內容雜湊與請求指明的值相符。任一項不成立時系統 SHALL 以 409 拒絕核准，SHALL NOT 改動回報狀態，並 SHALL 在錯誤訊息中區分「預覽逾期」「預覽已被取代」與「內容雜湊不符」。

此需求的目的是消除「檢查的時刻」與「抓取的時刻」之間的落差：驗證通過即代表呼叫端已檢視過的位元組與後續寫入向量庫的位元組是同一份。

#### Scenario: 雜湊不符拒絕核准

- **WHEN** 核准請求指明的內容雜湊與伺服器端快照不符
- **THEN** 回傳 409，回報狀態不變且不排入 ingest

#### Scenario: 預覽逾期拒絕核准

- **WHEN** 核准所依據的預覽已超過保存期限
- **THEN** 回傳 409 並指出預覽已逾期，回報狀態不變

#### Scenario: 選定 URL 不在預覽中

- **WHEN** 核准的選定 URL 未包含於該預覽，或其在預覽中的抓取狀態不是成功
- **THEN** 回傳 409，回報狀態不變

### Requirement: 重新收錄不得清除既有來源名稱

對同一 URL 重新收錄時，若呼叫端未指定來源名稱，系統 SHALL 沿用該 URL 既有文件的來源名稱，SHALL NOT 將其寫成空值。此讀取 SHALL 發生在刪除既有文件之前。

呼叫端已指定來源名稱時 SHALL 以指定值寫入。既有文件不存在或其來源名稱為空時，系統 SHALL 使用呼叫端提供的預設值（例如抓取到的頁面標題），使新收錄的內容仍具備可讀的來源名稱。

此需求的存在理由：「這頁資料已過時」是最常見的回報原因，其處理路徑正是對既有策展 URL 重新收錄；來源名稱一旦被清空，該來源在回答的參考清單與檢索上下文標頭中就只剩網址。

#### Scenario: 沿用既有來源名稱

- **WHEN** 對一個向量庫中已有文件且具來源名稱的 URL 重新收錄，且未指定來源名稱
- **THEN** 新寫入的文件保留原本的來源名稱

#### Scenario: 新 URL 使用提供的預設名稱

- **WHEN** 對向量庫中尚無文件的 URL 收錄，且呼叫端提供了來源名稱
- **THEN** 新寫入的文件使用該名稱，而非空字串

## MODIFIED Requirements

### Requirement: 核准後自動 ingest

營運端核准或拒絕回報時，系統 SHALL 要求呼叫者為已登入且 `role=admin` 的使用者（Bearer JWT）。

核准時系統 SHALL 先同步完成驗證（回報存在、狀態可核准、選定 URL 全部通過 `url-trust` 定義的正規化與允許網域檢查、且通過預覽快照綁定驗證），驗證失敗 SHALL 以既有錯誤碼拒絕且不改動任何狀態。

URL 驗證 SHALL 一次檢查全部選定 URL，並在失敗回應中列出**所有**不合格的 URL 與各自原因（`malformed`／`not_allowed`），SHALL NOT 只回報第一個不合格項。失敗回應的內容 SHALL 為結構化資料（含錯誤碼、不合格 URL 清單與可直接顯示的訊息），使審核介面 SHALL 能逐項標示是哪些 URL 有問題、問題為何。錯誤訊息 SHALL 取自訊息目錄，SHALL NOT 於服務層硬編英文字串。

驗證通過後，排入 ingest 的目標 SHALL 為正規化後的 URL，SHALL NOT 為呼叫端送出的原始字串。系統 SHALL 立即回應，將回報標記為 `reviewing` 且 `ingest_job.status=running`，並於回應送出後才執行收錄。核准端點 SHALL NOT 在 HTTP 回應中等待 ingest 完成，因此 SHALL NOT 於核准回應直接回傳 `resolved`。

背景收錄 SHALL 使用核准時所綁定的預覽快照內容，SHALL NOT 重新抓取該 URL。快照在收錄前已不可用時，該 URL SHALL 視為失敗，SHALL NOT 改以重新抓取的內容替代。

背景 ingest 全部成功時系統 SHALL 將 `status` 設為 `resolved` 且 `ingest_job.status=succeeded`；任一 URL 失敗時 SHALL NOT 標記 `resolved`，SHALL 將 `ingest_job.status` 設為 `failed` 並記錄可讀的錯誤訊息。背景執行過程發生未預期例外時，系統 SHALL 仍將 `ingest_job.status` 收斂為 `failed`，SHALL NOT 讓工作停留在 `running`。

#### Scenario: admin 核准立即回應

- **WHEN** role=admin 的使用者以有效 Bearer token 核准並提供允許網域 URL 與有效的預覽綁定
- **THEN** 回應立即回傳該回報，`status` 為 `reviewing` 且 `ingest_job.status` 為 `running`，ingest 尚未在回應中完成

#### Scenario: 背景 ingest 全部成功

- **WHEN** 背景工作對全部選定 URL 收錄成功
- **THEN** 回報 `status` 為 `resolved`、`ingest_job.status` 為 `succeeded`，且向量庫含該 URL 的 chunk

#### Scenario: 背景收錄不重新抓取

- **WHEN** 背景工作開始收錄一個已通過預覽綁定驗證的 URL
- **THEN** 系統以快照內容切塊寫入，SHALL NOT 對該 URL 發出新的抓取請求

#### Scenario: ingest 失敗不假 resolved

- **WHEN** 背景工作中任一 URL 收錄失敗
- **THEN** `status` 不為 `resolved`，`ingest_job.status` 為 `failed`，並可查得錯誤訊息

#### Scenario: 非白名單 URL 於回應階段即拒絕

- **WHEN** 核准請求包含非白名單 URL
- **THEN** 回傳 400，回報狀態不變且不排入背景工作

#### Scenario: 多個不合格 URL 一次列出

- **WHEN** 核准請求包含兩個不合格 URL（一個格式不合法、一個網域不在允許清單）
- **THEN** 400 回應同時列出兩者與各自原因，回報狀態不變且不排入背景工作

#### Scenario: 看似政府網址的偽裝 URL 被拒絕

- **WHEN** 核准請求包含以反斜線偽裝成允許網域的 URL（例如 `https://evil.com\.gov.tw/page`）
- **THEN** 回傳 400，該 URL 不進入 ingest

#### Scenario: 正規化後的 URL 才進入 ingest

- **WHEN** 核准請求送出帶有追蹤參數或大寫主機名的合法 URL
- **THEN** 登記的 `ingest_job.selected_urls` 為正規化後的字串，背景工作以該字串綁定預覽快照

#### Scenario: 非 admin 無法核准

- **WHEN** role 非 admin 的已登入使用者呼叫核准端點
- **THEN** 回傳 403，不執行 ingest

#### Scenario: 拒絕回報

- **WHEN** 營運拒絕回報
- **THEN** status 為 rejected，不執行 ingest
