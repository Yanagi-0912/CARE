## MODIFIED Requirements

### Requirement: Golden set 資料格式

系統 SHALL 提供可版本控管的 RAG 評測題庫（JSONL）。每一行 SHALL 為一題，且至少包含 `id`、`query`、`route`（`kb`｜`refuse`｜`web`）。針對 `route=kb` 的題目，題庫 SHOULD 提供 `expected_url_substrings`、`expected_source_substrings` 與／或 `expected_content_substrings`（非空字串陣列），供檢索命中判定使用；當知識庫文件缺少 `url` 時，SHALL 允許以 source／content 子字串計分。

題庫 SHALL 允許可選欄位 `expected_verdict`，其值為 `錯誤`｜`部分錯誤`｜`正確`｜`事實釐清`｜`證據不足` 之一，用於評測查核判定。標了 `expected_verdict` 為「證據不足」的題目 SHALL 表示「該主張不應命中任何已查核報告」，用以量測誤配。

#### Scenario: 合法題目列

- **WHEN** 評測腳本讀取題庫檔
- **THEN** 可解析每一行 JSON，且缺少必填欄位的列會被回報為錯誤而非靜默略過

#### Scenario: 標註期望判定

- **WHEN** 某題標有 `expected_verdict`
- **THEN** 該題同時參與判定正確率計分，且其值限於五種合法判定

## ADDED Requirements

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
