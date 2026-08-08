# CARE-data ETL 資料品質報告

**給誰看**：`Capoo0618/CARE-data` 的維護者
**寫這份報告的人**：CARE（LINE Bot 端）這邊，正在做 RAG 檢索品質的調查
**日期**：2026-08-08

這不是稽核，是我們這邊在排查 RAG 檢索表現時，意外發現瓶頸有一大部分在資料端，
所以把實測到的東西整理過來，看看有沒有機會一起把知識庫品質往上推一階。
所有數字都是這幾天實測得到的，方法都寫在最後一節，歡迎直接複核或挑戰。

---

## 先說做得好的地方

在讀 `main_pipeline.py` / `scraper_api.py` / `scraper_tfc.py` / `test_system.py` 的過程中，
有幾個設計覺得值得說一聲：

- **Early Stopping 的動機是對的**：`upload_to_mongodb()`（`main_pipeline.py:49-94`）用「發現已存在資料就跳過該來源」
  來省 API quota、省向量化成本，這個方向沒有問題——問題只出在停止條件本身（見下面第 7 項），
  不是這個機制不該存在。
- **429 退避有做**：`get_embedding()`（`main_pipeline.py:25-47`）偵測到 `Quota exceeded` / `429`
  會 `time.sleep(40)` 重試，而不是直接讓整批失敗，這在向量化幾千個 chunk 時很重要。
- **GitHub Actions 排程很乾淨**：`.github/workflows/etl_pipeline.yml` 用 `cron: '0 0 * * *'`（UTC，等於台灣時間 08:00）
  搭配 `workflow_dispatch` 讓人可以手動補跑，Serverless 化這件事本身降低了維運負擔，也符合 README 說的
  「降低目標網站封鎖固定 IP 的風險」。
- **有動態一致性測試，而且測得很實在**：`test_system.py` 的 `test_02_api_data_integrity` /
  `test_03_tfc_data_integrity` 不是只測函式本身，而是「當下重新打一次來源網站／API，
  拿最新結果跟爬蟲模組的輸出比對」，這種測法能抓到爬蟲邏輯跟來源實際格式脫節的情況，比純
  mock 測試更有價值。

以下 8 項加 1 項次要問題，都是在讀程式碼、實際打 API、查詢線上 MongoDB 之後才寫下來的，
每一項都附上「現象 → 實測數據 → 根因 → 建議」，並且盡量給出對方可以自己重跑一次的方法。

---

## 1. Embedding 沒有指定 `taskType`，實際上等同全部編碼在「查詢空間」

**現象**：`get_embedding()`（`main_pipeline.py:25-47`）送給 Gemini `embedContent` 的 payload 是：

```python
payload = {"model": "models/gemini-embedding-001", "content": {"parts": [{"text": text}]}}
```

（`main_pipeline.py:27`）——沒有帶 `taskType`。Gemini embedding 模型支援 `RETRIEVAL_QUERY` /
`RETRIEVAL_DOCUMENT` 兩種不對稱的 taskType，用來讓查詢向量與文件向量落在不同但對齊的子空間，
理論上能提升非對稱檢索（短查詢 vs 長文件）的排序品質。目前 CARE-data 寫入的每一個 chunk 向量，
都是用「不指定 taskType」的方式算出來的。

**實測數據**：我們對同一段文字，實際呼叫三次 `embedContent`（不指定 / `RETRIEVAL_QUERY` /
`RETRIEVAL_DOCUMENT`），取得三組向量後算 cosine：

```
cos(未指定, RETRIEVAL_QUERY)    = 1.000000
cos(未指定, RETRIEVAL_DOCUMENT) = 0.927816
```

跟 `RETRIEVAL_QUERY` 完全重合（cosine = 1.0），跟 `RETRIEVAL_DOCUMENT` 有明顯差距。
結論：**「不指定 taskType」在數值上就等於 `RETRIEVAL_QUERY`**，所以目前整個知識庫的文件向量，
其實是用「查詢模式」編碼出來的。

