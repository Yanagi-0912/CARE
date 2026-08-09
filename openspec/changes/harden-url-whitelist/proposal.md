## Why

白名單（`app/services/rag/whitelist.py`）是「什麼內容可以進向量庫、可以被當成參考來源」的唯一信任邊界，但它目前只有 17 行、只看 `urlparse` 的 hostname，且硬編在原始碼裡。在把 URL 輸入面從「系統自己產生」（Firecrawl 搜尋結果、agent tool）擴大到「使用者自由輸入」（manual-knowledge-report）之前，這條邊界必須先補起來。

已實測確認的繞過（Python 3.13 / Node 24 皆已驗證）：

```
Python  urlsplit('https://evil.com\.gov.tw/page').hostname → 'evil.com\\.gov.tw'  → endswith('.gov.tw') → is_allowed_url = True
Node    new URL('https://evil.com\.gov.tw/page').host      → 'evil.com'           → href 'https://evil.com/.gov.tw/page'
```

WHATWG（瀏覽器、Node，Firecrawl 是 Node 服務）把反斜線視同斜線，Python 不會。同一個字串，admin 在審核頁看到的是像政府網址的連結，實際抓取的是 `evil.com`；抓回來的內容進向量庫、進 RAG prompt，回答末尾還掛著那個看起來像 gov.tw 的網址當參考來源。百分比編碼的變體 `https://evil.com%5C.gov.tw/page` 同樣通過現行檢查（Python hostname `evil.com%5C.gov.tw`），Node 端則直接 `Invalid URL`。既有測試 `tests/unit/services/rag/test_web_whitelist.py:37` 的拒絕案例完全沒有這一類。

另外三個結構問題：

1. **無法設定**：`ALLOWED_DOMAIN_SUFFIXES`（`whitelist.py:3`）硬編四個後綴，其中 `hpa.gov.tw`／`cdc.gov.tw`／`mohw.gov.tw` 都被 `gov.tw` 完全涵蓋，實際規則等同單一 `*.gov.tw`。要調整範圍必須改程式碼、重新部署。
2. **無正規化**：`www.hpa.gov.tw/x`（無 scheme）目前直接判不合格。自動來源不會產生這種字串，但使用者手貼的會，而且在必填情境下會造成大量「看起來像誤判」的失敗。
3. **抓取後不再驗證**：`IngestService.ingest_url`（`ingest_service.py:41`）只在抓取前驗一次；`FirecrawlClient.scrape`（`firecrawl_client.py:79`）只回傳 markdown 字串，重導向到哪裡、最終 URL 是什麼，呼叫端完全看不到。

## What Changes

- **修掉解析歧異**：`is_allowed_url` 在剖析前先拒絕含反斜線、控制字元、空白、userinfo（`@`）、authority 非 ASCII 的字串；判定改以正規化後的 host 為準。
- **新增 `normalize_url(raw) -> str | None`**：trim、補 scheme、小寫 host、去尾點與預設埠、丟棄 fragment、剝除追蹤參數、根路徑補 `/`；無法唯一化即回 `None`。明確定義刻意不做的事（不解 dot-segment、不動百分比編碼、不排序 query、不連網、不判私有位址）。
- **白名單改為可設定**：`app/core/config.py` 新增 `RAG_ALLOWED_DOMAIN_SUFFIXES`（逗號分隔，空值退回內建預設），`.env.example` 同步；預設清單收斂掉三個冗餘後綴並依明確判準擴充。網搜階段的 `site:` 篩選拆成獨立設定 `RAG_WEB_SEARCH_SITE_FILTER`，與入庫白名單解耦。
- **新增 `assert_allowed_urls(urls) -> list[str]`**：一次回報**全部**不合格 URL 與各自原因（`malformed`／`not_allowed`），取代 `service.py:159-164` 遇到第一個就 raise 的迴圈；錯誤文案改走 `app/i18n/messages.py`，不再硬編 `f"URL not in whitelist: {url}"`。
- **抓取後以最終 URL 二次驗證**：`WebSearchClient` 協定新增 `scrape_page() -> ScrapedPage(text, final_url)`，`FirecrawlClient` 從 `data.metadata` 取最終 URL；`IngestService.ingest_url` 在寫入前用最終 URL 再過一次白名單，不通過即 `rejected` 且不寫任何 chunk。
- **可注入的 `UrlPolicy`**：允許清單改由建構子傳入的 policy 物件持有，測試以 DI 換掉清單，不需要 monkey patch `settings`。模組層 `normalize_url`／`is_allowed_url`／`assert_allowed_urls` 保留為委派給預設 policy 的薄包裝，既有呼叫端與 `scripts/ingest_url.py` 不必改寫。

