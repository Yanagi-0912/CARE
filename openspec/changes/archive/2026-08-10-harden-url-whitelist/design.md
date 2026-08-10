## Context

`app/services/rag/whitelist.py` 現行實作（全文 41 行）只做一件事：`urlparse` 取 hostname，比對四個硬編後綴。三個呼叫點共用它：

| 呼叫點 | 位置 | URL 來源 |
| --- | --- | --- |
| 網搜結果過濾 | `web_search_service.py:143` | Firecrawl search hits |
| 入庫前檢查 | `ingest_service.py:41` | admin 核准的 URL、CLI |
| 核准前檢查 | `knowledge_reports/service.py:160` | admin 在 LIFF 手打／回報帶上來的 |

三者的共同假設是「URL 由系統或營運產生，不會是刻意構造的」。change 3 之後這個假設不成立：URL 變成使用者必填欄位。

問題不在「Python 的 `urlparse` 有 bug」，而在**同一個字串，兩個解析器給出不同的 host**。我們用 Python 判斷，用 Node（Firecrawl）抓取，用瀏覽器（admin 點連結）顯示。已驗證的差異：

```
輸入                                  Python urlsplit().hostname     Node new URL().host
https://evil.com\.gov.tw/page         evil.com\.gov.tw   (放行)      evil.com          (實際抓 evil.com)
https://evil.com%5C.gov.tw/page       evil.com%5C.gov.tw (放行)      Invalid URL       (拋錯)
https://evil.com。gov.tw/             evil.com。gov.tw   (拒絕)      evil.com.gov.tw   (其實是合法的 gov.tw 子網域)
https://evil.com\t.gov.tw/x           evil.com.gov.tw    (放行)      evil.com.gov.tw   (兩邊都刪 tab，但顯示字串騙人)
```

前兩列是安全漏洞（我們說可信，抓取端去了別的地方）；第三列是誤判；第四列兩邊一致但**人眼看到的字串與機器解讀的 host 不同**。

## Goals / Non-Goals

**Goals:**

- 任何我們放行的字串，其 host 在 Python、Node、瀏覽器三者的解讀必須一致
- 顯示給 admin 的字串、存進 Mongo 的字串、送去抓取的字串，是同一個字串
- 允許清單可由營運調整，不必改程式碼
- 給下游一個「一次講完全部錯誤」的驗證入口
- 抓取後、寫入前再驗一次，讓重導向無法繞過事前檢查
- 全部可用 DI 測試，不 monkey patch `settings`

**Non-Goals:**

- SSRF 防護（私有網段、DNS rebinding、`file:`／`gopher:` 之外的協定）——白名單本身就是最強的收斂，加上 `gov.tw` 不會解析到內網
- 內容層的可信度判斷（頁面是不是廣告、是不是過期公告）——那是 change 2 的 admin 內容預覽要處理的
- 反收錄（把已進向量庫的 chunk 撤回）——系統目前沒有這個能力，本 change 不建
- IDN／punycode 支援（見 Decision 3）
- 使用者端建立回報時的硬擋（change 3）

## Decisions

### 1. 用「canonicalize 後比對」當主結構，黑名單只保留一個最小、有明確理由的前置檢查

**選擇**：`normalize_url()` 是唯一的入口，它把輸入轉成一個**受限文法**內的唯一字串，轉不出來就回 `None`。`is_allowed_url()` 判斷的對象一律是 `normalize_url()` 的輸出，不是原始字串。正規化尾端做**不動點檢查**：`normalize_url(out) == out`，且 `urlsplit(out).hostname` 必須等於正規化過程中認定的 host，兩者任一不成立就拒絕。

**為什麼不是黑名單式過濾**：黑名單要列舉的是「已知會造成解析歧異的字元」，而那份清單是由**我們不控制的**解析器定義的——Firecrawl 用的 Node 版本、admin 的瀏覽器、未來換掉的抓取服務。今天要擋反斜線，明天發現 `%5C`，後天發現 `。`（U+3002）／`．`（U+FF0E）／`｡`（U+FF61）都是 IDNA 的標籤分隔符。每加一個新解析器，黑名單就欠一輪稽核，而漏掉的預設是**放行**。canonicalize + 比對把預設翻過來：只有能被證明只有一種解讀的字串才通過，沒想到的攻擊手法預設**拒絕**。