**佐證數據（不是同一次測試，是另一批獨立量測，放在這裡互相印證）**：
我們對 CARE 端的 `$vectorSearch` 做了 top-40 抽樣（4 個 query，共 160 筆結果），
分數全部落在 **0.79–0.90** 之間，沒有一筆低於 0.5。更值得注意的是，一個**完全不相關**的
測試查詢（「幫我寫一首關於貓咪的詩」）拿到的分數是 0.79–0.82，跟真正相關的查詢（0.83–0.90）
幾乎重疊。這種「無論相不相關，分數都擠在同一個窄帶」的現象，跟「query 向量與 document 向量
其實落在同一個子空間、缺少不對稱檢索該有的區辨力」的推論方向一致——雖然我們沒有把這兩個現象
做因果實驗，但兩組獨立數據指向同一個方向，值得一併參考。

**根因**：`main_pipeline.py:25-47`，payload 缺 `taskType` 欄位。

**建議修改**：

```python
payload = {
    "model": "models/gemini-embedding-001",
    "content": {"parts": [{"text": text}]},
    "taskType": "RETRIEVAL_DOCUMENT",
}
```

**必須說清楚的界線**：這次只證實了「不指定 taskType 在數值上等同 RETRIEVAL_QUERY」這個事實，
以及「向量分數目前確實缺乏區辨力」這個現象。**改了 taskType 之後排序會不會實際變好、變多好，
我們沒有做 A/B 驗證，不知道**。CARE 端同時也要把查詢向量的 `embedContent` 呼叫改成
`RETRIEVAL_QUERY`（目前不指定，數值上剛好是對的，但顯式寫出來比較不脆弱），兩邊要一起改、
一起重新索引，才有意義做這個 A/B。這是下一步，不是這份報告能替雙方決定的事。

---

## 2. 標題只進了 embedding，沒有寫進 `chunk_content`

**現象**：`upload_to_mongodb()` 裡（`main_pipeline.py:74,80`）：

```python
vector = get_embedding(f"主題：{article['title']}\n內容：{chunk}")   # line 74
...
collection.insert_one({
    ...
    "chunk_content": chunk,   # line 80，只有 chunk 本身，沒有標題
    ...
})
```

向量化時有把標題接進去（`主題：{title}\n內容：{chunk}`），但落地存進 Mongo 的
`chunk_content` 欄位只有純 chunk 文字，標題不見了。

**三階段對照**：

| 檢索階段 | 用的欄位 | 有沒有看到標題 |
| --- | --- | --- |
| 向量檢索（`$vectorSearch`） | `embedding`（由含標題的字串算出） | 有 |
| BM25 / 全文檢索 | `chunk_content` | 沒有 |
| Cross-encoder rerank | `chunk_content` | 沒有 |

同一篇文章切出來的多個 chunk，內文可能都很短、很像（例如「...詳見附件...」），
只有標題能區分「這段在講什麼主題」。向量檢索因為標題有編碼進去，多少還吃得到這個訊號；
BM25 跟 rerank 完全看不到標題，等於少了一個對短 chunk 特別重要的訊號來源。

**根因**：`main_pipeline.py:74`（embedding 輸入含標題）與 `main_pipeline.py:80`
（落地欄位不含標題），兩處不一致。

**建議修改**：把 `f"主題：{title}\n內容：{chunk}"` 這個組合字串也寫進
`chunk_content`（或另外存一個 `contextualized_content` 欄位，讓 BM25 / rerank 都能用到），
而不是只用在 embedding 這一步就丟掉。

---

## 3. `clean_html` 用 regex 去標籤，不是 BeautifulSoup

**現象**：`utils.py:5-11`：

```python
def clean_html(raw_html):
    """清洗 HTML 標籤與特殊字元，回傳純文字"""
    if not raw_html: return ""
    clean_text = re.sub(r'<[^>]+>', '', raw_html)   # line 8
    clean_text = clean_text.replace('&nbsp;', ' ').replace('&rdquo;', '"').replace('&ldquo;', '"')
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()   # line 10
    return html.unescape(clean_text)
```

第 8 行 `re.sub(r'<[^>]+>', '', raw_html)` 只是把 `<...>` 這種標籤符號整串刪掉，
**不會連標籤內的文字內容一起處理**。如果來源 HTML 裡有 `<script>...</script>` 或
`<style>...</style>`，`re.sub` 只會拿掉 `<script>` 跟 `</script>` 這兩對標籤本身，
中間的 JS / CSS 原始碼會被當成普通文字留下來，混進最後的正文。

