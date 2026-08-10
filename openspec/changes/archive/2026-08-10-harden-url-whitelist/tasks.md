前置：無（本 change 是 `approve-with-content-preview`、`manual-knowledge-report` 的前置）。

測試一律用依賴注入：`UrlPolicy` 以建構子傳入自訂 `allowed_suffixes`；`IngestService`／`KnowledgeReportService` 以建構子傳入 `url_policy=` 與 test double；`FirecrawlClient` 以 `http_client=` 傳入 mock。**任何一項都不得用 `unittest.mock.patch` 改 `app.core.config.settings` 或模組層常數。**

## 1. whitelist 模組重寫（純函式，先跑綠）

- [x] 1.1 `app/services/rag/whitelist.py` 新增 `InvalidUrl`（`url` + `reason: Literal["malformed","not_allowed"]`）與 `UrlNotAllowedError(invalid: list[InvalidUrl])`；此模組 SHALL NOT import fastapi 或 i18n（驗證：`whitelist.py:84-94` 定義，`import` 區塊僅 `logging`／`re`／`dataclasses`／`functools`／`typing`／`urllib.parse`／`app.core.config`，無 fastapi／i18n）
- [x] 1.2 新增 `parse_allowed_suffixes(raw: str) -> tuple[str, ...]`：逗號分隔、trim、小寫、去前導 `.` 與 `*.`、去空項、**收斂被其他後綴涵蓋的冗餘項**、保持穩定順序；空字串回內建預設（驗證：`whitelist.py:275-301`；`test_web_whitelist.py::test_parse_allowed_suffixes_collapses_redundant`、`test_parse_allowed_suffixes_empty_returns_default`）
- [x] 1.3 新增 `UrlPolicy`（frozen dataclass，欄位 `allowed_suffixes: tuple[str, ...]`），方法 `normalize()`／`is_allowed()`／`assert_allowed()`（驗證：`whitelist.py:236-272`）
- [x] 1.4 實作 `UrlPolicy.normalize`：剖析前拒絕反斜線／控制字元（U+0000–U+001F、U+007F）／任何空白（含 U+00A0）；無 scheme 時補 `https://`；scheme 限 http/https；authority 拒絕 userinfo（`@`）與非 ASCII；host 小寫、去尾點、去預設埠；丟棄 fragment；剝除 `utm_*`／`gclid`／`fbclid`／`msclkid`／`yclid`／`igshid`／`mc_cid`／`mc_eid`；根路徑補 `/`、非根路徑去尾斜線；尾端做不動點檢查（`normalize(out) == out` 且 `urlsplit(out).hostname` 等於認定的 host），任一不成立回 `None`（驗證：`whitelist.py:111-233`；`test_web_whitelist.py::test_normalize_url_matches_expected`、`test_normalize_url_is_idempotent`。**已知偏離（合理）**：額外新增 `_HOST_RE` host 正字集規則，把原本只擋 authority 含 `%` 的個別檢查一般化成正向允許字元集，一次涵蓋 `<`／`>`／`^`／`|`／空標籤等 Node／瀏覽器也拒剖析的字元，理由見 `whitelist.py:60-77` 註解，屬 code review 後的強化，非漏做）
- [x] 1.5 `UrlPolicy.is_allowed`：先 `normalize`，再以標籤邊界比對後綴（`host == suffix` 或 `host.endswith("." + suffix)`）（驗證：`whitelist.py:245-255`；`test_web_whitelist.py::test_is_allowed_url_rejects_non_whitelist`〔`gov.tw.evil.com`／`evilgov.tw` 等案例〕）
- [x] 1.6 `UrlPolicy.assert_allowed(urls)`：回傳正規化後清單；**走完全部 URL** 再一次拋出含所有 `InvalidUrl` 的 `UrlNotAllowedError`，不得遇到第一個就中止（驗證：`whitelist.py:257-272`；`test_web_whitelist.py::test_assert_allowed_urls_reports_all_invalid`）
- [x] 1.7 新增 `default_url_policy()`（讀 settings，模組層 lru_cache 或單例），模組函式 `normalize_url`／`is_allowed_url`／`assert_allowed_urls` 委派給它；`ALLOWED_DOMAIN_SUFFIXES` 更名為 `DEFAULT_ALLOWED_DOMAIN_SUFFIXES`（驗證：`whitelist.py:29`、`304-334`；`grep -rn "\bALLOWED_DOMAIN_SUFFIXES\b"` 確認無殘留舊名）
- [x] 1.8 `with_whitelist_site_filter()` 改讀 `RAG_WEB_SEARCH_SITE_FILTER`（見 2.1），行為與現況等價（驗證：`whitelist.py:340-350`；`test_web_whitelist.py::test_with_whitelist_site_filter_appends_gov_tw`）

