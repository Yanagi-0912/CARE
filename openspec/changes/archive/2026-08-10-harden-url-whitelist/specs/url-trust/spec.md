## ADDED Requirements

### Requirement: URL 正規化

系統 SHALL 提供 `normalize_url(raw)`，將輸入字串轉為唯一的正規形式；當輸入無法被唯一化時 SHALL 回傳 `None`，SHALL NOT 回傳「盡力而為」的近似結果。

正規化 SHALL 執行下列動作：去除頭尾空白；輸入未帶 scheme 時補上 `https://`；scheme 與 host 轉小寫；去除 host 尾端的 `.`；去除該 scheme 的預設埠（http:80、https:443）；丟棄 fragment；剝除追蹤參數（`utm_source`、`utm_medium`、`utm_campaign`、`utm_term`、`utm_content`、`utm_id`、`gclid`、`fbclid`、`msclkid`、`yclid`、`igshid`、`mc_cid`、`mc_eid`）；路徑為根時補為 `/`，非根路徑去除尾端 `/`。

正規化 SHALL NOT 執行下列動作：解析 `.`／`..` 路徑段；對 path 或 query 做百分比編碼或解碼；重排 query 參數順序；移除追蹤參數以外的查詢參數；發出任何 DNS 或 HTTP 請求；判斷目標是否為私有網段。

正規化 SHALL 為冪等：對任一輸入 `x`，`normalize_url(normalize_url(x))` 的結果 SHALL 等於 `normalize_url(x)`。

#### Scenario: 無 scheme 的輸入補為 https

- **WHEN** 呼叫端傳入 `www.hpa.gov.tw/x`
- **THEN** 回傳 `https://www.hpa.gov.tw/x`，而非 `None`

#### Scenario: 剝除追蹤參數並保留其餘查詢

- **WHEN** 傳入 `https://www.hpa.gov.tw/a?utm_source=line&nodeid=1`
- **THEN** 回傳 `https://www.hpa.gov.tw/a?nodeid=1`

#### Scenario: 同一資源的不同寫法收斂為同一字串

- **WHEN** 分別傳入 `HTTP://WWW.HPA.GOV.TW./a/`、`http://www.hpa.gov.tw:80/a`、`http://www.hpa.gov.tw/a#top`
- **THEN** 三者回傳同一個正規化字串

#### Scenario: 正規化為冪等

- **WHEN** 對任一可正規化的輸入連續套用兩次正規化
- **THEN** 第二次的結果與第一次相同

### Requirement: 剖析歧異一律拒絕

當一個字串在不同 URL 解析器（Python `urllib.parse`、WHATWG／Node、瀏覽器）之間可能得到不同的 host 時，系統 SHALL 拒絕該字串，SHALL NOT 以其中任一解析結果放行。判定 SHALL 在剖析之前先拒絕下列輸入：含反斜線（`\`）者；含控制字元（U+0000–U+001F、U+007F）者；authority 段以外亦不得含任何空白字元（含 U+00A0）者；authority 段含 userinfo（`@`）者；authority 段含非 ASCII 字元者。

判定 SHALL 額外要求：scheme 僅限 `http`／`https`；host 由 ASCII 英數與 `-`、`.` 組成且至少含一個 `.`；埠若存在 SHALL 為合法數值。正規化完成後 SHALL 重新剖析其輸出，並要求 host 與正規化過程認定的 host 相同；不相同 SHALL 拒絕。

被拒絕的輸入 SHALL 以 `malformed` 為原因，與「格式合法但網域不在允許清單」的 `not_allowed` 區分。

#### Scenario: 反斜線偽裝的網域被拒絕

- **WHEN** 傳入 `https://evil.com\.gov.tw/page`（Python 剖析得 host `evil.com\.gov.tw`，WHATWG 剖析得 host `evil.com`）
- **THEN** 判定為不合格，原因為 `malformed`，SHALL NOT 因為字串結尾像 `.gov.tw` 而放行

#### Scenario: 百分比編碼的反斜線被拒絕

- **WHEN** 傳入 `https://evil.com%5C.gov.tw/page`
- **THEN** 判定為不合格

#### Scenario: userinfo 偽裝被拒絕

- **WHEN** 傳入 `https://www.hpa.gov.tw@evil.com/x`
- **THEN** 判定為不合格，SHALL NOT 因為人眼可讀部分像政府網址而放行

#### Scenario: 控制字元不得被靜默移除後放行

- **WHEN** 傳入含 tab 的 `https://evil.com<TAB>.gov.tw/x`
- **THEN** 判定為不合格；SHALL NOT 由剖析器刪除 tab 後成為合格字串

#### Scenario: 非 ASCII 的 authority 被拒絕

- **WHEN** 傳入 `https://evil.com。gov.tw/` 或 `https://台灣.gov.tw/x`
- **THEN** 判定為不合格，原因為 `malformed`

### Requirement: 允許網域清單可設定

允許的來源網域 SHALL 由環境變數提供（逗號分隔的網域後綴），SHALL NOT 只能以修改原始碼的方式調整。未設定或設為空值時 SHALL 退回內建預設清單，使既有部署不需變更設定即可運作。

