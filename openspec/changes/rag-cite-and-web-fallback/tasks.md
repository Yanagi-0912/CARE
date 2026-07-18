## 1. Cite 修復（信任 bug）

- [x] 1.1 調整 `app/services/rag/answer_service.py` 的 `_append_sources`：僅對實際輸出來源從 1 連續編號；支援可選網路來源標註格式
- [x] 1.2 新增／更新 `tests/unit/services/rag/test_answer_service.py`：跳過無 URL、重複 URL 後編號為 `[1][2]…`，不得出現斷號
- [x] 1.3 實作「無法回答」啟發式判定；無法回答時不呼叫附 KB 來源
- [x] 1.4 於 `tests/unit/services/rag/test_answer_service.py` 覆蓋：有 docs 但模型回「無法／不知道」類文字 → 不附「參考資料來源」

## 2. Web 客戶端與白名單

- [x] 2.1 新增 Web 搜尋／抓頁介面與 Firecrawl 實作（如 `app/services/rag/web_*.py`），經建構子注入，禁止 monkey patch
- [x] 2.2 實作白名單常數與 URL 過濾：`gov.tw`、`hpa.gov.tw`、`cdc.gov.tw`、`mohw.gov.tw`（含子網域）
- [x] 2.3 新增 `tests/unit/services/rag/test_web_whitelist.py`（或同級）：允許／拒絕網域案例
- [x] 2.4 新增 `tests/unit/services/rag/test_firecrawl_client.py`（或同級）：以注入 fake／mock 實例驗證 search／scrape 呼叫契約（不打真實網路）

## 3. RagAnswerService 串接 Web Fallback

- [x] 3.1 擴充 `RagAnswerService`：`docs` 空 → web；有 docs 但無法回答 → web；成功則標註「以下參考網路公開資料」並 cite ≤3 網路來源
- [x] 3.2 KB 與 Web 皆失敗 → 無法回答文案且不附來源；Firecrawl 逾時／錯誤同樣降級
- [x] 3.3 於 `tests/unit/services/rag/test_answer_service.py` 覆蓋：空 docs + web 成功；無法回答 + web 成功；web 失敗不附來源；同一答不混 KB／Web 來源（DI 傳入 fake web client）
- [x] 3.4 於 `app/dependencies.py` 組裝 Firecrawl client 並注入 `RagAnswerService`；補齊 `FIRECRAWL_API_KEY`（或同等）設定說明

## 4. 驗收與收尾

- [x] 4.1 執行 `./init.sh`（或等價 pytest）確認全綠；DoD：所有相關單元測試通過
- [x] 4.2 對照 `openspec/changes/rag-cite-and-web-fallback/specs/rag-responses/spec.md` 情景做一次手動／測試清單勾核
- [x] 4.3 建立清楚的 git commit（繁體中文說明本次 cite + web fallback）
