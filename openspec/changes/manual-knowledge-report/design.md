## Context

`POST /api/knowledge-reports` 目前是一個「什麼都收」的端點：`question` 必填一個字元，`reason` 三選一，`user_note` 與 `user_source_urls` 全選填、沒有長度或數量上限，沒有速率限制。它至今沒被前端呼叫過，所以這些缺口從未被觸發。

一旦 LIFF 出現手動表單，這個端點的威脅模型就變了：`user_source_urls` 成為使用者可任意控制、且在 admin 核准後會被 `IngestService.ingest_url` 抓取並寫進向量庫、再進 RAG prompt、最後掛在回答末尾當參考來源的輸入。所以本 change 的難處不在表單本身（三個輸入、一個 POST），而在於「把驗證放在哪裡才不會誤傷既有的兩條自動路徑」。

現況的三條建立路徑全部匯流到同一個 `KnowledgeReportService.create()`（`app/services/knowledge_reports/service.py:40`）：

| 路徑 | 入口 | URL 來源 | 可信度 |
| --- | --- | --- | --- |
| 手動表單（本 change 新增） | `app/routers/users/knowledge_reports.py:26` | 使用者貼上 | 低，需驗證 |
| agent tool | `app/tools/knowledge_report_tools.py:24` | LLM 生成或轉述 | 最低，可能是幻覺 |
| web fallback 自動建報 | `app/services/rag/web_search_service.py:72` → `service.py:63` | Firecrawl 回傳、已過白名單 | 已驗過 |

## Goals / Non-Goals

**Goals:**
- LIFF 知識回報頁可手動送出「URL + 說明」的回報，送出後在同一頁立刻看到
- URL 在 **建立當下** 就通過白名單，不是等到 admin 核准才驗
- 收緊只作用於手動路徑，agent tool 與 web fallback 的行為不變
- 建立端點具備每使用者配額與長度／數量上限
- `report_id` 碰撞不再變成 500

**Non-Goals:**
- 不接 `delete_pending_or_reviewing_by_urls` 做去重（見決策 6）
- 不為 `reason` 引入分支邏輯
- 不做全域 rate limit middleware（只在這一個端點做配額）
- 不改 `IngestService`、不改核准流程（那是 change 2 的範圍）
- 不做回報的編輯／撤回

## Decisions

### 決策 1：白名單驗證放在 router 層，`service.create` 保持不驗

`create_from_web_fallback`（`service.py:63`）內部就是呼叫 `create`（`service.py:75`）。如果把 `assert_allowed_urls` 塞進 `create`，白名單日後一收緊（例如 change 1 之後 ops 把某個網域從 env 拿掉，或 change 1 的正規化把某種 URL 判成不合法），自動建報就會拋例外——而 `web_search_service.py:98` 的 `except Exception: logger.exception(...)` 會把它吞掉。使用者仍然拿到網路答案，回報卻靜默不見了，而且只在 log 裡留一行。這種失敗模式沒有人會發現。

因此驗證的位置就是「人工輸入進入系統的那個邊界」，也就是 router：

```
POST /api/knowledge-reports
  → CreateKnowledgeReportRequest（Pydantic：必填、數量、長度）        → 422
  → assert_allowed_urls(body.user_source_urls)（change 1 提供）      → 400
  → 配額檢查                                                        → 429
  → service.create(..., source="manual")
```

分工的理由：**422 給「你少填了東西」，400 給「你填的網址不能收」。** 前者前端表單自己就會先擋下，使用者不該看到；後者必須向使用者解釋清楚，所以要走 `HTTPException` 帶可讀的結構化 detail，而不是 Pydantic 的錯誤格式（前端 `parseError` 會把 Pydantic 的 detail 陣列 `JSON.stringify` 出來，那對長輩使用者是不可讀的）。

`KnowledgeReportService.create` 只多一個 `source: str | None = None` 參數，其餘不變。它仍然是「把一筆回報寫進去」，不承擔「這筆回報從哪來、可不可信」的判斷。

### 決策 2：`question` 用說明欄的內容（選項 b）

