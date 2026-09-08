# 未命中側的相關衛教資訊補上出處

## Why

### 這是 rag-responses 既有規則在專案裡唯一沒有對齊的地方

`rag-responses` 明文要求「缺 url 的來源仍須顯示，不得靜默丟棄」。`get_rag_answer`
遵守它（`rag_answer_flex._source_buttons` 與 `RagAnswerService._append_sources`），
`verify_claim` 的**未命中側完全不呈現來源**——`_fetch_related_info` 把 `url`
從 metadata 讀出來只拿去去重，組字串時就丟掉了。

使用者看到的是：判定「證據不足」，下面一段沒有出處的衛教文字。那段文字是真的
從知識庫檢索出來的、有真實網址，只是在呈現層被丟掉。

### 官方闢謠內容只會走這條路

`main_pipeline` 的 ETL 只有查核型來源（TFC）產出 `verdict`，其餘來源為 `None`；
而 `MongoAtlasClaimMatcher` 的 `$vectorSearch` filter 是 `verdict ∈ 五個合法值`。
兩者合起來的結果是**只有 TFC 能命中 `matched=True`**。

食藥署闢謠專區那 199 篇、以及 `mohw-truth-clarification` 提案要爬的 810 篇官方
闢謠內容，`verdict` 全是 `None`，永遠進不了命中側——它們**只能**從
`related_info` 這條沒有來源的路徑露出來。缺口不是偶發，是結構性的，而且會隨那
個提案落地而擴大到約 1,009 篇。

### 附帶：沒有網址的來源完全沒有去重

`_fetch_related_info` 舊版把整段去重包在 `if url:` 裡，所以沒有網址的來源不去重。
而「食藥署公告」那 576 篇（`scraper_api` 的全站新聞稿 feed）上游結構上就沒有
網址，同一篇的多個 chunk 因此可以佔滿全部三個名額——與該函式 docstring 宣稱的
「同一篇最多一段」相反。

## What Changes

**`ClaimVerificationService`**
- `_fetch_related_info` 改為回傳 `(text, tuple[SourceRef, ...])`
- `VerificationResult` 新增 `related_sources`，命中時為空（與 `source_url` 互斥）
- 去重鍵沿用 `RagAnswerService._source_key` 的規則：url 優先，缺 url 時退回
  「來源名＋標題」
- 內容為空的檢查移到去重之前，空 chunk 不再佔掉該篇的去重名額

**`verdict_flex`**
- 「相關衛教資訊」區塊末尾加一行「資料來源：[n] 來源名」，**每一筆都列**，
  含沒有網址的
- 有網址者於 footer 產生按鈕；`_footer` 改收 list（與 `medical_news_flex._footer`
  的既有形狀一致）
- 免責說明仍排在來源之前

**`claim_tools._format_verdict_reply`**（純文字 fallback）同步列出出處。

**`_RELATED_INFO_TOP_K` 3 → 2。** 加上出處後卡片變大，而量測發現**取 3 筆在滿版
情況下本來就已經超過門檻**——不是這次改動造成的。實測條件為三篇候選、每篇一個
滿版 chunk（500 字），走 `verify()` 真實路徑量上線位元組，門檻 `SAFE_BUBBLE_BYTES`
= 9,216：

| 設定 | 位元組 | 是否通過 |
| --- | --- | --- |
| 取 3 筆、無出處（本次變更前） | 10,893 | ❌ |
| 取 3 筆、含出處 | 12,500 | ❌ |
| 取 2 筆、含出處 | 8,993 | ✅ |

也就是說這個值一直偏大，只是過去沒人量過——那種卡片一路都在無聲退回純文字。
新增迴歸測試把 TOP_K 與大小門檻的關係鎖住。

**刻意不做**：不讓官方澄清稿產出 `verdict`。實測真相說明 3 頁 60 筆標題，標題無
判定前綴、列表頁無分類欄位（與 TFC 的兩條抽取路徑都不同），且只有約 17% 是對特定
謠言的查核，45% 是衛教迷思問答、38% 是機關對媒體報導的行政聲明——後者給任何判定
都是錯的。詳見 `mohw-truth-clarification` 的討論。

## Impact

**呈現**：未命中卡片多一行文字與最多 2 顆按鈕，衛教資訊段數由 3 減為 2。既有的
大小門檻（見「判定卡呈現與來源標示」）仍是同一道防線，超過時退回的純文字版現在
也帶出處。滿版情況下的卡片由「超標退回純文字」變成「通過、維持 Flex」。

**相容**：`related_sources` 有預設值 `()`，既有呼叫端與測試不需要改；沒有出處時
卡片行為與本次變更前完全相同。

**未處理（已知）**：`related_info` 為空有四個成因（候選全被 `verdict` 濾掉、內容
全空、檢索例外、retriever 未注入），目前只有「檢索例外」留 log。無法從線上分辨
「過濾造成的空」與「檢索故障造成的空」。這與本次變更正交，另案處理。