清單解析 SHALL 去除各項頭尾空白、轉小寫、去除前導的 `.` 與 `*.`、略過空項，並 SHALL 收斂被其他項完全涵蓋的冗餘後綴（例如同時設定 `gov.tw` 與 `hpa.gov.tw` 時，後者 SHALL 被收斂）。系統 SHALL 於載入時記錄被收斂掉的項目。

內建預設清單 SHALL 至少涵蓋 `gov.tw`。網域比對 SHALL 以標籤邊界為準（`host` 等於後綴，或 `host` 以 `.` + 後綴結尾），SHALL NOT 使用字串包含或單純的結尾字元比對。

允許清單 SHALL 可由建構子注入，使測試 SHALL NOT 需要修改全域設定物件。

#### Scenario: 冗餘後綴被收斂

- **WHEN** 設定為 `gov.tw, hpa.gov.tw, CDC.GOV.TW, .mohw.gov.tw`
- **THEN** 實際生效的清單為 `gov.tw` 單一項，並記錄其餘三項被收斂

#### Scenario: 標籤邊界比對

- **WHEN** 允許清單含 `gov.tw`，傳入 `https://gov.tw.evil.com/` 或 `https://evilgov.tw/`
- **THEN** 兩者皆判定為不在允許清單（`not_allowed`）

#### Scenario: 未設定時使用內建預設

- **WHEN** 環境變數未設定
- **THEN** 使用內建預設清單，且 `https://www.hpa.gov.tw/x` 仍為合格

### Requirement: 網搜的 site 篩選與允許清單分離

網路搜尋階段附加的 `site:` 篩選 SHALL 為獨立設定，SHALL NOT 由允許清單自動推導。搜尋階段的篩選目的是收窄召回，允許清單的目的是守住入庫的信任邊界；兩者變更互不牽動。

搜尋結果 SHALL 仍逐筆以完整允許清單過濾，SHALL NOT 因為查詢已帶 `site:` 就略過過濾。

#### Scenario: 擴充允許清單不改動搜尋查詢

- **WHEN** 允許清單新增一個網域後綴，而 site 篩選設定未變更
- **THEN** 送往搜尋服務的查詢字串不變

#### Scenario: 搜尋結果仍逐筆過濾

- **WHEN** 搜尋服務回傳一筆不在允許清單的結果
- **THEN** 該筆 SHALL NOT 進入回答的引用來源

### Requirement: 批次驗證與完整錯誤回報

系統 SHALL 提供 `assert_allowed_urls(urls)`，回傳全部通過驗證的**正規化後** URL 清單。當任一 URL 不合格時，SHALL 在檢查完全部輸入後才失敗，並在錯誤中列出**所有**不合格的 URL 與各自原因（`malformed` 或 `not_allowed`），SHALL NOT 於遇到第一個不合格項時即中止。

錯誤 SHALL 以純資料形式表達（URL 與原因碼），SHALL NOT 在此層綁定 HTTP 狀態碼或使用者可見文案；使用者可見文案 SHALL 由呼叫端自訊息目錄取得，SHALL NOT 於服務層硬編英文字串。

#### Scenario: 一次列出全部不合格 URL

- **WHEN** 傳入三個 URL，其中一個合法、一個格式不合法、一個網域不在允許清單
- **THEN** 錯誤中同時包含兩個不合格項及其原因，順序與輸入一致

#### Scenario: 回傳的是正規化後字串

- **WHEN** 傳入 `https://WWW.HPA.GOV.TW/a/?utm_source=line`
- **THEN** 回傳的清單中該項為正規化後的字串，而非原始輸入

### Requirement: 抓取後以最終 URL 二次驗證

將網頁內容寫入向量庫前，系統 SHALL 以抓取端回報的最終 URL 再次驗證允許清單。最終 URL 不在允許清單時 SHALL 以 `rejected` 結束，且 SHALL NOT 產生向量、SHALL NOT 刪除或寫入任何文件。

抓取端未回報最終 URL 時，系統 SHALL 以請求的 URL 續行並記錄該情況，SHALL NOT 因缺少最終 URL 而中止整條入庫。

寫入文件的 URL 欄位 SHALL 為正規化後的字串。以 URL 取代既有內容時，刪除條件 SHALL 同時涵蓋正規化前後的字串，使正規化上線前寫入的舊文件 SHALL NOT 殘留為重複資料。最終 URL 與請求 URL 不同時，SHALL 另行記錄最終 URL，使營運端可查得該內容實際抓自何處。

#### Scenario: 重導向離開允許網域

- **WHEN** 請求允許網域的 URL，但抓取端回報的最終 URL 位於允許清單之外
- **THEN** 結果為 `rejected`，且未寫入任何 chunk

#### Scenario: 重導向仍在允許網域

- **WHEN** 抓取端回報的最終 URL 與請求不同但仍在允許清單內
- **THEN** 正常寫入，並記錄最終 URL

#### Scenario: 抓取端未回報最終 URL

- **WHEN** 抓取端未提供最終 URL
- **THEN** 以請求的 URL 續行並記錄，入庫流程不中斷

#### Scenario: 取代舊資料時涵蓋正規化前的鍵

- **WHEN** 對一個正規化後字串與原始輸入不同的 URL 重新入庫
- **THEN** 刪除條件同時涵蓋兩種字串，該頁在向量庫中不出現兩份