表單只有 URL 與說明兩欄，但 `KnowledgeReport.question`（`app/models/knowledge_report.py:35`）是必填。三個選項：

| 選項 | 內容 | 評估 |
| --- | --- | --- |
| (a) 表單多一個欄位 | 使用者另填「問題」 | **否決**。直接違反「表單只有兩個欄位」的產品決定，且對長輩使用者而言，多一個必填自由文字欄就是一個放棄點。而且「問題」與「說明」在這個情境裡本來就難以區分——使用者要說的就是「這頁過時了」，硬要拆成兩段只會拿到兩段重複的文字。 |
| (b) 用說明欄填入 `question` | 前端把說明欄的文字同時送進 `question` 與 `user_note` | **採用**。零後端連鎖改動；`question` 的欄位說明本來就寫「使用者問題或知識缺口描述」，「這頁資料已過時」完全落在「知識缺口描述」裡。既有的列表卡片主標題（`KnowledgeReports/index.tsx:277`）、Featured 卡片 `<h2>`（:190）、admin 佇列的標題全部照舊有東西可顯示。 |
| (c) 改後端讓 `question` 變選填 | `min_length=1` 拿掉、model 改 `Optional` | **否決**。連鎖成本最大且全部落在別人身上：`openspec/specs/knowledge-reports/spec.md:8` 明文要求每筆回報含 `question`；`KnowledgeReport.question` 是必填，改成選填等於所有既有讀取點都要處理 `None`；前端使用者列表與 admin 佇列都以 `question` 當卡片標題，會出現一排無標題卡片；admin 少掉一個判斷依據，與本 change「讓 admin 一眼看得出該不該收」的目的相反。 |

**採用 (b)**，並明確兩件事：

1. 複製由 **前端** 做，不是後端偷偷把 `user_note` 抄到 `question`。API 契約維持誠實——`question` 仍是必填、仍由呼叫端提供。後端若做隱式複製，日後任何新呼叫端都會踩到一個沒寫在契約裡的行為。
2. 已知代價：手動回報的 `question` 與 `user_note` 內容相同。因此詳情 Dialog 在 `question === user_note` 時只顯示一次，避免同一段文字上下相鄰出現兩遍。日後若表單真的加了第三個欄位，兩者自然分岔，不需要改 API。

### 決策 3：agent tool 的 URL 維持選填，且採「過濾」而非「拒絕」

`submit_knowledge_report` 的 `user_source_urls` 維持 `list[str] | None = None`。

強制必填的後果不是「LLM 會去找一個正確的連結」，而是「LLM 為了完成工具呼叫而生一個看起來對的連結」。而幻覺出來的連結最可能長成 `https://www.hpa.gov.tw/<編出來的路徑>`——它會通過白名單（白名單只看 host）、進入待審佇列、被 admin 當成使用者提供的來源、然後被核准去 scrape。也就是說，把 tool 的 URL 改必填，會把「LLM 幻覺」直接接上「向量庫寫入」。這比沒有 URL 糟糕得多：沒有 URL 的回報 admin 會自己去找來源（`admin-knowledge-reports-ui` 已有這個機制），有一個假 URL 的回報 admin 反而傾向直接相信。

同時，tool 收到的 URL 若不合白名單，**過濾掉並記錄，不讓工具呼叫失敗**。理由：工具呼叫失敗會讓 agent 進入重試或改寫參數的迴圈，而它「修正」的方式就是換一個更像 `gov.tw` 的網址——又回到幻覺。過濾是靜默降級：回報照建，只是少了不可用的來源，admin 端行為與「使用者沒附來源」完全一致。

實作細節：`None` 必須維持 `None`，不可變成 `[]`——`tests/unit/tools/test_knowledge_report_tools.py:80` 正是靠這個斷言守住「tool 不強制 URL」，那個斷言要保留。

### 決策 4：新增 `source` 欄位

配額若把三條路徑都算進去，使用者只要在 LINE 多問幾個知識庫答不出來的問題，web fallback 就會替他建好幾筆報告，把手動表單的額度吃光——而使用者完全不知道自己「用掉」了什麼。所以配額必須只計手動建立的回報，這需要能區分來源。