## 2. 允許清單設定化

- [x] 2.1 `app/core/config.py` 新增 `RAG_ALLOWED_DOMAIN_SUFFIXES`（預設 `gov.tw,nhri.edu.tw,who.int,cdc.gov,nih.gov,medlineplus.gov`）與 `RAG_WEB_SEARCH_SITE_FILTER`（預設 `site:gov.tw`）（驗證：`config.py:116-124`）
- [x] 2.2 `.env.example` 補上兩個變數與註解（說明「只有落在此清單的網址能進向量庫／能被核准／change 3 之後能被回報」）（驗證：`.env.example:82-86`，註解逐字含「能進向量庫、能被核准（change 3 之後也才能被使用者回報）」）
- [x] 2.3 `default_url_policy()` 載入時若發生冗餘收斂，log 一行 INFO 列出被丟掉的後綴（避免營運誤以為清單是逐字生效）（驗證：`whitelist.py:309-318`）

## 3. 抓取端與 ingest 後驗證

- [x] 3.1 `app/services/rag/web_client.py` 新增 `ScrapedPage(text: str, final_url: str | None = None)`；`WebSearchClient` 協定新增 `scrape_page(url) -> ScrapedPage`，保留既有 `scrape(url) -> str`（驗證：`web_client.py:14-31`，`scrape` 與 `scrape_page` 皆在協定內）
- [x] 3.2 `app/services/rag/firecrawl_client.py` 新增 `scrape_page()`：取 `data.markdown` 與 `data.metadata` 的最終 URL（依序試 `url`、`sourceURL`，皆無則 `None`）；`scrape()` 改為 `(await self.scrape_page(url)).text`（`web_search_service.py` 呼叫 `self.web_client.scrape(url)` 處不必改）（驗證：`firecrawl_client.py:79-127`；`test_firecrawl_client.py::test_scrape_page_returns_final_url_from_metadata`、`test_scrape_page_returns_none_final_url_when_metadata_missing`、`test_scrape_returns_markdown_text`）
- [x] 3.3 `app/services/rag/ingest_service.py`：`IngestService.__init__` 新增 `url_policy: UrlPolicy | None = None`（預設 `default_url_policy()`）（驗證：`ingest_service.py:26-43`）
- [x] 3.4 `ingest_url` 改用 `scrape_page`；抓取前以正規化後 URL 檢查，抓取後若 `final_url` 不為 `None` 且與請求不同則再驗一次，不通過即回 `status="rejected"` 且不 embed／不 delete／不 insert；`final_url` 為 `None` 時以請求 URL 續行並 log（驗證：`ingest_service.py:45-86`；`test_ingest_service.py::test_rejects_when_final_url_leaves_whitelist`、`test_accepts_when_final_url_stays_in_whitelist`、`test_missing_final_url_falls_back_to_requested`）
- [x] 3.5 寫入文件的 `url` 用正規化字串；`delete_many` 條件放寬為 `{"url": {"$in": [原字串, 正規化字串, final_url]}}`（去重後）；`final_url` 與請求不同時額外寫 `final_url` 欄位（驗證：`ingest_service.py:137-166`；`test_ingest_service.py::test_delete_many_covers_pre_normalized_key`）

## 4. 呼叫端接線與錯誤契約

