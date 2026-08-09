## ADDED Requirements

### Requirement: Golden set 資料格式

系統 SHALL 提供可版本控管的 RAG 評測題庫（JSONL）。每一行 SHALL 為一題，且至少包含 `id`、`query`、`route`（`kb`｜`refuse`｜`web`）。針對 `route=kb` 的題目，題庫 SHOULD 提供 `expected_url_substrings`、`expected_source_substrings` 與／或 `expected_content_substrings`（非空字串陣列），供檢索命中判定使用；當知識庫文件缺少 `url` 時，SHALL 允許以 source／content 子字串計分。

#### Scenario: 合法題目列

- **WHEN** 評測腳本讀取題庫檔
- **THEN** 可解析每一行 JSON，且缺少必填欄位的列會被回報為錯誤而非靜默略過

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
