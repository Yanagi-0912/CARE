## ADDED Requirements

### Requirement: 核准前 Admin 必須看到將被收錄的內容

審核介面 SHALL 在核准前呈現每個選定來源 URL 將被收錄的實際內容，SHALL NOT 讓 admin 只依網址字串就完成核准。

開啟回報詳情時介面 SHALL 自動啟動內容預覽，SHALL NOT 要求 admin 另外按下取得內容的動作。預覽進行中時介面 SHALL 呈現進行中狀態，並 SHALL 停用核准動作；SHALL NOT 提供任何略過預覽直接核准的路徑。

預覽就緒後介面 SHALL 逐 URL 呈現抓取狀態、頁面標題、內容字數與內容本身；內容因長度被截斷時 SHALL 明確標示。核准請求 SHALL 帶上所依據的預覽識別碼與各 URL 的內容雜湊。

來源 URL 的預設全選行為 SHALL 維持不變，使 admin 在一般情況下 SHALL NOT 需要為了核准而逐一勾選。

#### Scenario: 開啟詳情自動取得內容

- **WHEN** admin 開啟一筆回報的詳情
- **THEN** 介面自動請求內容預覽，並在就緒前呈現進行中狀態

#### Scenario: 預覽未就緒不得核准

- **WHEN** 任一選定 URL 尚無成功的預覽結果
- **THEN** 核准動作為停用狀態，不送出請求

#### Scenario: 呈現將被收錄的內容

- **WHEN** 預覽就緒且該 URL 抓取成功
- **THEN** 介面顯示該 URL 的標題、字數與內容，內容被截斷時標示已截斷

#### Scenario: 核准帶上預覽綁定

- **WHEN** admin 於預覽就緒後按下核准
- **THEN** 請求包含預覽識別碼與各選定 URL 的內容雜湊

### Requirement: 預覽失效時的重新抓取

後端因預覽逾期、預覽已被取代或內容雜湊不符而拒絕核准時，介面 SHALL 顯示該原因並提供重新抓取的動作，SHALL NOT 將其呈現為一般的操作失敗。重新抓取完成前核准動作 SHALL 維持停用。

抓取失敗（外部服務錯誤或頁面無內容）的 URL SHALL 於介面標示，且 SHALL NOT 可被核准；admin SHALL 仍能對該回報執行拒絕。

#### Scenario: 預覽逾期後重新抓取

- **WHEN** 後端以 409 拒絕核准並指出預覽已逾期
- **THEN** 介面顯示該原因並提供重新抓取動作，核准維持停用直到新的預覽就緒

#### Scenario: 抓取失敗的 URL 不可核准

- **WHEN** 某個選定 URL 的預覽結果為抓取失敗或空內容
- **THEN** 介面標示該 URL 的失敗狀態，核准動作停用，拒絕動作仍可使用