補充一個小澄清：README「技術架構」列了 BeautifulSoup，這確實有在用——`scraper_tfc.py`
用 `BeautifulSoup(response.content, 'html.parser')` 解析列表頁跟文章頁（抓連結、抓 `<p>`）。
但兩支爬蟲**共用**的文字清洗函式 `clean_html`（也就是真正決定「進資料庫的內文長什麼樣」
的那一步）本身沒有用到它，用的是純 regex。這是清洗這一步的實作方式，跟 README 講的技術棧
有落差，不是說 BeautifulSoup 完全沒被用到。

**根因**：`utils.py:5-11`，`clean_html` 用 regex 而非 DOM 解析器去標籤。

**建議修改**：改用 BeautifulSoup 處理：

```python
soup = BeautifulSoup(raw_html, "html.parser")
for tag in soup(["script", "style"]):
    tag.decompose()
clean_text = soup.get_text(separator="\n")
```

先 `decompose()` 掉 `script` / `style` 節點，再用 `get_text()` 取文字，就不會有標籤內容
混進正文的問題，也剛好可以順便解決下一項的段落結構問題。

---

## 4. 段落結構在清洗階段被壓平成一行

**現象**：`utils.py:10`：

```python
clean_text = re.sub(r'\s+', ' ', clean_text).strip()
```

這一行把「所有連續空白字元」（包含換行 `\n`、`\n\n`）全部壓成單一個空白字元。
也就是說，不管來源 HTML 原本有沒有段落分隔，清洗完之後**整篇文章變成一整行沒有段落標記的文字**。

**根因**：`utils.py:10`，`\s+` 這個 pattern 沒有區分「行內連續空白」跟「段落分隔（換行）」，
兩種情況都被壓成同一個空白字元。

**下游影響**：這件事本身不是 bug，但它跟第 5 項（固定字元數硬切）是連在一起的——
下游的 `chunk_text()`（`main_pipeline.py:17-23`）本來可以優先按段落邊界切，但因為段落資訊
在清洗階段就已經被抹掉了，`chunk_text()` 收到的是一整條沒有結構的字串，只能退而求其次，
按固定字元數硬切。

**建議修改**：清洗時保留段落分隔（例如換行處理成 `\n\n`），只壓縮「行內」連續空白，
不要把段落邊界也一起吃掉。如果採用第 3 項建議的 `get_text(separator="\n")`，
這一步可以自然而然一起解決。

---

## 5. 固定 500 字元硬切，會切斷句子——這是目前最值得優先處理的一項

**現象**：`chunk_text()`（`main_pipeline.py:17-23`）：

```python
def chunk_text(text: str, chunk_size=500, overlap=50) -> list:
    if not text: return []
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size])
        start += (chunk_size - overlap)
    return chunks
```

純粹按字元數（500 字元一段、重疊 50 字元）滑動窗口切片，不看句界、不看段落。

**實測數據**（查詢線上 `health_articles_chunks`，刪除導覽列噪音前的狀態）：

- chunk 長度分布：p25=248、median=500、p75=500、p90=500、max=1200、mean=384
- 有 **127 筆長度恰好 1 字元**（實際內容像 `'3'`、`'.'`、`'。'`、`'×'` 這種殘渣）
- 有 **480 筆長度 < 100 字元**（佔當時全庫 4,605 筆的 10.4%）
- 相鄰 chunk 的 overlap 實測確實是 50 字元，跟程式碼一致
- 斷句實例（某個 chunk 的開頭）：`'元整及55萬8,000元。國民健康署呼籲...'`——
  很明顯是從上一個 chunk 中間硬生生切開的殘句，句子本身已經不完整

**根因**：`main_pipeline.py:17-23`，`chunk_text()` 只用字元數切，沒有任何語意 / 句界邊界判斷。

**建議修改**：先按段落切（前提是第 4 項的段落結構有保留下來），超過 `chunk_size` 的段落再按
句界（`。！？`）切；切出來如果還是短到只剩標點或個位數字元，直接丟棄，不要進資料庫。

**這一項是這次調查中最有價值的線索，值得多花篇幅講清楚，因為它有兩組互相對照的實測數據撐著：**

**(a) 光是刪掉噪音資料，檢索指標就有可重現的提升。** CARE 這邊在 2026-08-08 從知識庫裡
刪掉了 266 筆 chunk——這些是 Firecrawl 抓取來源網站首頁時，把導覽列（選單連結、頁尾等
非正文內容）也切成了 chunk，混進了知識庫。這 266 筆只佔當時全庫 4,605 筆的 5.8%，
刪除前後用同一份 34 題 golden set、同一套 rerank 設定重新跑檢索指標：