- [x] 4.1 `app/i18n/messages.py` 新增 `url.reject.summary`／`url.reject.reason.malformed`／`url.reject.reason.not_allowed`（zh-TW + en；刻意不進 `tests/unit/i18n/test_messages.py` 的 `REQUIRED_KEYS`，理由見 design Decision 7）（驗證：`messages.py:1042-1059`；`test_messages.py` 的 `REQUIRED_KEYS` 元組確認不含任何 `url.reject.*` key）
- [x] 4.2 `app/services/knowledge_reports/service.py`：`__init__` 新增 `url_policy=`；`approve()` 的 `for url in normalized_urls` 迴圈改呼叫 `assert_allowed_urls`，捕捉 `UrlNotAllowedError` 後轉 `HTTPException(400, detail={"code": "url_not_allowed", "invalid_urls": [...], "message": t(...)})`；ingest 目標改用回傳的正規化 URL（驗證：`service.py:127-189`，以注入的 `self._url_policy.assert_allowed(...)` 呼叫，符合檔案開頭「一律用依賴注入」的約定；`test_service.py::test_approve_reports_all_invalid_urls`、`test_approve_registers_normalized_urls`）
- [x] 4.3 `app/services/rag/web_search_service.py:143`：hit URL 先 `normalize_url`，`None` 則跳過；`Document.metadata["url"]` 與後續 `_extract_source_urls` 帶出的都是正規化字串（驗證：`web_search_service.py:132-180`；`test_web_search_service.py::test_skips_hit_with_parser_divergent_url`、`test_document_url_is_normalized`）
- [x] 4.4 `app/dependencies.py`：`IngestService` 與 `KnowledgeReportService` 建構時明確傳入 `url_policy=default_url_policy()`，維持單一組裝點慣例（驗證：`dependencies.py:166-183`）
- [x] 4.5 `scripts/ingest_url.py` 的 `_dry_run` 沿用模組層 `is_allowed_url`，僅將輸出訊息由 `URL not in whitelist` 改為含原因碼；確認 CLI 仍可跑（驗證：`scripts/ingest_url.py:75-85`；手動執行 `python scripts/ingest_url.py --url https://evil.com/ --dry-run` 輸出 `message=url_not_allowed reason=not_allowed`，`--help` 正常）

## 5. 測試：whitelist（`tests/unit/services/rag/test_web_whitelist.py`）

全部以 `UrlPolicy(allowed_suffixes=("gov.tw",))` 建構子注入，不碰 settings。

- [x] 5.1 擴充既有 `test_is_allowed_url_rejects_non_whitelist` 的參數，補上**反斜線**類：`https://evil.com\.gov.tw/page`（現行實作放行，Node 實抓 `evil.com`）、`https://evil.com\@x.gov.tw/`（現行實作放行，Python host 為 `x.gov.tw`、Node host 為 `evil.com`）。兩者是本 change 的核心迴歸，動手前應先確認它們在舊碼上是紅的（驗證：`test_web_whitelist.py:51-52`，兩案例逐字存在）
- [x] 5.2 補**百分比編碼**類：`https://evil.com%5C.gov.tw/page`（現行實作放行，Node 直接 `Invalid URL`）、`https://hpa.gov.tw%2egov.tw/`、`https://www.hpa.gov.tw%2f.evil.com/`（驗證：`test_web_whitelist.py:55-57`）
- [x] 5.3 補**控制字元與空白**類：`https://evil.com<TAB>.gov.tw/x`（現行實作放行——`urlsplit` 會靜默刪除 tab，host 變成 `evil.com.gov.tw`）、`https://a.gov.tw<CR><LF>.evil.com/`、`https://www.hpa.gov.tw/a b`（現行放行；改為拒絕，理由是顯示字串與實際抓取字串不一致）、以及把其中的空白換成 U+00A0 的變體（驗證：`test_web_whitelist.py:61-64`，含 `evil.com\xa0.gov.tw/x` 的 U+00A0 變體）
- [x] 5.4 補 **`@`（userinfo）** 類：`https://www.hpa.gov.tw@evil.com/x`、`https://www.hpa.gov.tw:pass@evil.com/`（驗證：`test_web_whitelist.py:67-68`）
- [x] 5.5 補 **IDN／非 ASCII authority** 類：`https://evil.com。gov.tw/`、`https://台灣.gov.tw/x`（本 change 一律拒絕，reason=`malformed`）（驗證：`test_web_whitelist.py:72-73`）
- [x] 5.6 補**標籤邊界**迴歸：`https://gov.tw.evil.com/`、`https://notgov.tw.example.com/`、`https://evilgov.tw/`（沿用既有案例並補最後一個）（驗證：`test_web_whitelist.py:40-41,46`）
- [x] 5.7 新增 `test_normalize_url_*`：無 scheme 補全（`www.hpa.gov.tw/x` → `https://www.hpa.gov.tw/x`）、大小寫（`HTTP://WWW.HPA.GOV.TW/X` → host 小寫、path 保留大小寫）、去尾點、去預設埠（`:443`）、丟 fragment、剝 utm（`?utm_source=line&nodeid=1` → `?nodeid=1`）、根路徑補 `/`、非根路徑去尾斜線（驗證：`test_web_whitelist.py::test_normalize_url_matches_expected`，`NORMALIZE_TABLE` 8 組案例逐一涵蓋上述子項）
- [x] 5.8 新增 `test_normalize_url_is_idempotent`：對 5.7 全部案例斷言 `normalize(normalize(x)) == normalize(x)`（驗證：`test_web_whitelist.py::test_normalize_url_is_idempotent`）
- [x] 5.9 新增 `test_normalize_url_returns_none_for_non_http_scheme`：`javascript:alert(1)`、`file:///etc/passwd`、`data:text/html,x`（驗證：`test_web_whitelist.py::test_normalize_url_returns_none_for_non_http_scheme`）
- [x] 5.10 新增 `test_parse_allowed_suffixes_collapses_redundant`：`"gov.tw, hpa.gov.tw ,CDC.GOV.TW,,.mohw.gov.tw"` → `("gov.tw",)`；並斷言空字串回內建預設（驗證：`test_web_whitelist.py::test_parse_allowed_suffixes_collapses_redundant`；空字串斷言實際落在另一個測試 `test_parse_allowed_suffixes_empty_returns_default`，兩者合起來涵蓋本項全部斷言）
- [x] 5.11 新增 `test_assert_allowed_urls_reports_all_invalid`：傳入 3 個（1 合法、1 `malformed`、1 `not_allowed`），斷言 `UrlNotAllowedError.invalid` 長度為 2 且 reason 正確、順序與輸入一致（驗證：`test_web_whitelist.py::test_assert_allowed_urls_reports_all_invalid`）
- [x] 5.12 新增 `test_assert_allowed_urls_returns_normalized`：合法輸入回傳的是正規化後字串（非原字串）（驗證：`test_web_whitelist.py::test_assert_allowed_urls_returns_normalized`）
- [x] 5.13 確認 `test_is_allowed_url_accepts_whitelist_domains` 與 `test_with_whitelist_site_filter_appends_gov_tw` 維持綠（含 `https://165.npa.gov.tw/` 這種數字開頭標籤）（驗證：`test_web_whitelist.py:17-32,102-105`；`pytest tests/unit/services/rag/test_web_whitelist.py` 全綠）