**但「canonicalize 後與原字串比對」不能是全部**，這點必須講清楚，否則實作會做錯：Python 的 `urlsplit` 會**靜默刪除** `\t`／`\r`／`\n`（3.6.14 之後為對齊 WHATWG 而加的行為）。`https://evil.com\t.gov.tw/x` 經過 `urlsplit` 後 host 是 `evil.com.gov.tw`——一個完全合法、且會通過不動點檢查的正規字串。也就是說，**正規化器自己會把攻擊字串洗乾淨**。因此在剖析之前必須先拒絕「Python 剖析器已知會刪除的字元」：控制字元（U+0000–U+001F、U+007F）與所有空白（含 U+00A0）。這是一個五行、有具體理由、可以指著 CPython 原始碼說明的前置檢查，不是「把想得到的壞字元都列一列」。

同理，反斜線也在剖析前拒絕（`\` 在 WHATWG 是路徑分隔符、在 Python 是普通 host 字元），userinfo（`@`）也是（`https://www.hpa.gov.tw@evil.com/x` 兩邊都解成 `evil.com`，但貼在審核頁上人眼會讀成 hpa.gov.tw）。

**否決的替代方案**：

- *嚴格版「`normalize_url(s) == s` 才算合格」*：漂亮但不能用。既有測試的 `https://www.gov.tw/`、Firecrawl 回來的一半 hits、使用者手貼的 URL，全都不是正規形式。真要這樣做，`is_allowed_url` 會退化成「只接受我們自己產生的字串」，manual report 直接不可用。正確的層次是：**正規化允許輸入不規範，判定只認正規化後的結果**。
- *改用 `yarl` / `furl` / `w3lib` 之類的第三方 URL 函式庫*：多一個相依，而且它們大多也是包在 `urllib.parse` 上，不會自動解決 WHATWG 差異；真正對齊 WHATWG 的 Python 實作（`ada-url`、`can_ada`）是 C 擴充，為了 40 行的模組不值得。記為 open question。
- *把判定移到 Node 側（讓 Firecrawl 回報它解析出的 host）*：確實最準，但要多一次網路往返才能知道「這個 URL 能不能收」，而核准端點必須同步回答。且 change 3 要在使用者按送出的當下擋。

### 2. `normalize_url()` 做什麼、刻意不做什麼

**SHALL 做**（每一項都有理由）：

| 動作 | 理由 |
| --- | --- |
| 去頭尾空白 | 貼上時最常見的雜訊 |
| 無 scheme 時補 `https://` | `www.hpa.gov.tw/x` 是使用者最常見的貼法；不補等於必填欄位一直失敗 |
| scheme／host 小寫 | `HTTP://WWW.HPA.GOV.TW` 與小寫版是同一個資源；不統一會讓 Mongo 的 `url` 去重鍵失效 |
| 去 host 尾端的 `.` | `hpa.gov.tw.` 與 `hpa.gov.tw` 是同一台主機 |
| 去預設埠（http:80／https:443） | 同上，且避免 `:443` 版本繞過任何以字串比對的下游 |
| 丟棄 fragment | fragment 不會送到伺服器；保留只會讓 `#a` 與 `#b` 在向量庫變成兩份重複 chunk |
| 剝除追蹤參數 | `utm_source`／`utm_medium`／`utm_campaign`／`utm_term`／`utm_content`／`utm_id`／`gclid`／`fbclid`／`msclkid`／`yclid`／`igshid`／`mc_cid`／`mc_eid`。同一頁從 LINE 分享出來會帶不同的 utm，不剝掉就是同一頁重複入庫 |
| 根路徑補 `/`、非根路徑去尾斜線 | 與 WHATWG 的序列化一致，去重鍵才穩定 |

**SHALL NOT 做**（同樣每一項都有理由）：