考慮過用 `user_note == "auto:web-fallback"`（`service.py:79` 現行的魔法字串）當判別依據，否決：那個字串是給人看的備註，任何人改了文案就會把配額邏輯一起改壞，而且沒有任何測試會抓到。

因此新增 `KnowledgeReport.source: Optional[Literal["manual", "agent_tool", "web_fallback"]] = None`。舊紀錄沒有這個欄位，讀出來是 `None`，視為非手動、不佔配額——這個方向是安全的（寧可放行也不要誤擋既有使用者）。

`source` 的第二個用途是 admin 端：`agent_tool` 標記等於「這個 URL 是 LLM 給的，可能是幻覺」，正是 admin 在核准前最需要知道的一件事。

**邊界**：`source` 只用於（1）配額計數（2）admin 顯示。SHALL NOT 影響審核、ingest 或去重行為。這條寫進 spec，免得它變成第二個到處長分支的欄位。

### 決策 5：配額實作與參數

- 新增 `KnowledgeReportRepository.count_manual_by_line_user_since(line_user_id, since, collection=None)`，查詢 `{"line_user_id": ..., "source": "manual", "created_at": {"$gte": since}}`。
- 用既有的 `knowledge_report_line_user_created` 複合索引（`knowledge_report_repository.py:21`，`line_user_id` + `created_at`）即可，**不新增索引**；`source` 為索引外的殘餘篩選，在每人每日至多數十筆的量級下無意義。
- 視窗：滾動 24 小時（`now - timedelta(hours=24)`），不是自然日。自然日會出現「午夜前後可以送兩倍」的漏洞，而滾動視窗的實作成本完全相同。
- 上限：`KNOWLEDGE_REPORT_MANUAL_DAILY_QUOTA`，預設 10。挑 10 的理由：正常使用者一天不會回報 10 次；濫用者 10 次也塞不滿 admin 佇列。設成 env 是因為真實用量還沒有資料，第一版一定會調。
- 超過回 **429**，detail 帶 `code="quota_exceeded"` 與 `limit`，讓前端能組出「今天已達 {{limit}} 次」的文案。
- 檢查放在 router，與決策 1 同一個理由：自動路徑不該被使用者的手動配額擋住。

**已知限制（接受）**：檢查與寫入之間沒有原子性，同一使用者高度併發時可能超額 1–2 筆。要修就得上 `findAndModify` 計數器或 Redis，對一個次要功能的濫用防護而言不划算。這裡防的是「有人手動狂送」，不是「有人寫腳本打併發」——後者本來就需要全域 rate limit，那是另一個 change。

### 決策 6：手動回報不做去重，且把這件事寫進 spec

`delete_pending_or_reviewing_by_urls`（`knowledge_report_repository.py:217`）是 `delete_many`、filter 只有 `status` 與 `user_source_urls`、**不含 `line_user_id`**、硬刪且無 tombstone。

在只有 web fallback 呼叫它的現況下，這還算可接受：URL 由 Firecrawl 決定，使用者無法指定。表單一開放就完全不同了——使用者 A 貼上使用者 B 待審回報裡的 URL，B 的回報就被永久刪除，而且 B 送出時看到的「已送出」在下次開頁時會憑空消失。這不是去重，這是一個任何登入使用者都能呼叫的「刪除他人資料」原語。

所以手動回報 **不觸發任何刪除**。重複的 URL 會在 admin 佇列出現多筆，由 admin 目視處理——`IngestService.ingest_url` 對同 URL 本來就是覆蓋寫入，重複核准不會產生重複的 chunk，代價只有 admin 多點一次。

同時必須修改既有 spec：`openspec/specs/knowledge-reports/spec.md:83` 現在寫的是「建立自動（**或一般**）回報前……SHALL 刪除該舊回報」，字面上涵蓋手動路徑。這條要限縮成只適用 web fallback 自動建報，否則本 change 一上線就與 spec 相牴觸，而下一個人讀 spec 會認為「順手接上去重」是在補實作缺口。