本 change **不含**：使用者端建立回報的表單與硬擋（change 3）、核准前的內容預覽（change 2）、速率限制、`delete_pending_or_reviewing_by_urls` 的跨使用者硬刪問題。

## Capabilities

### New Capabilities

- `url-trust`：**新增一個獨立 capability**。白名單目前散在兩處——`rag-responses` 只說「允許網域」、`knowledge-reports` 只說「通過白名單」，兩邊都只描述自己那一半，沒有一處定義「什麼叫通過」。而這條邊界同時被三個 domain 使用（RAG 網搜 `web_search_service.py:143`、入庫 `ingest_service.py:41`、審核 `knowledge_reports/service.py:160`），change 3 還會再加上第四個（建立回報時硬擋）。若把正規化與剖析歧異的規則塞進其中任一個既有 capability，另外三個引用時只能複製一次描述，之後就會各自漂移。獨立成 `url-trust` 後，「可信 URL 的定義」只有一份，其他 capability 以引用方式使用。

### Modified Capabilities

- `knowledge-reports`：核准端點的 URL 驗證語意——改為一次列出全部不合格 URL 與原因，且 ingest 目標使用正規化後的 URL。
- （`rag-responses` 不改）：該 spec 對白名單的描述只有「允許網域」四個字，不涉及判定方式；判定方式移交 `url-trust` 定義後，`rag-responses` 的需求文字仍然成立。且 `crag-web-fallback` 這個未 archive 的 change 已對 `rag-responses` 的「公開網路搜尋工具」「RAG 僅查知識庫」下了 REMOVED delta，本 change 若再動同樣的標題會在 archive 時互相打架。

## Impact

- **程式（CARE）**：`app/services/rag/whitelist.py`（重寫）、`app/services/rag/web_client.py`、`app/services/rag/firecrawl_client.py`、`app/services/rag/ingest_service.py`、`app/services/rag/web_search_service.py`、`app/services/knowledge_reports/service.py`、`app/core/config.py`、`app/i18n/messages.py`、`app/dependencies.py`、`scripts/ingest_url.py`、`.env.example`
- **API 契約**：`POST /api/admin/knowledge-reports/{id}/approve` 的 400 回應 `detail` 由字串改為物件 `{code, invalid_urls[], message}`。狀態碼不變、成功路徑不變、其他端點不受影響。使用者端 `POST /api/knowledge-reports` 本 change 不動。
- **程式（CARE-LIFF）**：`src/api/knowledgeReportsApi.ts:55-56` 目前對非字串 `detail` 直接 `JSON.stringify`，需補一條「物件且含 `message` 時取 `message`」，否則 `admin-knowledge-reports-ui` 既有場景「補上的 URL 未通過白名單 → 顯示後端回傳的原因」會退化成一坨 JSON。
- **測試**：`tests/unit/services/rag/test_web_whitelist.py`（大幅補強）、`tests/unit/services/rag/test_ingest_service.py`、`tests/unit/services/rag/test_firecrawl_client.py`、`tests/unit/services/rag/test_web_search_service.py`、`tests/unit/services/knowledge_reports/test_service.py`、`tests/unit/i18n/test_messages.py`、`tests/unit/resources/test_medical_anti_fraud_seed_urls.py`（應維持全綠，作為預設清單未縮水的迴歸線）；CARE-LIFF `src/tests/adminKnowledgeReports.test.tsx`
- **設定**：新增 `RAG_ALLOWED_DOMAIN_SUFFIXES`、`RAG_WEB_SEARCH_SITE_FILTER`；兩者皆有內建預設，未設定時行為與現況等價（除了本 change 修掉的繞過）
- **相依**：本 change 是 `approve-with-content-preview`（change 2）與 `manual-knowledge-report`（change 3）的前置；兩者都依賴 `normalize_url`／`assert_allowed_urls` 的界面