| 不做 | 理由 |
| --- | --- |
| 不解析 `.`／`..` 路徑段 | WHATWG 會解、Python 不會，但**路徑不影響 host**，對信任邊界零貢獻；改寫路徑反而可能把好好的網址改成 404 |
| 不對 path／query 做百分比編解碼 | `%2F` 與 `/` 在路徑上語意不同，解碼會改變資源；編碼則會讓中文網址變得人眼不可讀（admin 要看的） |
| 不排序 query 參數 | 少數站台的參數順序有意義，收益（去重）遠小於風險 |
| 不移除非追蹤的參數（例如 `ref`、`id`、`nodeid`） | `nodeid` 就是 hpa.gov.tw 的頁面識別；`ref` 在部分站台是分頁參數 |
| 不做 DNS 解析、不發 HTTP | 正規化必須是純函式（可測、無 I/O、不會在核准端點上卡住） |
| 不判斷私有位址／內網 | 見 Non-Goals；白名單後綴比對已經把範圍收到 `gov.tw` 之類 |
| 不轉 punycode | 見 Decision 3 |

正規化 SHALL 冪等：`normalize_url(normalize_url(x)) == normalize_url(x)`。這條要有測試，因為下游會對已存的 URL 再跑一次（例如重試核准）。

### 3. authority 一律要求 ASCII，先不支援 IDN

**選擇**：authority 段出現任何非 ASCII 字元即拒絕，理由碼 `malformed`。

實測資料：`'evil.com。gov.tw'.encode('idna')` 得到 `b'evil.com.gov.tw'`，與 Node 的 `new URL('https://evil.com。gov.tw/').host === 'evil.com.gov.tw'` 一致——Python 的 `encodings.idna` 確實會把 U+3002／U+FF0E／U+FF61 當標籤分隔符。所以支援 IDN 在技術上不難，**做法必須是「編碼成 punycode 後再比對」而不是「拿原始 unicode 字串去 endswith」**（後者正是現行程式碼的做法，會誤判）。

**先不做的理由**：允許清單全是 ASCII 後綴；台灣政府網站沒有 IDN 主機名；而放行非 ASCII authority 會同時引入同形異義字（homograph）的顯示風險——admin 在審核頁看到的與實際連的可能不同。成本近乎零、收益近乎零、風險非零，先拒絕。

**若之後要開**：在 `normalize_url` 內做 `host.encode('idna').decode('ascii')`，把 punycode 形式（`xn--...`）當作正規形式存下來與顯示，**不要**顯示原始 unicode。這條寫在這裡是為了避免將來有人「順手支援一下 IDN」時走回頭路。

### 4. 預設允許清單：判準先於清單

現行四個後綴中，`hpa.gov.tw`／`cdc.gov.tw`／`mohw.gov.tw` 都被 `gov.tw` 完全涵蓋，是純冗餘。`parse_allowed_suffixes()` SHALL 收斂被涵蓋者（保留 `gov.tw`，丟掉三個子集），並在載入時 log 一行說明——否則營運看設定會誤以為「只收這四個機關」。

**判準**（一個網域後綴要進預設清單，五條全中才收）：

1. **機構層級的權威性**：政府主管機關、公立研究機構、國際衛生組織。不是「這一頁寫得好」——白名單的粒度是整個網域後綴，一收就是整站。
2. **內容穩定可長期存取**：有穩定網址、不是新聞快訊、不是使用者產生內容（UGC）、不是個人部落格。向量庫裡的 chunk 會活很久，來源必須也活很久。
3. **無商業銷售動機**：排除藥商、保健食品電商、醫美診所行銷站。
4. **註冊門檻構成實質限制**：`gov.tw` 只發給政府機關，這是白名單有效的根本；`org.tw`／`com.tw` 任何人都能註冊，收整個後綴等於沒收。
5. **對台灣使用者的可用性**：優先中文；英文站僅收 RAG 引用價值明顯高於翻譯成本者。

**預設清單**：

```
gov.tw,nhri.edu.tw,who.int,cdc.gov,nih.gov,medlineplus.gov
```

| 後綴 | 收的理由 |
| --- | --- |
| `gov.tw` | 涵蓋 hpa／cdc／mohw／fda／nhi／npa／ntuh／vghtpe 等全部台灣官方與公立醫院，取代原本四條 |
| `nhri.edu.tw` | 國家衛生研究院，公立研究機構、中文、衛教與流病資料品質高 |
| `who.int` | 世界衛生組織，判準 1／2／3 皆滿足 |
| `cdc.gov` | 美國 CDC，傳染病與疫苗資訊的最常被引用來源 |
| `nih.gov` | 涵蓋 `nlm.nih.gov`、`ncbi.nlm.nih.gov` |
| `medlineplus.gov` | NIH 的民眾衛教入口，可讀性明顯優於 PubMed |

