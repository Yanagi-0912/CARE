## 1. 題庫與文件

- [x] 1.1 建立 `evals/rag/README.md`：抽題步驟、欄位說明、去識別化、如何跑評測、如何解讀 hit@k
- [x] 1.2 建立 `evals/rag/golden.jsonl`：含 schema 示範至少 5 題（kb／refuse 各有例子；web 可先 1 題標記）
- [x] 1.3（資料）從真實 LINE／常用測資補齊至 ≥30 題，並標好 `expected_url_substrings`（可用 `scripts/rag_query_cli.py` 輔助）
- [x] 1.4 支援缺 url 的 KB：`expected_source_substrings` + scoring 可依 `source_name` 命中（食藥署題）
- [x] 1.5 細標：`expected_content_substrings` + `scripts/rag_tighten_golden.py`；golden 收成 pid／關鍵句

## 2. 評測腳本

- [x] 2.1 新增 `scripts/rag_eval.py`：讀 JSONL、驗證必填欄位、呼叫 retriever（DI／既有 dependencies）
- [x] 2.2 實作 retrieval hit 判定（url substring）與摘要輸出；支援 `--out report.json`
- [x] 2.3 實作可選 `--with-answer`：呼叫 `RagAnswerService.answer`，計算 `source_hit`／`refuse_ok`
- [x] 2.4 可選 `--fail-under <float>`：hit 率低於門檻時 exit code ≠ 0
- [x] 2.5 支援 `--rank-mode`／`--top-n` 與 `--compare-rerank`（vector vs Cohere top-n）

## 3. 測試與驗證

- [x] 3.1 新增 `tests/unit/scripts/test_rag_eval_scoring.py`（或 `tests/unit/eval/`）：對 hit 判定純函式做單元測試（注入假 docs，禁止 monkey patch 全域）
- [x] 3.2 用範例 `golden.jsonl` 在本機對 staging／dev Mongo 跑一輪，確認報告可讀
- [ ] 3.3 `./init.sh`（或 pytest）全綠後 commit（待使用者指示）
- [x] 3.4 本機執行 `--compare-rerank` 並記錄 hit_rate_delta