| 指標 | 刪除前 | 刪除後 |
| --- | --- | --- |
| hit_rate@5 | 0.382 | **0.441** |
| mean_mrr | 0.217 | **0.241** |
| mean_ndcg@5 | 0.257 | **0.291** |

重跑一次拿到逐位元相同的數字（這套 eval harness 在程式碼與資料都固定的情況下是確定性的，
不是隨機取樣），所以這不是單次運氣波動。這次唯一變動的變數就是「刪掉了一批品質差的 chunk」，
檢索邏輯、rerank 設定、golden set 全部沒變，**因果歸因是站得住的**。

**(b) 相對地，同一時期做的兩項純檢索調參都沒有帶來增益**：把向量分數硬門檻整個拿掉
（`min_score` 從 0.5 改成 0），指標逐位元不變；把標題補進 reranker 的輸入文字，
`nDCG@5` 只多了 +0.004，接近雜訊水準，不能算有效提升。

把 (a) 跟 (b) 放在一起看，訊息很明確：**這次調查裡，能量到、可重現的檢索指標提升，
唯一來源是資料變乾淨；檢索邏輯端的調整目前都測不出效果。** 換句話說，
ETL 這邊產出的切片品質，是目前 CARE 整條 RAG pipeline 裡投資報酬率最高的改善點。
上面說的「500 字元硬切導致這些短 chunk / 斷句」是我們對成因的推測（下一節會再強調一次），
但「資料品質是目前的瓶頸」這件事，是有 A/B 對照數據撐著的。

---

## 6. 食藥署闢謠專區的文章沒有 URL

**現象**：`scraper_api.py:44`：

```python
raw_url = item.get("連結網址", item.get("url", item.get("Url", item.get("URL", item.get("連結", None)))))
```

這是兩個來源（食藥署、衛福部）共用的欄位擷取邏輯，會依序嘗試好幾種可能的鍵名。

**實測數據**：直接呼叫食藥署 API（`https://www.fda.gov.tw/DataAction`），實際回傳 702 筆，
欄位只有 `['標題', '內容', '附檔連結', '發布日期']`——**裡面沒有任何一個鍵名對得上
第 44 行嘗試的那幾種 url 欄位名**，`附檔連結` 這個欄位的實測值是**字串** `'None'`
（不是 JSON null，是真的四個字元的字串），本來就不是文章網址。所以第 44 行的 fallback
鏈條在食藥署這邊必然全部落空，`raw_url` 恆為 `None`。

反觀衛福部闢謠網站 API（`https://www.hpa.gov.tw/wf/newsapi.ashx`）欄位是
`['標題', '內容', '連結網址', '附加檔案', '發布日期', '修改日期']`，有 `連結網址`，
可以正常抓到。

這個落差反映到線上資料庫：目前 1,367 筆食藥署闢謠專區的 chunk（佔知識庫的 30%），
`url` 欄位**全部**是 `None`，對應到 706 個不重複標題。這些文章在 CARE 端目前永遠沒辦法
被列為「有連結的參考來源」。

**根因**：`scraper_api.py:44` 的 fallback 鏈條假設所有來源都會提供某種形式的 url 欄位，
但食藥署這支 API 的原始資料本身就不包含文章網址（`附檔連結` 是附件連結，不是文章連結）。

**建議修改**：兩個方向都可以，看哪個對你們比較划算：

1. 改抓食藥署闢謠專區的**網頁列表**（而不是這支 JSON API）來取得文章網址，跟 TFC 一樣走網頁爬蟲路線；或
2. 如果這支 API 本來就沒有網址可用，在文件裡明確標記「食藥署來源不具可連結網址」，
   下游（CARE 這邊）就可以改成用「來源名｜標題」的方式呈現，而不是嘗試附一個不存在的連結。

我們這邊也可以配合改，只是需要先知道哪個方向比較符合你們的規劃。

---

## 7. Early Stopping 的停止條件可能會漏抓文章

**現象**：`upload_to_mongodb()`（`main_pipeline.py:63-67`）：