**考慮過但未納入**：

- `edu.tw`（整個後綴）：包含學生個人首頁、系所公告、社團網站，違反判準 1 與 4。要收特定大學醫院請逐一列（且台大醫院是 `ntuh.gov.tw`，已在 `gov.tw` 內）。
- `org.tw`（整個後綴）：違反判準 4。個別醫學會（例如 `pediatr.org.tw`）品質不錯，但那應該是營運**逐一**加進 env，不是收整個後綴——這正是「白名單可設定」要換到的彈性。
- `.gov`（美國整個後綴）：只發給美國政府機構，看似安全，但包含地方政府與政治性內容，違反判準 1 的「主管機關」與判準 5。明列 `cdc.gov`／`nih.gov`／`medlineplus.gov` 即可。
- `fda.gov`（美國 FDA）：與 `fda.gov.tw`（台灣食藥署）在來源清單上並列會讓使用者混淆藥品許可證的管轄，判準 5 不過。
- `mayoclinic.org`、`clevelandclinic.org`：內容品質高，但是私立醫療機構的行銷／招攬管道之一，判準 3 有疑慮。
- 維基百科、各大新聞網：判準 1、2 皆不過。

**這條判準直接決定 change 3 可不可用**，所以要說清楚它其實沒有想像中那麼緊：知識庫裡的內容**只可能**來自白名單（`ingest_service.py:41` 是唯一入口），所以「這頁資料已過時」這類回報指向的 URL 一定在白名單內。白名單只會擋掉「請幫我收錄這個新來源」而那個來源不在清單上的情況——而那種回報就算讓它建立起來，也會在 change 2 的核准階段被同一份白名單擋掉，只是把失敗從 5 秒後延到三天後。這是 change 3 選擇「建立當下硬擋」的主要論據。

### 5. 網搜的 `site:` 篩選與入庫白名單解耦

`WHITELIST_SEARCH_SITE_FILTER = "site:gov.tw"`（`whitelist.py:12`）目前註解寫「後綴皆屬 *.gov.tw，單一 site:gov.tw 即可涵蓋」。清單擴充後這個推導不成立了，但**不應該**自動改成 `site:gov.tw OR site:who.int OR ...`：

- Firecrawl 對多個 `site:` 的 OR 支援不確定，語法不吃就等於整條網搜失效，直接砸在使用者臉上
- 網搜階段要的是「收窄召回、提高相關性」，入庫階段要的是「守住信任邊界」，兩者的最佳值不同

**選擇**：拆成 `RAG_WEB_SEARCH_SITE_FILTER`（預設 `site:gov.tw`），與 `RAG_ALLOWED_DOMAIN_SUFFIXES` 各自獨立。`with_whitelist_site_filter()` 行為與現況一致（既有測試 `test_web_whitelist.py:41` 不動），`_fetch_web_docs` 仍會用完整白名單過濾 hits——搜尋只是收窄，過濾才是把關。

### 6. `UrlPolicy` 用建構子注入，模組函式保留為薄包裝

專案硬性規則禁止 monkey patch。若允許清單是模組層常數，測試就只能 `patch('app.core.config.settings.RAG_ALLOWED_DOMAIN_SUFFIXES', ...)`，違規。

**選擇**：

```python
@dataclass(frozen=True)
class UrlPolicy:
    allowed_suffixes: tuple[str, ...]
    def normalize(self, raw: str) -> str | None: ...
    def is_allowed(self, raw: str) -> bool: ...
    def assert_allowed(self, urls: list[str]) -> list[str]: ...   # raise UrlNotAllowedError

def parse_allowed_suffixes(raw: str) -> tuple[str, ...]: ...      # 純函式，可直接測
def default_url_policy() -> UrlPolicy: ...                        # 讀 settings，production 用
```

模組層 `normalize_url` / `is_allowed_url` / `assert_allowed_urls` 委派給 `default_url_policy()`。這樣：