日後若真要去重，正確的形狀是：限定 `line_user_id` 相同、且標記為 `duplicate` 而非硬刪。明確排除在本 change 外。

### 決策 7：前端不重新實作白名單，只揭露規則

白名單在 change 1 之後會變成 env／DB 可設定。前端若硬編一份副本，兩邊必然漂移，而漂移的方向是最糟的那一種：**前端擋掉後端其實允許的網址**——使用者被拒、後端沒有任何紀錄、沒有人會發現。

所以前端只做兩件事：

1. **事前揭露規則，不揭露清單。** URL 欄位下方常駐一行說明：「目前只收政府衛教網站（網址含 gov.tw），例如 hpa.gov.tw、cdc.gov.tw。」講規則不列清單的理由是：完整清單會隨 env 變動、對使用者也沒有意義；而規則文案若哪天 ops 加了非 gov 的網域，只是變成「說得比實際保守」——這是安全的漂移方向（使用者可能少貼，但不會被莫名拒絕）。這個耦合記在這裡：**新增非 `gov.tw` 網域時，要同步更新這句 i18n 文案。**
2. **只做「明顯不是網址」的前端檢查**（空字串、無 host）。白名單一律交給後端判定。

考慮過的替代方案：新增 `GET /api/knowledge-reports/allowed-domains` 讓前端動態渲染說明文字。否決——為了一行說明文字增加一支公開端點與一次額外請求，而它解決的問題（文案與設定漂移）只在 ops 新增非 gov 網域時才出現，而那件事目前沒有計畫。

### 決策 8：錯誤文案方向

後端目前所有 `HTTPException` 的 detail 都是英文（`"Report not found"`、`"URL not in whitelist: ..."`），而前端 `knowledgeReportsApi.ts:51` 的 `parseError` 會把 detail 原樣顯示。直接沿用等於讓長輩使用者看到英文技術訊息。

因此建立端點的 4xx 回 **結構化 detail**：`{"code": ..., "urls": [...]}`，前端依 `code` 對應 i18n 文案。三個 code：

| code | HTTP | 觸發 |
| --- | --- | --- |
| `url_invalid` | 400 | change 1 的 `normalize_url` 回 `None`（含反斜線、控制字元、authority 段非 ASCII 等） |
| `url_not_allowed` | 400 | 正規化成功但不在白名單 |
| `quota_exceeded` | 429 | 24 小時內手動回報已達上限 |

分成兩個 URL code 而不是一個的理由：使用者的補救動作不同。`url_invalid` 是「你貼的東西有問題，重貼一次」，`url_not_allowed` 是「這個網站我們不收，不是你打錯」。混成一句「網址無效」會讓貼了正確 `youtube.com` 連結的人以為是自己打錯字，然後反覆重試。

`assert_allowed_urls` 一次回報全部不合格 URL 的設計（change 1 界面），必須一路傳到前端：detail 的 `urls` 是陣列，表單逐一列出，而不是只講第一個。使用者貼三個網址被拒兩個時，只講一個會讓他修完再送、再被拒一次。

文案方向（zh-TW，其他五語同義翻譯）：

- `url_not_allowed`：標題「這個網址目前無法收錄」／內文「我們只收政府衛教網站（網址含 gov.tw），例如 hpa.gov.tw、cdc.gov.tw。」／逐條列出被拒的網址／補救「如果這是政府網站，請確認網址有沒有多出奇怪的符號。你也可以只把情況寫在說明欄，我們會請人工判斷。」
- `url_invalid`：「這個網址看起來不完整或含有不該出現的符號，請重新複製一次完整網址。」
- `quota_exceeded`：「今天的回報次數已達上限（{{limit}} 次），明天再試。已送出的回報仍在審核中。」——最後一句很重要，否則使用者會以為剛才送的也沒進去。

刻意避免的措辭：「網址無效」「格式錯誤」「權限不足」。前兩者把「我們不收這個網站」誤導成「你打錯字」；第三個會讓人以為自己的帳號有問題。