```python
if collection.find_one(query):
    print(f"  ⏭️ [{source_name}] 發現已存在資料: {article['title'][:15]}...")
    print(f"     -> 🛑 觸發提早結束機制，跳過【{source_name}】後續所有文章！")
    skipped_sources.add(source_name)
    continue
```

只要在某個來源裡遇到**單一一篇**已經存在於 Mongo 的文章，就把整個來源加進
`skipped_sources`，這個來源後面所有文章（不管是不是真的已存在）全部跳過。

這個機制的動機很合理（省 quota、省向量化成本，前面已經提過），但它隱含一個假設：
**來源回傳的文章順序，必須嚴格按時間新到舊排列，而且每一篇都要成功寫入過**。
只要有一種情況不成立——例如來源 API 排序不是嚴格時間序、或某一篇文章曾經寫入失敗
（沒進 Mongo，但排在它後面、真正新的文章因為提早遇到「已存在」而被跳過）——
後續那些新文章就會永遠補不回來，因為下次跑 ETL 還是會在同一個位置提早停止。

**實測佐證**：衛福部闢謠網站 API 實際回傳 1,000 筆，但目前線上資料庫裡這個來源只有
910 個不重複 URL。1,000 vs 910 這個落差，跟「early stopping 提早停在某一篇、後面的新文章
沒被抓到」是一致的（我們沒有逐篇比對確認具體是哪些文章被漏掉，這裡只能說落差的方向跟
機制的已知風險吻合）。

**根因**：`main_pipeline.py:63-67`，停止條件是「遇到第一篇已存在的文章」，而不是
「確認整個來源都已經同步過」。

**建議修改**：兩個方向，難度不同：

1. 簡單版：改成「連續 N 篇都已存在才真的停止」（例如連續 3 篇），可以容忍偶爾的順序抖動；
2. 更穩健版：不做提早停止，改成先把來源目前的 URL / 標題集合抓下來，跟 Mongo 裡已有的做
   差集，只處理差集裡真正沒抓過的文章——這樣就不依賴「來源一定照時間排序」這個假設，
   quota 消耗也還是只花在真正新的文章上。

---

## 8. 只 `insert`，沒有 `update`，文章改版後知識庫不會更新

**現象**：`upload_to_mongodb()` 裡（`main_pipeline.py:76-85`）：

```python
collection.insert_one({
    "source_name": article["source"],
    "url": article["url"],
    "original_title": article["title"],
    "chunk_content": chunk,
    "chunk_index": i + 1,
    "total_chunks": len(chunks),
    "embedding": vector,
    "uploaded_at": time.time()
})
```

兩支政府 API 都有提供 `發布日期`（衛福部那支另外還有 `修改日期`），但目前這兩個欄位完全
沒有被存進 Mongo，寫入邏輯也只有 `insert_one`，沒有任何 `update_one` / upsert 的路徑。

**影響**：如果來源網站更新了某篇文章的內容（更正資訊、補充資料），CARE-data 目前的機制
（第 7 項提到的「發現已存在就跳過」）會直接判定這篇文章「已存在」而跳過，不會重新抓取，
知識庫裡留著的永遠是第一次抓到的舊版本。這跟 CARE 端「使用者可以回報某則資訊過時」的
需求方向是直接衝突的——即使使用者回報了，ETL 這邊目前也沒有機制能真的把內容更新掉。

**根因**：`main_pipeline.py:76-85` 只有 `insert_one`，沒有寫入日期欄位，也沒有 upsert 邏輯。

**建議修改**：把 `發布日期` / `修改日期` 存成 `published_at` / `updated_at` 欄位；
寫入邏輯改成先比對 Mongo 裡既有文章的 `updated_at` 跟來源目前的 `修改日期`，
不同就重新清洗、重新切片、重新向量化並覆蓋（`update_one` + upsert），而不是無條件 `insert_one`。

---

## 附帶一項次要事項：`verify=False`

`scraper_api.py:32`、`scraper_tfc.py:37`、`scraper_tfc.py:86` 這三處呼叫 `requests.get(...)`
都帶了 `verify=False`，關閉了 TLS 憑證驗證：

```python
response = requests.get(source['url'], headers=headers, timeout=15, verify=False)   # scraper_api.py:32
response = requests.get(page_url, headers=headers, timeout=15, verify=False)         # scraper_tfc.py:37
detail_res = requests.get(link, headers=headers, timeout=15, verify=False)           # scraper_tfc.py:86
```

