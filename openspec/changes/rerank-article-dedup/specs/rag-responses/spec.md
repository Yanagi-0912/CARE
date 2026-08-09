## ADDED Requirements

### Requirement: 精排後之文章層級去重

精排（reranker）SHALL 對 wide retrieve 取回的完整候選集排序後，系統 SHALL 在截取進生成 prompt 的最終筆數之前，依文章身分（`RagAnswerService._source_key`：有 `url` 用 `url`，無 `url` 用 `source_name`＋`original_title`）做去重，使同一篇文章最多保留 `RAG_RERANK_MAX_CHUNKS_PER_ARTICLE` 個 chunk（預設 `2`）。去重 SHALL 保持精排排序的相對順序，SHALL NOT 重新排序候選。

呼叫精排 API 時，系統 SHALL 要求回傳完整候選集的排序結果（`top_n` 等於候選集筆數），而非只取用最終要放入 prompt 的筆數，使去重能看到完整排序、判斷是否有其他文章的候選因同文章擠壓而被排除。

#### Scenario: 單一文章佔滿多個席位時釋出名額給其他文章

- **WHEN** 精排完整排序中，前段名次被同一篇文章的 3 個以上 chunk 佔據
- **THEN** 最終進 prompt 的候選中，該篇文章最多保留 `RAG_RERANK_MAX_CHUNKS_PER_ARTICLE` 個 chunk，名額由排序在後、屬於其他文章的候選遞補

#### Scenario: 去重不改變候選間的相對順序

- **WHEN** 去重前的完整排序為 A、B、C（依相關性由高到低，A、B 同屬一篇文章）
- **THEN** 去重後保留的候選之間，相對順序與去重前一致（不因去重而重新排序）
