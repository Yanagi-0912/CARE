# rag-eval Specification

## Purpose
TBD - created by archiving change rag-eval-harness. Update Purpose after archive.
## Requirements
### Requirement: Golden set 資料格式

系統 SHALL 提供可版本控管的 RAG 評測題庫（JSONL）。每一行 SHALL 為一題，且至少包含 `id`、`query`、`route`（`kb`｜`refuse`｜`web`）。針對 `route=kb` 的題目，題庫 SHOULD 提供 `expected_url_substrings`、`expected_source_substrings` 與／或 `expected_content_substrings`（非空字串陣列），供檢索命中判定使用；當知識庫文件缺少 `url` 時，SHALL 允許以 source／content 子字串計分。

題庫 SHALL 允許可選欄位 `expected_verdict`，其值為 `錯誤`｜`部分錯誤`｜`正確`｜`事實釐清`｜`證據不足` 之一，用於評測查核判定。標了 `expected_verdict` 為「證據不足」的題目 SHALL 表示「該主張不應命中任何已查核報告」，用以量測誤配。

#### Scenario: 合法題目列

- **WHEN** 評測腳本讀取題庫檔
- **THEN** 可解析每一行 JSON，且缺少必填欄位的列會被回報為錯誤而非靜默略過

#### Scenario: 標註期望判定

- **WHEN** 某題標有 `expected_verdict`
- **THEN** 該題同時參與判定正確率計分，且其值限於五種合法判定

### Requirement: 檢索命中粗指標

評測腳本在 retrieval 模式下 SHALL 對每題執行知識庫檢索，並可選擇在計分前以向量分數或 Cohere 精排截斷至 top-n。對 `route=kb` 且具期望 url／source 子字串的題目，若回傳文件的任一 `url` 或 `source_name` 包含對應期望子字串，則視為 hit。腳本 SHALL 輸出整體 hit 比例與未命中題目 id。

#### Scenario: kb 題命中期望來源

- **WHEN** 某題 `route` 為 `kb`，且檢索結果中至少一筆 url 包含其 `expected_url_substrings` 之一
- **THEN** 該題記為 retrieval hit

#### Scenario: kb 題以 source_name 命中（缺 url）

- **WHEN** 某題僅標 `expected_source_substrings`，且檢索結果中至少一筆 `source_name` 包含期望子字串之一
- **THEN** 該題記為 retrieval hit

#### Scenario: kb 題未命中

- **WHEN** 某題 `route` 為 `kb` 且有期望子字串，但檢索結果没有任何 url／source 命中
- **THEN** 該題記為 retrieval miss，並出現在失敗列表中

### Requirement: Cohere 有／無對照

評測腳本 SHALL 支援 `--compare-rerank`：對同一批 wide-retrieve 結果分別以向量 top-n 與 Cohere top-n 計分，並輸出兩邊 hit_rate 與差異摘要。

#### Scenario: 對照輸出

- **WHEN** 使用者執行 `--compare-rerank`
- **THEN** 標準輸出包含 vector 與 cohere 兩邊的 hit_rate，以及 hit_rate_delta

### Requirement: 可選答案層檢查

當啟用答案評測時，腳本 SHALL 呼叫既有 RAG 問答服務產生回答。對 `must_not_answer=true` 或 `route=refuse` 的題目，若回答符合專案既有「無資料／無法回答」類訊息，SHALL 記為 refuse_ok；對 `route=kb` 題目，SHALL 檢查回答附帶來源是否命中期望子字串（source_hit）。

#### Scenario: 拒答題不胡謅

- **WHEN** 題目標記應拒答或無資料，且答案評測已啟用
- **THEN** 腳本檢查回答是否為無資料／無法回答類訊息，並將結果寫入報告

### Requirement: 報告輸出

評測腳本 SHALL 在標準輸出印出摘要（題數、hit 比例、失敗 id），並支援將每題明細寫入 JSON 報告檔。

#### Scenario: 產出報告檔

- **WHEN** 使用者指定報告輸出路徑並完成評測
- **THEN** 該路徑存在 JSON 檔，內含每題的 query、hit 與否與檢索到的 url 列表

### Requirement: 判定正確率與誤配率

評測腳本 SHALL 對標有 `expected_verdict` 的題目輸出兩項指標：

- **判定正確率**：回傳判定與期望判定相同的比例。
- **誤配率**：期望為「證據不足」但系統回傳了其他判定的比例。誤配是查核功能唯一的嚴重失效模式（把某則主張的判定貼到另一則主張上），因此 SHALL 單獨計分，SHALL NOT 併入判定正確率。

腳本 SHALL 一併輸出判定錯誤與誤配的題目 id，供人工覆核。

#### Scenario: 判定正確

- **WHEN** 某題 `expected_verdict` 為「錯誤」，系統回傳判定亦為「錯誤」
- **THEN** 該題記為判定正確

#### Scenario: 誤配

- **WHEN** 某題 `expected_verdict` 為「證據不足」，但系統回傳「錯誤」
- **THEN** 該題記為誤配，且出現在誤配題目列表中

#### Scenario: 未標註期望判定的題目不受影響

- **WHEN** 某題沒有 `expected_verdict`
- **THEN** 該題不參與判定相關計分，既有的 hit_rate／MRR／nDCG 計分不變