對象都是政府網站（`fda.gov.tw`、`hpa.gov.tw`）跟 TFC 官網，正常情況下憑證應該是有效的，
關掉驗證等於放棄了「確認對方真的是該網域」這一層保護，沒有看到對應的理由註解說明為什麼
需要關閉。建議拿掉 `verify=False`（改回預設的 `verify=True`），如果之前是因為遇到過憑證
錯誤才加上去的，麻煩告知具體情境，我們可以一起評估怎麼處理比較好。這一項優先級比較低，
放在附帶項目裡。

---

## 建議不要做的事：改用 Firecrawl 全面取代目前的爬蟲

在整個調查過程中，有考慮過「乾脆全部換成 Firecrawl 之類的網頁爬取服務」這個方向，
但看過資料組成之後，覺得這樣做的效益不高，理由：

- 目前知識庫裡 **91% 的資料來自兩支會回傳結構化 JSON 的政府 API**（衛福部闢謠網站 +
  食藥署闢謠專區），這兩支本來就不需要「網頁爬取」，用 API 直接拿結構化資料反而比較穩定、
  比較不會因為網站改版而壞掉。
- CARE 這邊已經實際用過 Firecrawl 抓過來源網站首頁，結果是產生了前面第 5 項提到的
  266 筆導覽列噪音 chunk（已於 2026-08-08 從知識庫刪除）——網頁爬取工具如果沒有針對
  正文區塊做特別處理，反而容易把選單、頁尾這類非正文內容也切進去，不是萬靈丹。
- 真正需要網頁爬取的只有台灣事實查核中心（TFC）這個來源，目前是 132 筆 chunk，
  只佔知識庫的 2.9%，用現有的 `scraper_tfc.py`（BeautifulSoup + 翻頁邏輯）處理量級上是夠的，
  沒有急迫性要換工具。

所以這次的建議方向是「把現有 API 為主的架構打磨得更好」（也就是上面 8 項），
而不是引入新的爬取服務去解決一個佔比很小的問題。

---

## 這份報告如何被驗證

所有數字的取得方式都寫在這裡，希望收到報告的人可以自己重跑一次確認，而不是只能選擇相信：

- **分數分佈、chunk 長度分布、欄位覆蓋率（url=None 筆數等）**：直接查詢線上 MongoDB
  collection `health_articles_chunks`（例如用 `db.health_articles_chunks.aggregate(...)`
  算長度分布、`db.health_articles_chunks.countDocuments({url: None})` 算 url 缺失筆數）。
- **`taskType` 預設值等同 `RETRIEVAL_QUERY`**：對 Gemini `embedContent` 端點，用同一段文字，
  實際發送三種 payload（不指定 taskType / `RETRIEVAL_QUERY` / `RETRIEVAL_DOCUMENT`），
  取回三組向量後算兩兩 cosine 相似度比對。
- **政府 API 欄位**：直接對兩支 API 發 GET 請求（`https://www.fda.gov.tw/DataAction`、
  `https://www.hpa.gov.tw/wf/newsapi.ashx`），檢視回傳 JSON 的鍵名跟筆數。
- **檢索指標（hit_rate / mean_mrr / mean_ndcg@5）**：CARE 這邊的
  `python scripts/rag_eval.py --rank-mode cohere --top-n 5`，題庫是
  `evals/rag/golden.jsonl`（共 38 題，其中 34 題屬於有計分的 `route=kb` 且有期望來源的題目）。

**明確標示哪些是推測、不是實測**：

- 「500 字元硬切會導致 cross-encoder / rerank 表現不佳」是我們的**推測**，
  沒有做過控制實驗去單獨驗證這個因果關係——實測撐得住的是「資料變乾淨後指標會提升」
  （第 5 項的 (a)(b) 對照），至於「具體是硬切這個機制造成的」，是我們根據 chunk 長度分布
  跟斷句實例做的合理推論，不是量測結果。
- 「修正 `taskType` 之後排序會改善」目前**未知**，需要做 A/B 才能回答。這次只證實了
  「不指定 taskType 在數值上等同 RETRIEVAL_QUERY」這個事實本身，沒有測過改了之後
  排序指標會不會、會多少變好。

如果對任何一項的方法論有疑問，或想一起討論怎麼分工修（哪些在 CARE-data 這邊改、
哪些需要 CARE 那邊配合），隨時可以再聊。