## 6. 測試：ingest 與抓取端

- [x] 6.1 `tests/unit/services/rag/test_ingest_service.py` 的 `_make_service` 補上 `scrape_page` AsyncMock（回 `ScrapedPage`）並傳入 `url_policy=UrlPolicy(allowed_suffixes=("gov.tw",))`；既有 `test_rejects_non_whitelist_url`／`test_empty_scrape_no_write`／`test_successful_ingest_writes_docs`／`test_replace_same_url`／`test_embed_failure_no_mongo_write` 全部維持綠（驗證：`test_ingest_service.py:14-54` 的 `_make_service`；`pytest tests/unit/services/rag/test_ingest_service.py` 全綠）
- [x] 6.2 新增 `test_rejects_when_final_url_leaves_whitelist`：請求 `https://www.hpa.gov.tw/a`、`scrape_page` 回 `final_url="https://evil.com/a"` → `status="rejected"`，且 `embeddings.aembed_documents`／`collection.delete_many`／`collection.insert_many` 皆 `assert_not_awaited()`（驗證：`test_ingest_service.py:161-181`）
- [x] 6.3 新增 `test_accepts_when_final_url_stays_in_whitelist`：`final_url="https://www.hpa.gov.tw/a-new"` → `status="ok"`，寫入文件含 `final_url` 欄位（驗證：`test_ingest_service.py:184-200`）
- [x] 6.4 新增 `test_missing_final_url_falls_back_to_requested`：`final_url=None` → 正常寫入，文件不含 `final_url`（驗證：`test_ingest_service.py:203-218`）
- [x] 6.5 新增 `test_delete_many_covers_pre_normalized_key`：請求 `https://WWW.HPA.GOV.TW/a/?utm_source=line` → `delete_many` 的 `$in` 同時含原字串與正規化字串，`insert_many` 的 `url` 為正規化字串（驗證：`test_ingest_service.py:221-237`）
- [x] 6.6 `tests/unit/services/rag/test_firecrawl_client.py` 新增 `test_scrape_page_returns_final_url_from_metadata`（`http_client=` 注入 mock，payload 帶 `data.metadata.url`）與 `test_scrape_page_returns_none_final_url_when_metadata_missing`；確認既有 `test_scrape_returns_markdown_text` 等維持綠（驗證：`test_firecrawl_client.py:115-147`；`pytest tests/unit/services/rag/test_firecrawl_client.py` 全綠）

## 7. 測試：知識回報與網搜

