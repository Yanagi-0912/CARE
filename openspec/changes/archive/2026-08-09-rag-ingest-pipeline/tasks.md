## 1. Chunking

- [x] 1.1 新增 `app/services/rag/chunking.py`：`split_text_to_chunks`（標題／雙換行＋長度窗）
- [x] 1.2 單元測試：空字串、短文單塊、長文多塊、無空塊

## 2. IngestService

- [x] 2.1 新增 `IngestResult`＋`IngestService.ingest_url`（白名單→scrape→chunk→embed→replace by url）
- [x] 2.2 單元測試：reject／empty scrape／成功 insert／同 url replace（mock collection／embed／client）

## 3. CLI

- [x] 3.1 `scripts/ingest_url.py`：`--url`／`--source-name`／`--dry-run`，組裝 settings
- [x] 3.2 文件字串／`--help` 可用（可選 smoke：dry-run 對 mock 或略）

## 4. 收尾

- [x] 4.1 跑相關 unit tests
- [x] 4.2 勾選本 tasks