- whitelist 的單元測試建 `UrlPolicy(allowed_suffixes=("gov.tw",))` 直接測——建構子注入
- `IngestService` / `KnowledgeReportService` 新增 `url_policy: UrlPolicy | None = None` 參數，預設取 `default_url_policy()`——建構子注入
- `scripts/ingest_url.py:76`、`tests/unit/resources/test_medical_anti_fraud_seed_urls.py:18` 這些只想問「這個 URL 行不行」的呼叫端一行都不用改

**否決**：把 policy 塞進 FastAPI `dependency_overrides`。核准路徑的 service 是在 `app/dependencies.py:165` 模組載入時就組好的單例，router 層的 override 到不了 `IngestService`。

### 7. 錯誤契約：資料在後端，文案在呈現層

`assert_allowed_urls` 失敗時拋 `UrlNotAllowedError(invalid=[InvalidUrl(url, reason)])`，`reason ∈ {"malformed", "not_allowed"}`。`whitelist.py` **不 import FastAPI、不 import i18n**——它是純函式模組，讓它知道 HTTP 狀態碼會把信任邊界綁死在一個 transport 上（change 3 的 agent tool 路徑不走 HTTP）。

`KnowledgeReportService.approve` 捕捉後轉成：

```json
{"detail": {"code": "url_not_allowed",
            "invalid_urls": [{"url": "https://evil.com/", "reason": "not_allowed"},
                             {"url": "ht!tp://x", "reason": "malformed"}],
            "message": "以下 2 個網址未通過來源白名單：…"}}
```

`message` 走 `app/i18n/messages.py`（新增 `url.reject.summary`、`url.reject.reason.malformed`、`url.reject.reason.not_allowed`），取代 `service.py:163` 硬編的 `f"URL not in whitelist: {url}"`。

**只提供 zh-TW 與 en**，不進 `tests/unit/i18n/test_messages.py` 的 `REQUIRED_KEYS`（那份清單是 LINE 使用者面訊息的六語硬性要求）。這些字串只會出現在 admin 審核頁與 API 錯誤回應，受眾是營運。`t()` 對缺語系會退回 zh-TW（`messages.py:996-1001`），行為安全。這是刻意的取捨，不是漏做。

**否決**：`detail` 維持字串、把全部 URL 串在一起。前端無法區分「格式錯」與「網域不允許」，也無法逐一標紅是哪幾個輸入框有問題——而 change 3 的表單正需要這個。

### 8. 抓取後以最終 URL 二次驗證

`FirecrawlClient.scrape`（`firecrawl_client.py:79-111`）只回傳 `data.markdown`，最終 URL 被丟掉。改法：

```python
@dataclass(frozen=True)
class ScrapedPage:
    text: str
    final_url: str | None = None
```

`WebSearchClient` 協定新增 `scrape_page(url) -> ScrapedPage`；`FirecrawlClient.scrape_page` 從 `data.metadata` 取最終 URL（依序試 `url`、`sourceURL`），`scrape()` 保留為 `(await self.scrape_page(url)).text`，`web_search_service.py:149` 不受影響。

`IngestService.ingest_url` 改用 `scrape_page`，抓回來後：`final_url` 正規化並過白名單，不通過 → `IngestResult(status="rejected", message=<redirect_not_allowed>)`，**不 embed、不 delete、不 insert**。

**`final_url` 為 `None` 時**（Firecrawl 沒回 metadata）：視為「抓取端未回報」，以請求 URL 續行並 log 一行。這是**明知的 fail-open**，理由：Firecrawl 是黑箱，重導向發生在它內部，metadata 是我們唯一拿得到的證據；拿不到就一律拒絕會讓整條入庫在 Firecrawl 改版時全面停擺。殘留風險由 change 2 的 admin 內容預覽補——admin 看到的是**實際抓回來的內容**，內容不對就不會核准。這條殘留風險必須寫進 change 2 的 Context。

**否決**：改在 `IngestService` 內自己發一次 HTTP HEAD 追重導向。那是第二個抓取端，它看到的重導向不保證等於 Firecrawl 看到的（UA、地區、cookie 都不同），驗了也不算數，還多一次外連。

### 9. 向量庫的 `url` 欄位與去重鍵