- [x] 7.1 `tests/unit/services/knowledge_reports/test_service.py` 既有 `test_approve_rejects_non_whitelist_url`（現 336 行）維持綠（仍為 400）（驗證：`test_service.py:336-350`；`pytest tests/unit/services/knowledge_reports/test_service.py` 全綠）
- [x] 7.2 新增 `test_approve_reports_all_invalid_urls`：`selected_urls` 傳 2 個不合格 → `exc.value.detail["invalid_urls"]` 有 2 筆且含 reason，`mock_ingest.ingest_url.assert_not_awaited()`；service 以建構子注入 `repository=mock_repo, ingest_service=mock_ingest, url_policy=UrlPolicy(...)`（驗證：`test_service.py:353-395`，實際案例為 5 個 URL 中 3 個不合格，涵蓋範圍優於任務描述的 2 個）
- [x] 7.3 新增 `test_approve_rejects_backslash_lookalike_url`：`selected_urls=["https://evil.com\.gov.tw/x"]` → 400（本 change 的核心迴歸）（驗證：`test_service.py:398-421`）
- [x] 7.4 新增 `test_approve_registers_normalized_urls`：帶 utm 的合法 URL → `start_ingest_job` 收到的 `job.selected_urls` 是正規化後字串（驗證：`test_service.py:424-445`）
- [x] 7.5 `tests/unit/services/rag/test_web_search_service.py` 新增：hit URL 為 `https://evil.com\.gov.tw/x` 時不進 `Document`；合法 hit 的 `metadata["url"]` 為正規化字串（以建構子注入 fake `web_client`）（驗證：`test_web_search_service.py::test_skips_hit_with_parser_divergent_url`、`test_document_url_is_normalized`）
- [x] 7.6 `tests/unit/resources/test_medical_anti_fraud_seed_urls.py` 維持綠（預設清單未縮水的迴歸線）（驗證：`test_medical_anti_fraud_seed_urls.py::test_seed_urls_exist_and_allowed`，`pytest` 通過）
- [x] 7.7 `tests/unit/routers/test_knowledge_reports.py` 新增 approve 400 回應 body 形狀的斷言（`detail["code"] == "url_not_allowed"`），router 依賴以 `app.dependency_overrides[get_knowledge_report_service]` 注入 fake service（驗證：`test_knowledge_reports.py::test_admin_approve_400_body_shape`，第 218-251 行）

## 8. CARE-LIFF：錯誤訊息顯示

- [x] 8.1 `src/api/knowledgeReportsApi.ts:55-56`：`detail` 為物件且含字串 `message` 時取 `message`，否則維持既有 `JSON.stringify` 後備（驗證：CARE-LIFF `src/api/knowledgeReportsApi.ts:51-79` 的 `parseError`）
- [x] 8.2 `src/tests/adminKnowledgeReports.test.tsx` 新增：approve 回 400 且 `detail` 為 `{code, invalid_urls, message}` 時，dialog 顯示 `message` 而非 JSON（**已知偏離（合理）**：測試實際落在新檔 `src/tests/knowledgeReportsApiErrors.test.ts`，理由是 `adminKnowledgeReports.test.tsx` 檔頭用 `vi.mock('../api/knowledgeReportsApi', ...)` 把整個 API 模組換假，`approveKnowledgeReport` 永遠是 `vi.fn()`，測不到真正的 `parseError` 邏輯；新檔改用 stub `globalThis.fetch` 直接呼叫真正實作，見該檔案頂部註解。驗證：`knowledgeReportsApiErrors.test.ts` 6 個案例，含 `detail` 為 `{code, invalid_urls, message}` 時顯示 `message` 而非 JSON）

## 9. 收尾

- [x] 9.1 `./init.sh` 全綠（驗證：worktree 無獨立 `.venv` 且 `requirements.txt` 本分支未變更，改以共用的 `/Users/jamessu/Desktop/computersciencehomework/CARE/.venv/bin/pytest tests/ -q` 執行 init.sh 的核心步驟——`1330 passed`，與 init.sh 步驟 3 等價）
- [x] 9.2 CARE-LIFF `npx vitest run` 全綠（驗證：於 CARE-LIFF worktree 執行 `npx vitest run` —— `Test Files 19 passed (19)`、`Tests 105 passed (105)`）
- [x] 9.3 勾選本 tasks；commit（訊息說明修掉的是已實測的反斜線繞過）
- [x] 9.4 通知 change 2／3：`normalize_url`／`assert_allowed_urls`／`ScrapedPage` 界面已可用（見 design「對後續 change 的界面」）（驗證：`design.md` 「對後續 change 的界面」段落已補上「界面已落地」小節，列出讀自原始碼的最終簽章與測試結果）