### 決策 9：表單用 Dialog，不用獨立頁面；路由用 `/knowledge-reports/new`

- **Dialog**：知識回報頁本身就是「追蹤進度」的頁，送出後要立刻在同一頁看到新的一筆。Dialog 關閉即 `invalidateQueries` 並就地更新列表，不需要導頁再導回。表單只有三個輸入，不值得一個頁面。專案已有同型別的 `CARE-LIFF/src/pages/Medications/ReminderFormDialog.tsx` 可對齊。`components/ui/dialog.tsx` 已內建焦點鎖定、Escape、焦點歸位與背景鎖捲——這些對 LIFF webview 尤其重要。
- **仍加路由 `/knowledge-reports/new`**：Rich Menu 或 LINE 訊息可能要直接把使用者送進表單。該路由渲染 **同一個** `KnowledgeReportsPage`，只是掛載時自動開啟 Dialog；關閉 Dialog 時 `navigate('/knowledge-reports', { replace: true })`。這樣有深連結能力，又不複製一份頁面。兩條路由都包在既有的 `ProtectedRoute` 裡（`App.tsx:101`）。

考慮過的替代方案：獨立的 `/knowledge-reports/new` 頁面元件。否決——送出後要嘛導回列表（LIFF webview 導頁會重掛整個頁面、重打 API，長輩裝置上明顯卡頓），要嘛在新頁顯示成功狀態（那就變成兩個地方各自呈現同一份資料）。

### 決策 10：`report_id` 碰撞重試，位數維持 4

`_generate_report_id`（`service.py:35`）是 `KR-YYYYMMDD-` 加 4 碼（大寫字母＋數字，36^4 ≈ 168 萬）。生日悖論下，單日約 1500 筆就有 50% 機率碰撞。`repository.insert`（:32）直接 `insert_one`，而 `knowledge_report_id` 是 unique index（:16-20），碰撞就是 `DuplicateKeyError` → 未處理例外 → 500。前端會把它渲染成一段錯誤字串，出現在剛送出表單的畫面上——使用者與 admin 都會把它讀成「白名單擋掉了」。

修法：`create` 內迴圈最多 5 次，捕捉 `pymongo.errors.DuplicateKeyError` 就重新產生編號再試；5 次都撞才讓例外往上。5 次全撞的機率在任何真實負載下都可忽略，而無上限的 `while True` 在 unique index 因其他欄位而衝突時會變成無窮迴圈。

**位數維持 4 碼。** 提到 6 碼可以把碰撞率降三個數量級且成本為零，但那會讓 `tests/unit/services/knowledge_reports/test_service.py:87` 的 `assert len(report.report_id.split("-")[-1]) == 4` 變紅，而重試本身已經把這個問題從「500」降級為「多一次 insert」。位數是次要優化，留給需要它的人做。

## 考慮過的替代方案與否決理由