`ingest_service.py:121` 用 `delete_many({"url": url})` 做 replace-by-url。改成正規化 URL 後，**既有文件是用正規化前的原字串存的**，直接改鍵會導致重新入庫時舊 chunk 沒被刪掉、變成同一頁兩份。

**選擇**：寫入的 `url` 欄位用正規化後的字串（顯示與引用都用它），刪除條件放寬成 `{"url": {"$in": [原字串, 正規化字串, final_url]}}`（去重後）。一次入庫就會把舊鍵收斂掉，不需要 migration script。

`final_url` 與請求 URL 不同且通過白名單時，額外寫一個 `final_url` 欄位，讓營運事後查得出「這份 chunk 實際上抓自哪裡」。`retriever` 的投影不含這個欄位，對 RAG 路徑零影響。

**否決**：直接把 `url` 改成 `final_url`。那會讓去重鍵隨重導向漂移（同一頁今天存 A、明天存 B），也會讓 `IngestJobResult.url` 與 admin 送出的 URL 對不起來。

## Risks / Trade-offs

- **[正規化改變 admin 送出的字串]** → 核准回應與 `ingest_job.selected_urls` 顯示的會是正規化後版本，與 admin 貼進去的可能略有不同（少了 utm、多了尾斜線）。可接受，而且是想要的：畫面上顯示的就是實際會抓的。
- **[預設清單擴充放大攻擊面]** → 從一個後綴變成六個，`nih.gov` 底下還有 PubMed 這種專業內容可能不適合直接餵給民眾。緩解：清單可用 env 收窄，營運隨時能砍回 `gov.tw`；且 change 2 之後 admin 看得到內容才核准。
- **[英文來源出現在「參考資料來源」]** → 使用者可能點進去看不懂。這是既有的 `line-reply-rules` 範圍，本 change 不處理，但擴充清單會讓它更常發生，記在這裡。
- **[`scrape_page` 是協定的破壞性變更]** → 所有 `IngestService` 的測試 double 都要補這個方法。範圍有限（`tests/unit/services/rag/test_ingest_service.py` 一個檔），且沒有第三方實作這個協定。
- **[400 `detail` 由字串變物件]** → 只有 LIFF admin 頁一個消費者，且它現在就會 `JSON.stringify` 非字串 detail，最差情況是訊息變醜而不是壞掉。仍在 tasks 內修掉。
- **[fail-open：`final_url` 缺失]** → 見 Decision 8。已知、有意、有補償控制（change 2）。
- **[誤擋合法輸入]** → 空白、控制字元、非 ASCII authority 一律拒絕，可能擋掉某些真的能用的奇怪網址。這是刻意選擇的方向：在使用者輸入面，「拒絕並說明原因」比「放行一個我們無法確定會連到哪裡的字串」便宜得多。錯誤訊息要能區分 `malformed` 與 `not_allowed`，讓使用者知道是「打錯了」還是「這個網站不收」。

## Migration Plan

1. 先落 `whitelist.py` 與其測試（純函式，無外部相依，可獨立跑綠）
2. 接 `config.py` / `.env.example`；不設 env 時預設清單生效，既有部署不需要改任何設定
3. 接 `ingest_service` / `firecrawl_client` / `web_client`（協定變更與其測試一起）
4. 接 `knowledge_reports/service.py` 與 LIFF 錯誤顯示
5. `./init.sh` 全綠。`tests/unit/resources/test_medical_anti_fraud_seed_urls.py` 必須維持綠——它是「預設清單沒有意外縮水」的迴歸線
6. 無 DB migration；向量庫的 `url` 鍵在下一次 re-ingest 時自然收斂（Decision 9）

## 對後續 change 的界面（change 2、3 依此撰寫）

```python
# app/services/rag/whitelist.py
def normalize_url(raw: str) -> str | None
def is_allowed_url(url: str) -> bool                      # 內部先 normalize
def assert_allowed_urls(urls: list[str]) -> list[str]     # 回正規化後清單；失敗拋 UrlNotAllowedError
class UrlNotAllowedError(Exception):  invalid: list[InvalidUrl]
class InvalidUrl:  url: str; reason: Literal["malformed", "not_allowed"]
class UrlPolicy:   allowed_suffixes: tuple[str, ...]      # 可注入
def parse_allowed_suffixes(raw: str) -> tuple[str, ...]
def default_url_policy() -> UrlPolicy

# app/services/rag/web_client.py
class ScrapedPage:  text: str; final_url: str | None
```

- **change 2（approve-with-content-preview）**：預覽抓取請走 `scrape_page`，顯示給 admin 的網址用 `final_url`（有的話）而非 admin 輸入的字串；Decision 8 的 fail-open 殘留風險要寫進該 change 的 Context。
- **change 3（manual-knowledge-report）**：建立端點對單一 URL 呼叫 `assert_allowed_urls([url])`，把 `InvalidUrl.reason` 對映成表單的欄位錯誤（`malformed` → 「網址格式不正確」；`not_allowed` → 「目前只收錄下列來源…」並列出 `allowed_suffixes`）。存進 `user_source_urls` 的必須是回傳的正規化字串。

### 界面已落地（tasks.md 9.4）

以上界面已於分支 `sdd/harden-url-whitelist`（CARE 端 HEAD `b7602a4`）實際落地並通過測試，change 2、3 可以開始依賴。下面是直接讀自原始碼的最終簽章，與上面設計草稿有出入之處以此為準（草稿沒列出 `UrlPolicy` 三個方法與 `WebSearchClient.scrape_page` 的完整簽章，此處補齊；其餘與草稿一致）：

```python
# app/services/rag/whitelist.py
DEFAULT_ALLOWED_DOMAIN_SUFFIXES: tuple[str, ...]  # 原 ALLOWED_DOMAIN_SUFFIXES 已更名

class InvalidUrl:                       # frozen dataclass
    url: str
    reason: Literal["malformed", "not_allowed"]

class UrlNotAllowedError(Exception):
    invalid: list[InvalidUrl]
    def __init__(self, invalid: list[InvalidUrl]) -> None: ...

class UrlPolicy:                        # frozen dataclass；建構子注入 allowed_suffixes，測試不必碰 settings
    allowed_suffixes: tuple[str, ...]
    def normalize(self, raw: str) -> str | None: ...
    def is_allowed(self, raw: str) -> bool: ...
    def assert_allowed(self, urls: list[str]) -> list[str]: ...  # 回正規化後清單；走完全部才一次拋錯

def parse_allowed_suffixes(raw: str) -> tuple[str, ...]: ...
def default_url_policy() -> UrlPolicy: ...              # lru_cache(maxsize=1) 單例，讀 settings.RAG_ALLOWED_DOMAIN_SUFFIXES
def normalize_url(raw: str) -> str | None: ...            # 薄包裝，委派 default_url_policy().normalize
def is_allowed_url(url: str) -> bool: ...                  # 薄包裝，委派 default_url_policy().is_allowed
def assert_allowed_urls(urls: list[str]) -> list[str]: ...  # 薄包裝，委派 default_url_policy().assert_allowed

# app/services/rag/web_client.py
class ScrapedPage:                      # frozen dataclass
    text: str
    final_url: str | None = None

class WebSearchClient(Protocol):
    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]: ...
    async def scrape(self, url: str) -> str: ...          # 保留，web_search_service.py 仍在用
    async def scrape_page(self, url: str) -> ScrapedPage: ...
```

驗證狀態：`tests/unit/services/rag/test_web_whitelist.py` 等本 change 新增／覆蓋的測試共 321 個全綠；CARE `pytest tests/` 共 1330 個測試全綠；CARE-LIFF（第 8 節錯誤訊息顯示，分支同名、commit `e80a68e`）`npx vitest run` 105 個測試全綠。

## Open Questions

- 是否值得引入真正對齊 WHATWG 的 Python URL 解析器（`ada-url`）取代 Decision 1 的前置檢查？現階段的答案是「不值得為 40 行模組加 C 擴充相依」，但若之後 URL 輸入面再擴大（例如開放使用者上傳含連結的文件），值得重評。
- 允許清單是否該從 env 移到 DB（讓 admin 在 LIFF 上自助增刪）？本 change 選 env，因為改清單是低頻、高風險的動作，走部署流程反而是好事。若營運端反覆要求加網域再說。