| 替代方案 | 否決理由 |
| --- | --- |
| 把 `assert_allowed_urls` 放進 `service.create` | `create_from_web_fallback` 內部呼叫 `create`，而該呼叫的失敗會被 `web_search_service.py:98` 的 `except Exception` 吞掉。白名單一收緊，自動建報靜默停止，只在 log 留一行。 |
| `create(require_urls: bool = False)` 參數 | 比上一項好，但預設 `False` 讓真正的規則藏在呼叫點：任何人複製一個新的呼叫點都會預設拿到不驗證的版本，而那正是需要驗證的那種呼叫點（新的人工入口）。同時 service 要 import whitelist、單元測試矩陣加倍。router 層驗證則讓「誰要驗」在路由定義上一眼看得到。 |
| 用全域 rate limit middleware 取代端點配額 | 全 app 目前只有 CORS middleware，加一層全域中介層會影響 LINE webhook 等所有路徑，風險遠大於一個次要功能該承擔的。且 IP 級限流對 LINE webview 的使用者無效（可能共用出口 IP）。 |
| 用 `user_note == "auto:web-fallback"` 判別自動回報，不加 `source` 欄位 | 那是給人看的備註文案，改文案就會把配額邏輯一起改壞，且沒有任何測試會抓到。 |
| 手動回報沿用 `delete_pending_or_reviewing_by_urls` 去重 | 該函式 filter 不含 `line_user_id`、是硬刪、無 tombstone。開放表單後等於給每個登入使用者一個刪除他人資料的原語。 |
| `question` 改為選填 | 牽動 spec、domain model、使用者列表卡片標題、admin 佇列標題四處，且讓 admin 少一個判斷依據——與本 change 目的相反。 |
| 前端複製一份白名單做即時擋 | change 1 之後白名單可設定，副本必然漂移；漂移方向是「前端擋掉後端允許的網址」，使用者被拒而後端無紀錄，最難發現。 |
| 表單另開獨立頁面 | LIFF webview 導頁會重掛頁面、重打 API；送出後不論導回或就地顯示成功都比 Dialog 差。 |
| 讓 agent tool 的 `user_source_urls` 也必填 | 等於要求 LLM 在沒有連結時自己生一個。幻覺的 `gov.tw` URL 會通過白名單、進佇列、被 admin 誤信為使用者提供的來源、核准後被 scrape。 |
| tool 收到非白名單 URL 時讓工具呼叫失敗 | agent 會重試並「修正」參數——修正的方式就是換一個更像 `gov.tw` 的網址，又回到幻覺。改為靜默過濾。 |
| 把 `report_id` 亂碼加長到 6 碼取代重試 | 只降低機率不消除問題，且會讓 `test_service.py:87` 的長度斷言變紅。重試才是正解。 |

## Risks / Trade-offs

- **[admin 佇列被灌爆]** 表單開放後待審量會上升。→ Mitigate：每人 24 小時 10 筆配額；`admin-knowledge-reports-ui` 已有分頁與各狀態計數，佇列規模是看得見的。若配額仍不足，調 env 即可。
- **[使用者貼了正確但非 gov.tw 的權威連結被拒]** 例如醫學會或醫院網站。→ Mitigate：錯誤文案明確給第二條路——「只把情況寫在說明欄」，讓 admin 自己去找來源（`admin-knowledge-reports-ui` 的 admin 補 URL 機制本 change 保留）。但這條路需要 `user_source_urls` 至少一個 URL 才送得出去，所以實際上使用者仍會卡住；這是「URL 必填」的已知代價，接受，因為它換到的是「admin 只看 URL + 說明就能判斷」。
- **[`question` 與 `user_note` 內容重複]** 資料層面確實冗餘。→ Mitigate：詳情 Dialog 去重顯示；日後加第三欄即自然分岔，不需改 API。
- **[配額檢查與寫入非原子]** 高併發下可超額 1–2 筆。→ 接受，見決策 5。
- **[breaking：`POST /api/knowledge-reports` 請求主體收緊]** → 既有呼叫端只有測試，前端從未呼叫過。
- **[依賴 change 1 尚未落地的界面]** `normalize_url` / `assert_allowed_urls` 由 change 1 提供。→ 本 change 的 tasks 以「change 1 已完成」為前提；若順序被打亂，`assert_allowed_urls` 缺席會讓 router 直接 ImportError，不會靜默降級。

## Migration Plan

1. change 1 `harden-url-whitelist` 完成並合併（反斜線繞過修好、`normalize_url` / `assert_allowed_urls` 可用）
2. change 2 `approve-with-content-preview` 完成並合併（admin 在核准前看得到實際抓到的內容）
3. 後端：model 收緊 → `source` 欄位 → repository 計數 → service `source` 參數與 `report_id` 重試 → router 驗證與配額 → tool 過濾
4. 前端：API function → 表單 Dialog → 路由 → 列表三處顯示補齊 → i18n 六語言
5. `./init.sh` 全綠、`CARE-LIFF` 的 `vitest` 全綠
6. 上線後觀察：admin 佇列 `source="manual"` 的筆數、400 `url_not_allowed` 的比率。若 `url_not_allowed` 比率偏高，代表事前揭露的文案沒被看到，優先改文案而不是放寬白名單。
