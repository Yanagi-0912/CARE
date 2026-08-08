# RAG 檢索品質改善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 RAG 的檢索品質可被精確量測（nDCG@5 / MRR / citation coverage），修好生成端的假引用，並取得兩項不需重建知識庫的檢索增益。

**Architecture:** 分兩個 openspec change。`rag-eval-metrics` 先建立可信的量測（含把 `original_title` 從 Mongo 投影出來，這是後續三項改動的共同前提），再修生成端引用；`rag-retrieval-tuning` 移除與架構意圖相反的 `min_score` 門檻，並把標題補進 reranker 的輸入，使 reranker 讀到與 embedding 一致的文本。所有改動都在 CARE repo 內，不觸碰上游 ETL、不重跑 embedding。

**Tech Stack:** Python 3.12、FastAPI、LangChain Core（`Document`）、Motor（MongoDB Atlas）、Cohere Rerank v2 HTTP API、pytest / pytest-asyncio

## Global Constraints

- **禁止 monkey patch**：測試一律以依賴注入傳入 mock，不得使用 `unittest.mock.patch` 修改全域或別處導入的實例（來源：`openspec/config.yaml`）
- 單元測試置於 `tests/unit/services/rag/`，對應既有檔案命名
- LINE 回覆一律純文字，**不得輸出任何 Markdown**，包含 `**粗體**`、`# 標題`、`[文字](網址)`（來源：`openspec/specs/line-reply-rules`）
- 參考來源最多 **3** 筆（`CITE_TOP_K = 3`）
- 面向使用者文件與 commit 描述使用繁體中文
- Definition of Done：`./init.sh` 全綠
- 不重跑 embedding、不重建知識庫、不改上游 `Capoo0618/CARE-data`
- 每個 change 合併後執行 `openspec archive <change>`

## 執行順序

```
Task 1  openspec: rag-eval-metrics 骨架
Task 2  original_title 投影 + eval title 標籤        (B1)
Task 3  nDCG@5 / MRR                                 (B2)
Task 4  context 格式與 prompt                        (B3-1)
Task 5  _append_sources 重寫                          (B3-2)
Task 6  citation coverage                            (B4)
Task 7  openspec: rag-retrieval-tuning 骨架
Task 8  移除 min_score 門檻                           (C1)
Task 9  rerank 輸入補回標題                           (C2)   ◄ 預期最大增益
Task 10 清除導覽列噪音腳本                            (C3)
Task 11 Atlas Search index 範本修正                   (C4)
Task 12 CARE-data 問題報告                            (交付 A)
```

Task 2 完成後、Task 8 與 Task 9 各自完成後，都要跑一次 eval 記錄數字（見各 Task 的驗證步驟）。

---

### Task 1: openspec change `rag-eval-metrics` 骨架

**Files:**
- Create: `openspec/changes/rag-eval-metrics/.openspec.yaml`
- Create: `openspec/changes/rag-eval-metrics/proposal.md`
- Create: `openspec/changes/rag-eval-metrics/design.md`
- Create: `openspec/changes/rag-eval-metrics/tasks.md`

**Interfaces:**
- Consumes: 無
- Produces: 供 Task 2–6 勾選的 `tasks.md` 清單

- [ ] **Step 1: 建立 `.openspec.yaml`**

```yaml
schema: spec-driven
created: 2026-08-08
```

- [ ] **Step 2: 撰寫 `proposal.md`**

必須包含以下四節（格式對齊 `openspec/changes/cohere-rag-rerank/proposal.md`）：

`## Why` — 說明現況：eval 只有 binary hit_rate，rerank 前後差異看不出來；
生成端 context 不含來源資訊、prompt 未強制標 `[n]`，來源是事後貼上，
造成答案內容與所附來源不對應。

`## What Changes` — 條列：
- `eval_scoring` 新增 `expected_title_substrings` 標籤（比對 `original_title`）
- 新增 `MRR`、`nDCG@5` 指標；**不做 recall@k**（缺 exhaustive relevance judgments，分母不存在）
- 兩個 retriever 的 `$project` 加入 `original_title`
- context 改為帶編號與出處標頭；prompt 要求逐句標 `[n]`
- 「參考資料來源」改為只列**實際被引用**者，依首次引用順序連續重編號
- 模型未輸出任何 `[n]` 時不附來源，記錄 `citation_missing` log
- 新增 citation coverage 指標
- **非 BREAKING**：對外 tool 介面不變

`## Capabilities` — `### Modified Capabilities` 下宣告：

> `rag-responses`：「檢索上下文與參考來源上限」由「SHALL 只列出最多 3 筆關聯度最高的網址」
> 改為「SHALL 只列出實際被引用的來源，最多 3 筆，依首次引用順序連續編號；
> 當來源缺少 `url` 時，SHALL 以 `來源名｜標題` 呈現，不得靜默丟棄」。

`## Impact` — 列出程式檔案（見 Task 2–6 的 Files 區塊）、無新 HTTP route、
測試路徑 `tests/unit/services/rag/`、無新增相依套件。

- [ ] **Step 3: 撰寫 `design.md`**

至少包含 `## Context`、`## Goals / Non-Goals`、`## Decisions` 三節。
`## Decisions` 需涵蓋：

- **D1 為何不做 recall@k**：golden set 每題只標一個正解來源，
  沒有「該題在語料庫中共有幾筆相關文件」的分母。硬算的分母是假的。
- **D2 nDCG 的 IDCG 基準**：以「取回清單自身的 relevance 重排後」計算 IDCG，
  而非以語料庫全體理想排序（同 D1，缺判準）。此為無完整判準時的標準做法，
  須在 `evals/rag/README.md` 註明口徑。
- **D3 未引用時不附來源**：忠於「只列實際被引用者」，且與既有
  `no-fabricated-rag-sources` change 的意圖一致。發生率由 citation coverage 量測。

- [ ] **Step 4: 撰寫 `tasks.md`**

以 `## 1. …` / `- [ ] 1.1 …` 格式，對應本計畫 Task 2–6 的步驟，
並依 `openspec/config.yaml` 的 `rules.tasks` 要求引用 `tests/` 下對應的 pytest 路徑。
最後一節須含 Definition of Done 項目（`./init.sh` 全綠）。

- [ ] **Step 5: 建立 spec delta `specs/rag-responses/spec.md`**

repo 中 19 個 change 有 18 個都有 `specs/<capability>/spec.md` delta ——
這是 `openspec archive` 併回 `openspec/specs/` 的機制，proposal 的散文不能取代它。
建立 `openspec/changes/rag-eval-metrics/specs/rag-responses/spec.md`：

```markdown
## MODIFIED Requirements

### Requirement: 檢索上下文與參考來源上限

RAG 檢索 SHALL 先取回最多 `RAG_RETRIEVE_CANDIDATES` 筆關聯文件作為候選（預設 40），經精排後 SHALL 將最多 `RAG_RERANK_TOP_N` 筆（預設 5）內容放入生成 prompt，且每筆 SHALL 帶有編號與出處標頭（來源名與標題）。回答最下方的「參考資料來源」SHALL 只列出**實際被引用**的來源，最多 3 筆，依首次引用順序連續重編號。當某筆來源缺少 `url` 時，系統 SHALL 以「來源名｜標題」呈現，不得因缺 url 而靜默丟棄。當模型未輸出任何引用編號時，系統 SHALL NOT 附上參考來源清單。

#### Scenario: 只列出實際被引用的來源

- **WHEN** 生成的答案引用了第 3 筆與第 1 筆內容
- **THEN** 參考來源只列這兩筆，依首次引用順序重編為 [1]、[2]，且答案內文中的編號一併改寫為對應的新編號

#### Scenario: 缺少 url 的來源仍顯示

- **WHEN** 被引用的文件有 `source_name` 與 `original_title` 但 `url` 為空
- **THEN** 該筆以「來源名｜標題」形式列於參考來源清單中

#### Scenario: 完全沒有引用時不附來源

- **WHEN** 生成的答案不含任何引用編號
- **THEN** 回覆不附「參考資料來源：」段落，並記錄 `citation_missing` log
```

- [ ] **Step 6: Commit**

```bash
git add openspec/changes/rag-eval-metrics
git commit -m "docs(openspec): 新增 rag-eval-metrics change 提案"
```

---

### Task 2: `original_title` 投影與 eval 標題標籤

**Files:**
- Modify: `app/services/rag/retriever.py`（兩處 `$project` 與 metadata 組裝）
- Modify: `app/services/rag/eval_scoring.py`
- Modify: `evals/rag/README.md`
- Test: `tests/unit/services/rag/test_retriever.py`
- Test: `tests/unit/services/rag/test_eval_scoring.py`（若不存在則建立）

**Interfaces:**
- Consumes: 無
- Produces:
  - `Document.metadata["original_title"]: str | None` — Task 4、5、9 依賴
  - `titles_from_docs(docs: list[Document]) -> list[str]`
  - `EvalCase.expected_title_substrings: list[str]`
  - `CaseResult.retrieved_titles: list[str]`
  - `is_doc_retrieval_hit(docs, *, expected_url_substrings, expected_source_substrings, expected_content_substrings=None, expected_title_substrings=None) -> bool`

- [ ] **Step 1: 寫失敗測試 — retriever 投影 `original_title`**

加到 `tests/unit/services/rag/test_retriever.py`。**沿用該檔既有的
`_make_retriever()` + `MagicMock` 假件寫法**（全檔 18 個測試都是這個風格；
依 DI 規則不得 monkey patch，但也不要手刻新的假件類別而讓同一檔案出現兩種風格）。
注意 `_make_retriever` 的 `text_field` 預設是 `"chunk_text"`：

```python
@pytest.mark.asyncio
async def test_retriever_projects_and_exposes_original_title():
    retriever, emb = _make_retriever(vector_dim=2)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": "abc",
                "chunk_text": "幽門螺旋桿菌與胃癌風險",
                "source_name": "食藥署闢謠專區",
                "url": None,
                "original_title": "捍「胃」健康 過年聚餐用公筷",
                "score": 0.8,
            }
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("幽門螺旋桿菌")

    pipeline = fake_collection.aggregate.call_args.args[0]
    project_stage = next(s for s in pipeline if "$project" in s)
    assert project_stage["$project"]["original_title"] == 1
    assert docs[0].metadata["original_title"] == "捍「胃」健康 過年聚餐用公筷"
```

同樣以 `MongoAtlasTextRetriever` 寫一個對應的測試，確認 text retriever 的
`$project` 與 metadata 也帶出 `original_title`。

- [ ] **Step 2: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_retriever.py::test_vector_retriever_projects_and_exposes_original_title -v`
Expected: FAIL — `KeyError: 'original_title'`（`$project` 尚未包含該欄位）

- [ ] **Step 3: 修改 `MongoAtlasVectorRetriever.ainvoke`**

`app/services/rag/retriever.py` 的 `$project` 階段加入 `original_title`：

```python
            {
                "$project": {
                    self.text_field: 1,
                    "_id": 1,
                    "source_name": 1,
                    "url": 1,
                    "original_title": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
```

metadata 組裝加入該欄位：

```python
            documents.append(
                Document(
                    page_content=text,
                    metadata={
                        "id": str(doc.get("_id")),
                        "score": score,
                        "source_name": doc.get("source_name"),
                        "url": doc.get("url"),
                        "original_title": doc.get("original_title"),
                    },
                )
            )
```

- [ ] **Step 4: 對 `MongoAtlasTextRetriever` 做相同修改**

`$project` 加入 `"original_title": 1`，metadata 加入
`"original_title": doc.get("original_title"),`。

- [ ] **Step 5: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_retriever.py -v`
Expected: PASS（含既有測試）

- [ ] **Step 6: 寫失敗測試 — eval 標題標籤**

建立或加到 `tests/unit/services/rag/test_eval_scoring.py`：

```python
from langchain_core.documents import Document

from app.services.rag.eval_scoring import (
    EvalCase,
    is_doc_retrieval_hit,
    score_case_retrieval,
    titles_from_docs,
)


def _doc(*, title=None, url=None, source=None, content="內容"):
    return Document(
        page_content=content,
        metadata={"original_title": title, "url": url, "source_name": source},
    )


def test_titles_from_docs_skips_blank():
    docs = [_doc(title="捍「胃」健康"), _doc(title=None), _doc(title="  ")]
    assert titles_from_docs(docs) == ["捍「胃」健康"]


def test_is_doc_retrieval_hit_matches_title_when_url_missing():
    docs = [_doc(title="捍「胃」健康 過年聚餐用公筷", url=None)]
    assert is_doc_retrieval_hit(
        docs,
        expected_url_substrings=["pid=19853"],
        expected_source_substrings=[],
        expected_content_substrings=[],
        expected_title_substrings=["捍「胃」健康"],
    )


def test_score_case_retrieval_scores_title_only_case():
    case = EvalCase(
        id="kb-900",
        query="幽門螺旋桿菌檢測",
        route="kb",
        expected_title_substrings=["捍「胃」健康"],
    )
    result = score_case_retrieval(case, [_doc(title="捍「胃」健康 過年聚餐用公筷")])
    assert result.skipped is False
    assert result.retrieval_hit is True
    assert result.retrieved_titles == ["捍「胃」健康 過年聚餐用公筷"]
```

- [ ] **Step 7: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_eval_scoring.py -v`
Expected: FAIL — `ImportError: cannot import name 'titles_from_docs'`

- [ ] **Step 8: 在 `eval_scoring.py` 實作**

`EvalCase` 加欄位（放在 `expected_content_substrings` 之後）：

```python
    expected_title_substrings: list[str] = field(default_factory=list)
```

`has_retrieval_expectations` 納入新欄位：

```python
    @property
    def has_retrieval_expectations(self) -> bool:
        return bool(
            self.expected_url_substrings
            or self.expected_source_substrings
            or self.expected_content_substrings
            or self.expected_title_substrings
        )
```

`CaseResult` 加欄位（放在 `retrieved_sources` 之後）：

```python
    retrieved_titles: list[str] = field(default_factory=list)
```

新增取值函式（放在 `source_names_from_docs` 之後）：

```python
def titles_from_docs(docs: list[Document]) -> list[str]:
    titles: list[str] = []
    for doc in docs:
        title = str(doc.metadata.get("original_title") or "").strip()
        if title:
            titles.append(title)
    return titles
```

`is_doc_retrieval_hit` 加參數與判定：

```python
def is_doc_retrieval_hit(
    docs: list[Document],
    *,
    expected_url_substrings: list[str],
    expected_source_substrings: list[str],
    expected_content_substrings: list[str] | None = None,
    expected_title_substrings: list[str] | None = None,
) -> bool:
    if is_substring_hit(urls_from_docs(docs), expected_url_substrings):
        return True
    if is_substring_hit(source_names_from_docs(docs), expected_source_substrings):
        return True
    if is_substring_hit(titles_from_docs(docs), expected_title_substrings or []):
        return True
    return is_substring_hit(
        contents_from_docs(docs), expected_content_substrings or []
    )
```

`score_case_retrieval` 傳入新標籤並回填 `retrieved_titles`：

```python
def score_case_retrieval(case: EvalCase, docs: list[Document]) -> CaseResult:
    urls = urls_from_docs(docs)
    sources = source_names_from_docs(docs)
    titles = titles_from_docs(docs)
    if case.route != "kb" or not case.has_retrieval_expectations:
        return CaseResult(
            id=case.id,
            query=case.query,
            route=case.route,
            skipped=True,
            retrieval_hit=None,
            retrieved_urls=urls,
            retrieved_sources=sources,
            retrieved_titles=titles,
        )
    return CaseResult(
        id=case.id,
        query=case.query,
        route=case.route,
        skipped=False,
        retrieval_hit=is_doc_retrieval_hit(
            docs,
            expected_url_substrings=case.expected_url_substrings,
            expected_source_substrings=case.expected_source_substrings,
            expected_content_substrings=case.expected_content_substrings,
            expected_title_substrings=case.expected_title_substrings,
        ),
        retrieved_urls=urls,
        retrieved_sources=sources,
        retrieved_titles=titles,
    )
```

- [ ] **Step 9: 重構 `load_golden_jsonl` 的重複解析並支援新欄位**

現行有四段幾乎相同的 list 欄位解析。抽成模組層級私有函式（放在 `load_golden_jsonl` 之前）：

```python
def _string_list_field(
    data: dict[str, Any], key: str, *, line_no: int, case_id: str
) -> list[str]:
    value = data.get(key) or []
    if not isinstance(value, list):
        raise ValueError(f"line {line_no} (id={case_id}): {key} must be a list")
    return [str(x).strip() for x in value if str(x).strip()]
```

將 `load_golden_jsonl` 中 `expected` / `expected_src` / `expected_content`
三段解析（原第 185–211 行）整段換成：

```python
            expected_clean = _string_list_field(
                data, "expected_url_substrings", line_no=line_no, case_id=case_id
            )
            expected_src_clean = _string_list_field(
                data, "expected_source_substrings", line_no=line_no, case_id=case_id
            )
            expected_content_clean = _string_list_field(
                data, "expected_content_substrings", line_no=line_no, case_id=case_id
            )
            expected_title_clean = _string_list_field(
                data, "expected_title_substrings", line_no=line_no, case_id=case_id
            )
```

並在建構 `EvalCase` 時加上 `expected_title_substrings=expected_title_clean,`。

- [ ] **Step 10: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_eval_scoring.py tests/unit/services/rag/test_retriever.py -v`
Expected: PASS

- [ ] **Step 11: 更新 `evals/rag/README.md` 題庫格式表**

在題庫格式表格中，於 `expected_content_substrings` 之後插入一列：

```markdown
| `expected_title_substrings` | 建議 | 期望 `original_title` 片段。**最穩定的標籤** —— 不隨切片方式改變；`expected_content_substrings` 會在上游改切法時整批失效 |
```

- [ ] **Step 12: 跑一次 eval 記錄 baseline**

Run: `.venv/bin/python scripts/rag_eval.py --rank-mode cohere --top-n 5 --out /tmp/rag-baseline.json`
把 `hit_rate` 記到 `openspec/changes/rag-eval-metrics/design.md` 的 `## Context`，
標明「口徑：加入 title 標籤前」。此數字取代 README 中的 0.29 / 0.44 作為後續比較基準。

- [ ] **Step 13: Commit**

```bash
git add app/services/rag/retriever.py app/services/rag/eval_scoring.py \
        evals/rag/README.md tests/unit/services/rag/test_retriever.py \
        tests/unit/services/rag/test_eval_scoring.py
git commit -m "feat(rag): 投影 original_title 並支援 title 標籤計分"
```

---

### Task 3: nDCG@5 與 MRR

**Files:**
- Modify: `app/services/rag/eval_scoring.py`
- Modify: `scripts/rag_eval.py`
- Test: `tests/unit/services/rag/test_eval_scoring.py`

**Interfaces:**
- Consumes: Task 2 的 `is_doc_retrieval_hit`、`EvalCase.expected_title_substrings`
- Produces:
  - `doc_relevances(case: EvalCase, docs: list[Document]) -> list[int]`
  - `mrr(relevances: list[int]) -> float`
  - `ndcg_at_k(relevances: list[int], k: int) -> float`
  - `CaseResult.mrr: Optional[float]`、`CaseResult.ndcg_at_5: Optional[float]`
  - `EvalSummary.mean_mrr: Optional[float]`、`EvalSummary.mean_ndcg_at_5: Optional[float]`

- [ ] **Step 1: 寫失敗測試**

加到 `tests/unit/services/rag/test_eval_scoring.py`：

```python
import math

from app.services.rag.eval_scoring import doc_relevances, mrr, ndcg_at_k


def test_mrr_uses_first_relevant_rank():
    assert mrr([1, 0, 0]) == 1.0
    assert mrr([0, 1, 0]) == 0.5
    assert mrr([0, 0, 1]) == pytest.approx(1 / 3)
    assert mrr([0, 0, 0]) == 0.0


def test_ndcg_at_k_rewards_earlier_position():
    assert ndcg_at_k([1, 0, 0, 0, 0], 5) == 1.0
    later = ndcg_at_k([0, 0, 1, 0, 0], 5)
    assert later == pytest.approx(1 / math.log2(4))
    assert later < 1.0


def test_ndcg_at_k_returns_zero_when_no_relevant():
    assert ndcg_at_k([0, 0, 0], 5) == 0.0


def test_ndcg_at_k_ignores_docs_beyond_k():
    assert ndcg_at_k([0, 0, 0, 0, 0, 1], 5) == 0.0


def test_doc_relevances_marks_each_doc_independently():
    case = EvalCase(
        id="kb-901",
        query="q",
        route="kb",
        expected_title_substrings=["捍「胃」健康"],
    )
    docs = [_doc(title="無關文章"), _doc(title="捍「胃」健康 過年聚餐用公筷")]
    assert doc_relevances(case, docs) == [0, 1]
```

檔頭需補 `import pytest`（若尚未有）。

- [ ] **Step 2: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_eval_scoring.py -v -k "mrr or ndcg or relevances"`
Expected: FAIL — `ImportError: cannot import name 'doc_relevances'`

- [ ] **Step 3: 實作三個純函式**

在 `eval_scoring.py` 檔頭加 `import math`，並在 `is_doc_retrieval_hit` 之後新增：

```python
def doc_relevances(case: EvalCase, docs: list[Document]) -> list[int]:
    """逐篇判定二元 relevance（1 = 命中任一期望 substring）。

    以單篇為單位重用 `is_doc_retrieval_hit`，因此 url／title／source／content
    的判準與 hit_rate 完全一致，指標之間不會互相矛盾。
    """
    return [
        1
        if is_doc_retrieval_hit(
            [doc],
            expected_url_substrings=case.expected_url_substrings,
            expected_source_substrings=case.expected_source_substrings,
            expected_content_substrings=case.expected_content_substrings,
            expected_title_substrings=case.expected_title_substrings,
        )
        else 0
        for doc in docs
    ]


def mrr(relevances: list[int]) -> float:
    """第一筆命中的排名倒數；全無命中回傳 0.0。"""
    for rank, rel in enumerate(relevances, start=1):
        if rel:
            return 1.0 / rank
    return 0.0


def _dcg(gains: list[int]) -> float:
    return sum(g / math.log2(i + 1) for i, g in enumerate(gains, start=1) if g)


def ndcg_at_k(relevances: list[int], k: int) -> float:
    """二元 gain 的 nDCG@k。

    IDCG 以「取回清單自身的 relevance 重排後」計算，而非語料庫全體的理想排序 ——
    golden set 沒有窮盡的相關性判準，無法得知語料庫中共有幾篇相關文件。
    此為缺完整判準時的標準做法，口徑已記於 evals/rag/README.md。
    """
    if k <= 0:
        return 0.0
    gains = relevances[:k]
    ideal = sorted(relevances, reverse=True)[:k]
    idcg = _dcg(ideal)
    if not idcg:
        return 0.0
    return _dcg(gains) / idcg
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_eval_scoring.py -v`
Expected: PASS

- [ ] **Step 5: 把指標接進 `CaseResult` 與 `score_case_retrieval`**

`CaseResult` 加欄位（放在 `rank_mode` 之前）：

```python
    mrr: Optional[float] = None
    ndcg_at_5: Optional[float] = None
```

`score_case_retrieval` 計分分支中，計算並帶入（skip 分支維持 `None`）：

```python
    relevances = doc_relevances(case, docs)
    return CaseResult(
        ...
        mrr=mrr(relevances),
        ndcg_at_5=ndcg_at_k(relevances, 5),
    )
```

- [ ] **Step 6: 把指標接進 `EvalSummary`**

`EvalSummary` 加欄位（放在 `hit_rate` 之後）：

```python
    mean_mrr: Optional[float]
    mean_ndcg_at_5: Optional[float]
```

`summarize_results` 計算平均（只對有計分的題目平均）：

```python
def summarize_results(results: list[CaseResult]) -> EvalSummary:
    scored = [r for r in results if not r.skipped and r.error is None]
    hits = sum(1 for r in scored if r.retrieval_hit is True)
    scored_n = len(scored)
    hit_rate = (hits / scored_n) if scored_n else None
    mrr_values = [r.mrr for r in scored if r.mrr is not None]
    ndcg_values = [r.ndcg_at_5 for r in scored if r.ndcg_at_5 is not None]
    return EvalSummary(
        total_cases=len(results),
        scored_cases=scored_n,
        hits=hits,
        hit_rate=hit_rate,
        mean_mrr=(sum(mrr_values) / len(mrr_values)) if mrr_values else None,
        mean_ndcg_at_5=(
            (sum(ndcg_values) / len(ndcg_values)) if ndcg_values else None
        ),
        miss_ids=[r.id for r in scored if r.retrieval_hit is False],
        skipped_ids=[r.id for r in results if r.skipped],
        error_ids=[r.id for r in results if r.error],
    )
```

- [ ] **Step 7: 寫 summary 測試**

```python
from app.services.rag.eval_scoring import CaseResult, summarize_results


def test_summarize_results_averages_only_scored_cases():
    results = [
        CaseResult(
            id="a", query="q", route="kb", skipped=False, retrieval_hit=True,
            retrieved_urls=[], mrr=1.0, ndcg_at_5=1.0,
        ),
        CaseResult(
            id="b", query="q", route="kb", skipped=False, retrieval_hit=False,
            retrieved_urls=[], mrr=0.0, ndcg_at_5=0.0,
        ),
        CaseResult(
            id="c", query="q", route="web", skipped=True, retrieval_hit=None,
            retrieved_urls=[],
        ),
    ]
    summary = summarize_results(results)
    assert summary.scored_cases == 2
    assert summary.mean_mrr == 0.5
    assert summary.mean_ndcg_at_5 == 0.5
    assert summary.skipped_ids == ["c"]
```

- [ ] **Step 8: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_eval_scoring.py -v`
Expected: PASS

- [ ] **Step 9: 讓 `scripts/rag_eval.py` 印出新指標**

`_print_summary`（約第 206 行）中，緊接在 `print(f"hit_rate: ...")` 之後插入：

```python
    def _fmt(value: Optional[float]) -> str:
        return f"{value:.3f}" if value is not None else "n/a"

    print(f"mean_mrr: {_fmt(summary.mean_mrr)}")
    print(f"mean_ndcg@5: {_fmt(summary.mean_ndcg_at_5)}")
```

`--compare-rerank` 的 delta 區塊（約第 387 行）中，
於 `hit_rate_delta` 之後補上 nDCG 的 delta：

```python
        if v_sum.mean_ndcg_at_5 is not None and c_sum.mean_ndcg_at_5 is not None:
            print(
                "ndcg@5_delta: "
                f"{c_sum.mean_ndcg_at_5 - v_sum.mean_ndcg_at_5:+.3f}"
            )
```

JSON 報告不需額外改動 —— `EvalSummary.to_dict()` 是 `asdict()`，新欄位自動包含。

- [ ] **Step 10: 執行一次實跑確認**

Run: `.venv/bin/python scripts/rag_eval.py --rank-mode cohere --top-n 5 --out /tmp/rag-b2.json`
Expected: 輸出含 `mean_mrr` 與 `mean_ndcg_at_5`，且 `hit_rate` 與 Task 2 Step 12 相同

- [ ] **Step 11: 在 `evals/rag/README.md` 補「怎麼讀結果」**

新增三行說明：`mean_mrr`、`mean_ndcg_at_5` 的意義，以及 IDCG 口徑
（「以取回清單自身重排為理想序，非語料庫全體」）與「不提供 recall@k」的理由。

- [ ] **Step 12: Commit**

```bash
git add app/services/rag/eval_scoring.py scripts/rag_eval.py \
        evals/rag/README.md tests/unit/services/rag/test_eval_scoring.py
git commit -m "feat(rag-eval): 新增 MRR 與 nDCG@5 指標"
```

---

### Task 4: context 格式與 prompt 引用要求

**Files:**
- Modify: `app/services/rag/answer_service.py`（`_generate_answer`）
- Modify: `app/services/rag/answer_prompts.py`（`build_rag_prompt`）
- Test: `tests/unit/services/rag/test_answer_service.py`
- Test: `tests/unit/services/rag/test_answer_prompts.py`

**Interfaces:**
- Consumes: Task 2 的 `Document.metadata["original_title"]`
- Produces: `RagAnswerService._build_context(docs: list[Document]) -> str`（static method）

- [ ] **Step 1: 寫失敗測試**

加到 `tests/unit/services/rag/test_answer_service.py`：

```python
from langchain_core.documents import Document

from app.services.rag.answer_service import RagAnswerService


def test_build_context_includes_numbered_source_and_title_header():
    docs = [
        Document(
            page_content="幽門螺旋桿菌與胃癌風險有關。",
            metadata={
                "source_name": "食藥署闢謠專區",
                "original_title": "捍「胃」健康 過年聚餐用公筷",
                "url": None,
            },
        ),
        Document(
            page_content="定期篩檢可降低大腸癌風險。",
            metadata={"source_name": "衛福部闢謠網站", "original_title": None},
        ),
    ]

    context = RagAnswerService._build_context(docs)

    assert "[1] 來源：食藥署闢謠專區｜標題：捍「胃」健康 過年聚餐用公筷" in context
    assert "幽門螺旋桿菌與胃癌風險有關。" in context
    # 缺 title 時只留來源，不留空欄位
    assert "[2] 來源：衛福部闢謠網站" in context
    assert "標題：None" not in context
    # url 不得進 context（避免模型改寫或杜撰網址）
    assert "http" not in context
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_answer_service.py::test_build_context_includes_numbered_source_and_title_header -v`
Expected: FAIL — `AttributeError: type object 'RagAnswerService' has no attribute '_build_context'`

- [ ] **Step 3: 實作 `_build_context` 並改用它**

在 `RagAnswerService` 中新增（放在 `_generate_answer` 之前）：

```python
    @staticmethod
    def _build_context(docs: list[Document]) -> str:
        """組出帶編號與出處標頭的 context。

        標頭只放 source_name 與 original_title，**不放 url** —— url 進 context
        會佔 token，且模型可能改寫或杜撰網址。url 由 `_append_sources`
        依編號對應回填。
        """
        blocks: list[str] = []
        for idx, doc in enumerate(docs, start=1):
            parts: list[str] = []
            source = str(doc.metadata.get("source_name") or "").strip()
            title = str(doc.metadata.get("original_title") or "").strip()
            if source:
                parts.append(f"來源：{source}")
            if title:
                parts.append(f"標題：{title}")
            header = f"[{idx}]" + (f" {'｜'.join(parts)}" if parts else "")
            blocks.append(f"{header}\n{doc.page_content}")
        return "\n\n".join(blocks)
```

`_generate_answer` 第一行改為：

```python
        context = self._build_context(docs)
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_answer_service.py -v`
Expected: PASS

- [ ] **Step 5: 寫 prompt 測試**

加到 `tests/unit/services/rag/test_answer_prompts.py`：

```python
from app.services.rag.answer_prompts import build_rag_prompt


def test_rag_prompt_requires_citation_markers():
    template = build_rag_prompt("zh-TW")
    text = template.format_messages(question="q", context="c")[0].content
    assert "每一項資訊都必須標上來源編號" in text
    assert "沒有任何一段內容支持的敘述，不要寫入回答" in text
```

- [ ] **Step 6: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_answer_prompts.py::test_rag_prompt_requires_citation_markers -v`
Expected: FAIL — assertion error（字串不存在）

- [ ] **Step 7: 修改 `build_rag_prompt` 的規則 1**

把原本的規則 1 換成兩條更明確的要求（其餘規則不動）：

```python
                "1. 每一項資訊都必須標上來源編號，格式為半形中括號加數字，"
                "例如：『...這是常見的症狀 [1]。』"
                "編號必須對應下方「RAG 內容」中每段開頭的編號；"
                "同一句引用多個來源時寫成 [1][2]。\n"
                "2. 沒有任何一段內容支持的敘述，不要寫入回答。\n"
```

其後原規則 2、3、4 順延為 3、4、5，編號需一併更新。

- [ ] **Step 8: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_answer_prompts.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add app/services/rag/answer_service.py app/services/rag/answer_prompts.py \
        tests/unit/services/rag/test_answer_service.py \
        tests/unit/services/rag/test_answer_prompts.py
git commit -m "feat(rag): context 帶編號出處標頭並要求逐項標註引用"
```

---

### Task 5: `_append_sources` 只列實際引用並重編號

**Files:**
- Modify: `app/services/rag/answer_service.py`
- Test: `tests/unit/services/rag/test_answer_service.py`

**Interfaces:**
- Consumes: Task 4 的 context 編號（模型輸出的 `[n]` 對應 `docs[n-1]`）
- Produces:
  - `cited_indices(answer_text: str) -> list[int]`（模組層級函式）
  - `RagAnswerService._append_sources(answer_text: str, docs: list[Document]) -> str`（行為變更）

- [ ] **Step 1: 寫失敗測試**

```python
from app.services.rag.answer_service import RagAnswerService, cited_indices


def _doc(source=None, url=None, title=None, content="內容"):
    return Document(
        page_content=content,
        metadata={"source_name": source, "url": url, "original_title": title},
    )


def test_cited_indices_returns_first_appearance_order_without_duplicates():
    assert cited_indices("甲 [3]，乙 [1]，丙 [3]。") == [3, 1]
    assert cited_indices("沒有引用") == []


def test_append_sources_lists_only_cited_and_renumbers():
    docs = [
        _doc(source="A", url="https://a.example/1"),
        _doc(source="B", url="https://b.example/2"),
        _doc(source="C", url="https://c.example/3"),
    ]
    out = RagAnswerService._append_sources("甲 [3]。乙 [1]。", docs)

    # [3] 首次出現 → 重編為 [1]；[1] → [2]
    assert "甲 [1]。乙 [2]。" in out
    assert "[1] C：https://c.example/3" in out
    assert "[2] A：https://a.example/1" in out
    assert "b.example" not in out  # 未被引用者不列出


def test_append_sources_uses_title_when_url_missing():
    docs = [_doc(source="食藥署闢謠專區", url=None, title="捍「胃」健康")]
    out = RagAnswerService._append_sources("內容 [1]。", docs)
    assert "[1] 食藥署闢謠專區｜捍「胃」健康" in out


def test_append_sources_returns_text_unchanged_when_no_citation():
    docs = [_doc(source="A", url="https://a.example/1")]
    text = "完全沒有引用標記的答案。"
    assert RagAnswerService._append_sources(text, docs) == text


def test_append_sources_deduplicates_same_url_to_one_number():
    docs = [
        _doc(source="A", url="https://a.example/1"),
        _doc(source="A", url="https://a.example/1"),
    ]
    out = RagAnswerService._append_sources("甲 [1]。乙 [2]。", docs)
    assert "甲 [1]。乙 [1]。" in out
    assert out.count("https://a.example/1") == 1


def test_append_sources_caps_at_three_and_drops_overflow_markers():
    docs = [_doc(source=f"S{i}", url=f"https://e.example/{i}") for i in range(1, 6)]
    out = RagAnswerService._append_sources("a[1]b[2]c[3]d[4]", docs)
    assert "[4]" not in out.split("參考")[0]  # 超出上限的標記被移除
    assert out.count("https://e.example/") == 3
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_answer_service.py -v -k "cited or append_sources"`
Expected: FAIL — `ImportError: cannot import name 'cited_indices'`

- [ ] **Step 3: 實作 `cited_indices` 與來源輔助函式**

`answer_service.py` 檔頭加 `import re`，並在 `RagAnswerService` 類別之前新增：

```python
_CITATION_RE = re.compile(r"\[(\d+)\]")


def cited_indices(answer_text: str) -> list[int]:
    """回傳答案中出現過的引用編號，依首次出現順序、去重。"""
    seen: set[int] = set()
    order: list[int] = []
    for match in _CITATION_RE.finditer(answer_text or ""):
        idx = int(match.group(1))
        if idx not in seen:
            seen.add(idx)
            order.append(idx)
    return order
```

在 `RagAnswerService` 中新增兩個 static helper：

```python
    @staticmethod
    def _source_label(doc: Document) -> str | None:
        """來源顯示字串；無 url 時退回「來源名｜標題」，兩者皆無則回 None。"""
        source = str(doc.metadata.get("source_name") or "").strip()
        url = str(doc.metadata.get("url") or "").strip()
        title = str(doc.metadata.get("original_title") or "").strip()
        if url:
            return f"{source}：{url}" if source else url
        if title:
            return f"{source}｜{title}" if source else title
        return None

    @staticmethod
    def _source_key(doc: Document) -> str:
        """判定「同一個來源」的鍵；有 url 用 url，否則用來源名＋標題。"""
        url = str(doc.metadata.get("url") or "").strip()
        if url:
            return f"url:{url}"
        source = str(doc.metadata.get("source_name") or "").strip()
        title = str(doc.metadata.get("original_title") or "").strip()
        return f"meta:{source}|{title}"
```

- [ ] **Step 4: 重寫 `_append_sources`**

整段替換原本的 `_append_sources`：

```python
    @staticmethod
    def _append_sources(answer_text: str, docs: list[Document]) -> str:
        cited = cited_indices(answer_text)
        if not cited:
            logger.info("citation_missing docs=%d", len(docs))
            return answer_text

        key_to_new: dict[str, int] = {}
        renumber: dict[int, int] = {}
        source_lines: list[str] = []

        for old_idx in cited:
            if old_idx < 1 or old_idx > len(docs):
                continue
            doc = docs[old_idx - 1]
            label = RagAnswerService._source_label(doc)
            if label is None:
                continue
            key = RagAnswerService._source_key(doc)
            existing = key_to_new.get(key)
            if existing is not None:
                renumber[old_idx] = existing
                continue
            if len(source_lines) >= CITE_TOP_K:
                continue
            new_idx = len(source_lines) + 1
            key_to_new[key] = new_idx
            renumber[old_idx] = new_idx
            source_lines.append(f"[{new_idx}] {label}")

        if not source_lines:
            logger.info("citation_unresolved cited=%s docs=%d", cited, len(docs))
            return answer_text

        def _replace(match: re.Match[str]) -> str:
            mapped = renumber.get(int(match.group(1)))
            return f"[{mapped}]" if mapped is not None else ""

        body = _CITATION_RE.sub(_replace, answer_text)
        heading = t("agent.sources_heading")
        return f"{body}\n\n{heading}\n" + "\n".join(source_lines)
```

註記：對應不到來源的 `[n]`（超出 `CITE_TOP_K` 上限、索引越界、或該篇
url 與 title 皆缺）會被整個移除，而非留下指向不存在來源的編號。

- [ ] **Step 5: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_answer_service.py -v`
Expected: PASS

- [ ] **Step 6: 執行 RAG 相關全部測試，修正既有測試的假設**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/ -v`
Expected: 既有測試若假設「一定會附前 3 筆來源」會失敗；
將這類測試的輸入答案文字補上 `[1]` 等引用標記，使其符合新契約。
**不要**為了讓舊測試通過而改回舊行為。

- [ ] **Step 7: Commit**

```bash
git add app/services/rag/answer_service.py tests/unit/services/rag/test_answer_service.py
git commit -m "feat(rag): 來源清單只列實際引用並依首次引用順序重編號"
```

---

### Task 6: citation coverage 指標

**Files:**
- Modify: `app/services/rag/eval_scoring.py`
- Modify: `scripts/rag_eval.py`
- Modify: `evals/rag/README.md`
- Test: `tests/unit/services/rag/test_eval_scoring.py`

**Interfaces:**
- Consumes: Task 5 的 `cited_indices`
- Produces:
  - `answer_citation_count(answer_text: str) -> int`
  - `CaseResult.citation_count: Optional[int]`
  - `EvalSummary.citation_coverage: Optional[float]`

- [ ] **Step 1: 寫失敗測試**

```python
from app.services.rag.eval_scoring import answer_citation_count


def test_answer_citation_count_counts_distinct_markers():
    assert answer_citation_count("甲 [1]，乙 [2]，丙 [1]。") == 2
    assert answer_citation_count("沒有引用") == 0
    assert answer_citation_count("") == 0


def test_summarize_results_reports_citation_coverage():
    results = [
        CaseResult(
            id="a", query="q", route="kb", skipped=False, retrieval_hit=True,
            retrieved_urls=[], citation_count=2,
        ),
        CaseResult(
            id="b", query="q", route="kb", skipped=False, retrieval_hit=True,
            retrieved_urls=[], citation_count=0,
        ),
        CaseResult(
            id="c", query="q", route="kb", skipped=False, retrieval_hit=True,
            retrieved_urls=[], citation_count=None,  # 未跑答案層
        ),
    ]
    summary = summarize_results(results)
    # 只對「有跑答案層」的題目計算（c 不計入分母）
    assert summary.citation_coverage == 0.5
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_eval_scoring.py -v -k citation`
Expected: FAIL — `ImportError: cannot import name 'answer_citation_count'`

- [ ] **Step 3: 實作**

`eval_scoring.py` 新增（放在 `is_source_hit` 之後）：

```python
def answer_citation_count(answer_text: str) -> int:
    """答案中出現的相異引用編號數量。

    重用 answer_service.cited_indices，確保與線上組裝來源時的判準一致。
    """
    from app.services.rag.answer_service import cited_indices

    return len(cited_indices(answer_text or ""))
```

`CaseResult` 加欄位：

```python
    citation_count: Optional[int] = None
```

`EvalSummary` 加欄位（放在 `mean_ndcg_at_5` 之後）：

```python
    citation_coverage: Optional[float]
```

`summarize_results` 中加入計算：

```python
    cited = [r for r in scored if r.citation_count is not None]
    citation_coverage = (
        sum(1 for r in cited if r.citation_count > 0) / len(cited)
    ) if cited else None
```

並在建構 `EvalSummary` 時帶入 `citation_coverage=citation_coverage,`。

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_eval_scoring.py -v`
Expected: PASS

- [ ] **Step 5: 在 `scripts/rag_eval.py` 的 `--with-answer` 路徑填入 `citation_count`**

`_eval_one`（約第 198 行）中，在 `result.answer_preview = (answer or "")[:240]`
之後插入一行：

```python
    result.citation_count = answer_citation_count(answer)
```

並在檔頭既有的 `from app.services.rag.eval_scoring import (...)` 匯入區塊中
加入 `answer_citation_count,`。

`_print_summary` 的 `if with_answer:` 區塊末尾補上輸出：

```python
        cited_cases = [r for r in results if r.citation_count is not None]
        if cited_cases:
            ok = sum(1 for r in cited_cases if r.citation_count > 0)
            print(f"citation_coverage: {ok}/{len(cited_cases)}")
```

未使用 `--with-answer` 時 `citation_count` 維持 `None`，不計入分母。

- [ ] **Step 6: 實跑驗證**

Run: `.venv/bin/python scripts/rag_eval.py --with-answer --rank-mode cohere --top-n 5 --out /tmp/rag-b4.json`
Expected: 報告含 `citation_coverage`。此數字即「模型實際標註引用的比例」，
若明顯偏低，代表 Task 4 的 prompt 需再強化 —— 這正是本指標的用途。

- [ ] **Step 7: 更新 `evals/rag/README.md`**

在「怎麼讀結果」加入 `citation_coverage` 的定義（分母為有跑答案層的題目、
分子為答案中至少有一個有效 `[n]` 的題目），並註明未附來源時
`_append_sources` 會記錄 `citation_missing` log。

- [ ] **Step 8: Commit**

```bash
git add app/services/rag/eval_scoring.py scripts/rag_eval.py \
        evals/rag/README.md tests/unit/services/rag/test_eval_scoring.py
git commit -m "feat(rag-eval): 新增 citation coverage 指標"
```

- [ ] **Step 9: 跑完整測試並收尾 change**

Run: `./init.sh`
Expected: 全綠。勾選 `openspec/changes/rag-eval-metrics/tasks.md` 全部項目並 commit。

---

### Task 7: openspec change `rag-retrieval-tuning` 骨架

**Files:**
- Create: `openspec/changes/rag-retrieval-tuning/.openspec.yaml`
- Create: `openspec/changes/rag-retrieval-tuning/proposal.md`
- Create: `openspec/changes/rag-retrieval-tuning/design.md`
- Create: `openspec/changes/rag-retrieval-tuning/tasks.md`

**Interfaces:**
- Consumes: Task 2 的 `Document.metadata["original_title"]`；Task 3、6 的指標（用於驗證）
- Produces: 供 Task 8–11 勾選的 `tasks.md`

- [ ] **Step 1: 建立 `.openspec.yaml`**

```yaml
schema: spec-driven
created: 2026-08-08
```

- [ ] **Step 2: 撰寫 `proposal.md`**

`## Why` — 兩個實測根因：
1. 向量檢索的 `min_score=0.5` 硬門檻在候選進 reranker 前就先過濾，
   與「wide retrieve → rerank」的架構意圖相反（第一階段應衝 recall）。
2. 上游 ETL 以 `f"主題：{title}\n內容：{chunk}"` 產生 embedding，
   但寫入 Mongo 的 `chunk_content` 不含標題，導致 Cohere reranker
   收到的是缺語境的斷句碎片，與向量空間所見文本不一致。

`## What Changes`：
- `DEFAULT_MIN_SCORE` 由 `0.5` 改為 `0.0`，並新增 env `RAG_VECTOR_MIN_SCORE`
- reranker 送出的 document 文本改為 `主題：{original_title}\n內容：{chunk}`，
  無標題時退回純內容
- 新增 `scripts/purge_navigation_chunks.py` 清除 229 筆導覽列噪音（預設 dry-run）
- 修正 `resources/atlas_text_search_index.json` 欄位名 `text` → `chunk_content`
- **非 BREAKING**

`## Capabilities` — `### Modified Capabilities`：
`rag-responses` 的檢索行為（移除向量分數硬門檻，過濾職責移交 reranker）。

`## Impact` — 程式：`retriever.py`、`config.py`、`cohere_reranker.py`、
`resources/atlas_text_search_index.json`、`scripts/` 新增一支；
`.env.example` 新增 `RAG_VECTOR_MIN_SCORE`；無新相依套件。

- [ ] **Step 3: 撰寫 `design.md`**

`## Decisions` 需涵蓋：
- **D1 為何移除 min_score 而非調低**：絕對門檻對 cosine 才成立，
  且 RRF 融合後 `metadata["score"]` 已被覆寫為融合分數，
  對 hybrid 路徑套用 0.5 門檻在語意上是錯的。保留參數但預設 0.0。
- **D2 reranker 文本格式對齊 embedding**：格式刻意與上游
  `main_pipeline.get_embedding` 的 `f"主題：{title}\n內容：{chunk}"` 完全一致，
  使三個階段（向量／BM25／rerank）看到的語境盡量收斂。
- **D3 為何不順手改 BM25 索引**：`chunk_content` 加標題需重寫 4,605 筆文件，
  屬上游 ETL 職責（見 `docs/care-data-issues.md`），本 change 不做。

- [ ] **Step 4: 撰寫 `tasks.md`**（對應 Task 8–11，引用對應 pytest 路徑，
      最後一節含 Definition of Done：`./init.sh` 全綠）

- [ ] **Step 5: 建立 spec delta `specs/rag-responses/spec.md`**

同 Task 1 Step 5 的理由：`openspec archive` 靠這個檔案併回 `openspec/specs/`。
建立 `openspec/changes/rag-retrieval-tuning/specs/rag-responses/spec.md`：

```markdown
## MODIFIED Requirements

### Requirement: 向量檢索候選過濾

向量檢索 SHALL NOT 以固定的相似度門檻過濾候選文件；預設 `RAG_VECTOR_MIN_SCORE` 為 `0.0`，第一階段的職責是最大化召回，過濾與排序 SHALL 由精排階段負責。系統 SHALL 保留該設定項，使需要時可由環境變數調回非零門檻。

送入精排的文件文本 SHALL 與建立 embedding 時的格式一致：當文件具備 `original_title` 時，SHALL 組為「主題：{original_title}\n內容：{chunk}」；缺標題時 SHALL 退回純內容。精排回傳的文件 `page_content` SHALL 維持原始 chunk 內容不變。

#### Scenario: 低分候選仍進入精排

- **WHEN** 向量檢索取回的文件中包含相似度低於 0.5 的候選
- **THEN** 這些候選仍送入精排階段，由精排決定去留

#### Scenario: 精排輸入帶標題

- **WHEN** 候選文件具備 `original_title`
- **THEN** 送往精排 API 的文本為「主題：{標題}\n內容：{內容}」，而回傳文件的 `page_content` 仍為原始 chunk 內容
```

- [ ] **Step 6: Commit**

```bash
git add openspec/changes/rag-retrieval-tuning
git commit -m "docs(openspec): 新增 rag-retrieval-tuning change 提案"
```

---

### Task 8: 移除向量分數硬門檻

**Files:**
- Modify: `app/services/rag/retriever.py:26`（`DEFAULT_MIN_SCORE`）
- Modify: `app/core/config.py`
- Modify: `app/dependencies.py`
- Modify: `.env.example`
- Test: `tests/unit/services/rag/test_retriever.py`

**Interfaces:**
- Consumes: 無
- Produces: `settings.RAG_VECTOR_MIN_SCORE: float`（預設 `0.0`）

- [ ] **Step 1: 寫失敗測試**

沿用 `test_retriever.py` 既有的 `_make_retriever()` + `MagicMock` 假件寫法
（全檔一致，勿手刻假件類別）。注意 `_make_retriever` 的 `text_field` 預設是
`"chunk_text"`，測資的鍵名要跟著用：

```python
@pytest.mark.asyncio
async def test_retriever_keeps_low_score_docs_by_default():
    retriever, emb = _make_retriever(vector_dim=2)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {"_id": "1", "chunk_text": "高分", "score": 0.9},
            {"_id": "2", "chunk_text": "低分", "score": 0.12},
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("高血壓")

    # 過濾職責移交 reranker，第一階段不再砍低分候選
    assert [d.page_content for d in docs] == ["高分", "低分"]


@pytest.mark.asyncio
async def test_retriever_still_honours_explicit_min_score():
    retriever, emb = _make_retriever(vector_dim=2, min_score=0.5)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {"_id": "1", "chunk_text": "高分", "score": 0.9},
            {"_id": "2", "chunk_text": "低分", "score": 0.12},
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("高血壓")
    assert [d.page_content for d in docs] == ["高分"]
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_retriever.py -v -k min_score`
Expected: 第一個測試 FAIL（低分被 0.5 門檻濾掉），第二個 PASS

- [ ] **Step 3: 改預設值**

`app/services/rag/retriever.py`：

```python
# 第一階段負責衝 recall，過濾交給 reranker（見 openspec/changes/rag-retrieval-tuning）。
# 保留參數以便需要時由 env 調回。
DEFAULT_MIN_SCORE = 0.0
```

- [ ] **Step 4: 新增設定並接線**

`app/core/config.py`（放在 `RAG_RRF_K` 之後）：

```python
    # 向量檢索最低分門檻。預設 0.0＝不過濾；過濾職責在 reranker。
    RAG_VECTOR_MIN_SCORE: float = float(os.getenv("RAG_VECTOR_MIN_SCORE", "0.0"))
```

`app/dependencies.py` 建立 `MongoAtlasVectorRetriever` 時加入
`min_score=settings.RAG_VECTOR_MIN_SCORE,`。

`.env.example` 加入：

```
# 向量檢索最低分門檻（0.0＝不過濾，過濾交給 reranker）
RAG_VECTOR_MIN_SCORE=0.0
```

- [ ] **Step 5: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/ -v`
Expected: PASS

- [ ] **Step 6: 跑 eval 比對，並確認延遲未惡化**

```bash
time .venv/bin/python scripts/rag_eval.py --rank-mode cohere --top-n 5 --out /tmp/rag-c1.json
```

把 `hit_rate` / `mean_mrr` / `mean_ndcg_at_5` 與 Task 3 Step 10 的數字並列記錄到
`openspec/changes/rag-retrieval-tuning/design.md`。

延遲檢查：候選數由 `RAG_RETRIEVE_CANDIDATES=40` 固定，移除門檻只影響
「實際送進 reranker 的筆數」上限（原本可能不足 40，現在會接近 40）。
比對本次與 Task 3 Step 10 的 `time` 總耗時，除以題數得到每題平均。
若每題增加超過 300ms，記錄到 design.md 的風險節，並考慮把
`RAG_RETRIEVE_CANDIDATES` 調回較小值 —— 但**不要**改回用 `min_score` 過濾。

- [ ] **Step 7: Commit**

```bash
git add app/services/rag/retriever.py app/core/config.py app/dependencies.py \
        .env.example tests/unit/services/rag/test_retriever.py
git commit -m "fix(rag): 移除向量分數硬門檻，過濾職責移交 reranker"
```

---

### Task 9: reranker 輸入補回標題

**Files:**
- Modify: `app/services/rag/cohere_reranker.py`
- Test: `tests/unit/services/rag/test_cohere_reranker.py`

**Interfaces:**
- Consumes: Task 2 的 `Document.metadata["original_title"]`
- Produces: `rerank_document_text(doc: Document) -> str`（模組層級函式）

- [ ] **Step 1: 寫失敗測試**

```python
import pytest
from langchain_core.documents import Document

from app.services.rag.cohere_reranker import CohereReranker, rerank_document_text


def test_rerank_document_text_prefixes_title():
    doc = Document(
        page_content="幽門螺旋桿菌與胃癌風險有關。",
        metadata={"original_title": "捍「胃」健康 過年聚餐用公筷"},
    )
    assert rerank_document_text(doc) == (
        "主題：捍「胃」健康 過年聚餐用公筷\n內容：幽門螺旋桿菌與胃癌風險有關。"
    )


def test_rerank_document_text_falls_back_to_content_without_title():
    doc = Document(page_content="純內容", metadata={"original_title": None})
    assert rerank_document_text(doc) == "純內容"


@pytest.mark.asyncio
async def test_cohere_reranker_sends_title_prefixed_documents():
    captured: dict = {}

    async def fake_post(url, *, headers, json, timeout):
        captured["json"] = json
        return {"results": [{"index": 0, "relevance_score": 0.9}]}

    reranker = CohereReranker(
        api_key="k", model="rerank-v4.0-pro", http_post=fake_post
    )
    docs = [
        Document(page_content="內容A", metadata={"original_title": "標題A"}),
        Document(page_content="內容B", metadata={}),
    ]

    await reranker.rerank("q", docs, top_n=2)

    assert captured["json"]["documents"] == ["主題：標題A\n內容：內容A", "內容B"]
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_cohere_reranker.py -v -k "title or rerank_document_text"`
Expected: FAIL — `ImportError: cannot import name 'rerank_document_text'`

- [ ] **Step 3: 實作**

在 `cohere_reranker.py` 的 `Reranker` protocol 之前新增：

```python
def rerank_document_text(doc: Document) -> str:
    """組出送進 reranker 的文本。

    上游 ETL 產生 embedding 時用的是 f"主題：{title}\\n內容：{chunk}"，
    但寫入 Mongo 的 chunk_content 不含標題。若直接把 chunk_content 丟給
    reranker，reranker 讀到的會是缺語境的斷句碎片，與向量空間所見不一致。
    這裡刻意重建同樣的格式，讓兩階段看到的語境收斂。
    """
    content = doc.page_content or ""
    title = str(doc.metadata.get("original_title") or "").strip()
    if not title:
        return content
    return f"主題：{title}\n內容：{content}"
```

`CohereReranker.rerank` 中把：

```python
        documents = [d.page_content for d in docs]
```

改為：

```python
        documents = [rerank_document_text(d) for d in docs]
```

註記：只改送進 API 的文本，回傳的 `Document.page_content` 仍是原始
`chunk_content`，因此 context 組裝與來源顯示不受影響。

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_cohere_reranker.py -v`
Expected: PASS

- [ ] **Step 5: 跑 eval 比對（本計畫預期增益最大的一步）**

Run: `.venv/bin/python scripts/rag_eval.py --compare-rerank --top-n 5 --out /tmp/rag-c2.json`
與 Task 8 Step 6 的數字並列記錄。特別關注 `mean_ndcg_at_5` —— 這一項最能反映
reranker 拿到完整語境後的排序改善。

- [ ] **Step 6: Commit**

```bash
git add app/services/rag/cohere_reranker.py tests/unit/services/rag/test_cohere_reranker.py
git commit -m "fix(rag): reranker 輸入補回標題，與 embedding 文本格式對齊"
```

---

### Task 10: 清除導覽列噪音腳本

**Files:**
- Create: `scripts/purge_navigation_chunks.py`
- Test: `tests/unit/scripts/test_purge_navigation_chunks.py`（若 `tests/unit/scripts/` 不存在則建立，含 `__init__.py` 視既有慣例而定）

**Interfaces:**
- Consumes: 無
- Produces: `NAVIGATION_URLS: tuple[str, ...]`、`build_delete_filter(urls) -> dict`、`async def purge(collection, urls, *, apply: bool) -> dict[str, int]`

- [ ] **Step 1: 寫失敗測試**

```python
import pytest

from scripts.purge_navigation_chunks import (
    NAVIGATION_URLS,
    build_delete_filter,
    purge,
)


class _FakeDeleteResult:
    def __init__(self, n):
        self.deleted_count = n


class _FakeCollection:
    def __init__(self, counts):
        self._counts = counts
        self.deleted_filters = []

    async def count_documents(self, flt):
        url = flt["url"]
        return self._counts.get(url, 0)

    async def delete_many(self, flt):
        self.deleted_filters.append(flt)
        return _FakeDeleteResult(sum(self._counts.values()))


def test_navigation_urls_are_homepages_or_malformed():
    assert "https://www.mohw.gov.tw/" in NAVIGATION_URLS
    assert "https://www.hpa.gov.tw/..." in NAVIGATION_URLS
    # 實際文章頁不得誤入清單
    assert not any("pid=19853" in u for u in NAVIGATION_URLS)


def test_build_delete_filter_uses_url_in():
    flt = build_delete_filter(["https://a", "https://b"])
    assert flt == {"url": {"$in": ["https://a", "https://b"]}}


@pytest.mark.asyncio
async def test_purge_dry_run_does_not_delete():
    collection = _FakeCollection({"https://www.mohw.gov.tw/": 114})
    report = await purge(
        collection, ["https://www.mohw.gov.tw/"], apply=False
    )
    assert report["matched"] == 114
    assert report["deleted"] == 0
    assert collection.deleted_filters == []


@pytest.mark.asyncio
async def test_purge_apply_deletes():
    collection = _FakeCollection({"https://www.mohw.gov.tw/": 114})
    report = await purge(
        collection, ["https://www.mohw.gov.tw/"], apply=True
    )
    assert report["deleted"] == 114
    assert collection.deleted_filters == [
        {"url": {"$in": ["https://www.mohw.gov.tw/"]}}
    ]
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_purge_navigation_chunks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.purge_navigation_chunks'`

- [ ] **Step 3: 實作腳本**

`scripts/purge_navigation_chunks.py`：

```python
#!/usr/bin/env python3
"""清除以 Firecrawl 抓首頁而產生的導覽列噪音 chunk。

這批資料由 CARE 的 IngestService 對首頁 URL 執行 ingest 產生，內容是
「一站式搜尋」「## 主視覺與專區連結」「[跳到主要內容區塊]」這類導覽元素，
對醫療問答無檢索價值，且每筆都佔一個 3072 維向量。

刻意以「明列 URL」而非「content_hash 是否存在」為條件：後者會連帶刪除
未來由知識回報審核流程正常寫入的資料。

用法：
  python scripts/purge_navigation_chunks.py            # dry-run，只報告
  python scripts/purge_navigation_chunks.py --apply    # 實際刪除
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings

# 2026-08-08 實測：以下 URL 下的 chunk 全為網站導覽元素，無文章正文。
# https://www.hpa.gov.tw/... 為格式損毀的 URL（內容是客服電話清單）。
NAVIGATION_URLS: tuple[str, ...] = (
    "https://www.mohw.gov.tw/",
    "https://www.fda.gov.tw/",
    "https://165.npa.gov.tw/",
    "https://www.hpa.gov.tw/",
    "https://www.hpa.gov.tw/...",
    "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=5020&pid=19922",
)


def build_delete_filter(urls: list[str] | tuple[str, ...]) -> dict:
    return {"url": {"$in": list(urls)}}


async def purge(collection, urls, *, apply: bool) -> dict[str, int]:
    matched = 0
    for url in urls:
        count = await collection.count_documents({"url": url})
        print(f"  {count:>5}  {url}")
        matched += count

    deleted = 0
    if apply and matched:
        result = await collection.delete_many(build_delete_filter(urls))
        deleted = result.deleted_count

    return {"matched": matched, "deleted": deleted}


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="實際執行刪除；未指定時只報告不刪除",
    )
    args = parser.parse_args()

    client = AsyncIOMotorClient(settings.MONGODB_URI)
    collection = client[settings.MONGODB_DB][settings.MONGODB_COLLECTION]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== 導覽列噪音清理（{mode}）===")
    report = await purge(collection, NAVIGATION_URLS, apply=args.apply)
    print(f"\n符合條件: {report['matched']} 筆")
    if args.apply:
        print(f"已刪除:   {report['deleted']} 筆")
    else:
        print("未刪除（加上 --apply 才會實際執行）")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_purge_navigation_chunks.py -v`
Expected: PASS

- [ ] **Step 5: 先跑 dry-run 確認筆數**

Run: `.venv/bin/python scripts/purge_navigation_chunks.py`
Expected: 合計 266 筆（114 + 49 + 45 + 13 + 8 + 37）。
**若數字明顯不符，停下來人工檢查，不要直接 `--apply`。**

- [ ] **Step 6: 實際刪除並跑 eval 確認無回歸**

```bash
.venv/bin/python scripts/purge_navigation_chunks.py --apply
.venv/bin/python scripts/rag_eval.py --rank-mode cohere --top-n 5 --out /tmp/rag-c3.json
```

Expected: 指標不應下降（刪除的是噪音）。若下降，代表清單誤含有效資料，
須以 git 記錄的 URL 清單回頭檢查。

- [ ] **Step 7: Commit**

```bash
git add scripts/purge_navigation_chunks.py tests/unit/scripts/test_purge_navigation_chunks.py
git commit -m "chore(rag): 新增導覽列噪音清理腳本（預設 dry-run）"
```

---

### Task 11: Atlas Search index 範本修正

**Files:**
- Modify: `resources/atlas_text_search_index.json`

**Interfaces:**
- Consumes: 無
- Produces: 無（純設定檔）

- [ ] **Step 1: 修正欄位名**

線上實際的 `care_text_index` 索引的是 `chunk_content`，但範本檔寫的是 `text`，
且 `mappings.dynamic` 為 `false` —— 照範本重建會使 BM25 完全失效。
把 `mappings.fields` 的鍵由 `text` 改為 `chunk_content`：

```json
  "mappings": {
    "dynamic": false,
    "fields": {
      "chunk_content": {
        "type": "string",
        "analyzer": "lucene.cjk",
        "searchAnalyzer": "lucene.cjk"
      },
      "source_name": {
        "type": "string",
        "analyzer": "lucene.cjk"
      },
      "url": {
        "type": "string",
        "analyzer": "lucene.keyword"
      }
    }
  }
```

並把 `_comment` 最後一行由「若 MONGODB_TEXT_FIELD 不是 'text'」改為
「本檔已對齊線上索引定義（2026-08-08 核對）；若 MONGODB_TEXT_FIELD 改變，
下方 fields 的鍵名要一併改掉」。

- [ ] **Step 2: 驗證與線上定義一致**

Run:

```bash
.venv/bin/python - <<'PY'
import asyncio, json, os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path.cwd() / ".env")
from motor.motor_asyncio import AsyncIOMotorClient

async def main():
    c = AsyncIOMotorClient(os.environ["MONGODB_URI"])
    col = c[os.environ["MONGODB_DB"]][os.environ["MONGODB_COLLECTION"]]
    async for idx in col.list_search_indexes():
        if idx.get("name") == os.environ["MONGODB_TEXT_INDEX"]:
            live = idx["latestDefinition"]["mappings"]
    tmpl = json.loads(Path("resources/atlas_text_search_index.json").read_text(encoding="utf-8"))
    assert live == tmpl["mappings"], f"不一致\nlive={live}\ntmpl={tmpl['mappings']}"
    print("✅ 範本與線上索引定義一致")
    c.close()

asyncio.run(main())
PY
```

Expected: `✅ 範本與線上索引定義一致`

- [ ] **Step 3: Commit**

```bash
git add resources/atlas_text_search_index.json
git commit -m "fix(rag): Atlas Search index 範本欄位名對齊線上（text → chunk_content）"
```

- [ ] **Step 4: 收尾 change**

Run: `./init.sh`
Expected: 全綠。勾選 `openspec/changes/rag-retrieval-tuning/tasks.md` 全部項目並 commit。

---

### Task 12: CARE-data 問題報告

**Files:**
- Create: `docs/care-data-issues.md`

**Interfaces:**
- Consumes: 無
- Produces: 無（交付給上游 repo 維護者的文件）

- [ ] **Step 1: 撰寫報告**

對象是 `Capoo0618/CARE-data` 的維護者。每一項採「現象 → 實測數據 → 根因（指到檔案行號）→ 建議修改」結構。必須涵蓋下列八項，且**每一項都要附本次實測到的具體數字或字串證據**：

1. **embedding 編在 query 空間**（`main_pipeline.py:25-47`）
   實測 `cos(未指定, RETRIEVAL_QUERY) = 1.000000`、
   `cos(未指定, RETRIEVAL_DOCUMENT) = 0.927816`。
   建議 payload 加 `"taskType": "RETRIEVAL_DOCUMENT"`。
   須註明：本次僅證實預設值等同 QUERY，**實際排序影響需 A/B 驗證**，
   不宜宣稱必然改善。

2. **標題只進 embedding、未寫入 `chunk_content`**（`main_pipeline.py:74,80`）
   三階段對照表（向量有標題／BM25 無／rerank 無）。
   建議把 `f"主題：{title}\n內容：{chunk}"` 一併寫入 `chunk_content`，
   或另存 `contextualized_content` 欄位供 BM25 與 rerank 使用。

3. **`clean_html` 是 regex 而非 BeautifulSoup**（`utils.py:5-11`，與 README「技術架構」不符）
   `re.sub(r'<[^>]+>', '', ...)` 只去標籤不去內容，`<script>`/`<style>` 內文會混入正文。
   建議改用 `BeautifulSoup(raw, "html.parser")`，先 `decompose()` 掉
   `script`/`style`，再以 `get_text(separator="\n")` 取文。

4. **段落結構被壓平**（`utils.py:10`）
   `re.sub(r'\s+', ' ', ...)` 把換行併成空格，導致下游無 `\n\n` 可切、只能硬切字元。
   建議保留段落分隔（僅壓縮行內連續空白）。

5. **固定 500 字元硬切**（`main_pipeline.py:17-23`）
   實測 DB 中 chunk 長度 p25=248 / median=500 / p90=500，
   句子從中間斷開（例：`'元整及55萬8,000元。國民健康署呼籲...'`）；
   尾段殘渣 127 筆長度 1 字元（`'3'`、`'。'`、`'×'`），480 筆 <100 字元（10.4%）。
   建議先按段落切、超長段落再按句界（`。！？`）切，並丟棄過短殘渣。

6. **食藥署文章無 URL**（`scraper_api.py:44`）
   實測該 API 欄位僅 `['標題', '內容', '附檔連結', '發布日期']`，
   `附檔連結` 值為字串 `'None'`。導致 1,367 筆（KB 的 30%）`url=None`，
   在 CARE 端永遠無法被列為參考來源。
   建議改抓食藥署闢謠專區的網頁列表以取得文章網址，或在文件中明確標記
   此來源不具可連結網址，由下游改以「來源名｜標題」呈現。

7. **Early stopping 會漏抓**（`main_pipeline.py:63-67`）
   遇到單篇已存在即 `skipped_sources.add(source_name)`，該來源後續全部跳過。
   若來源非嚴格時間排序，或中間某篇曾寫入失敗，後續新文章將永遠補不回來。
   實測佐證：HPA API 回傳 1,000 筆，DB 僅 910 個 URL。
   建議改為「連續 N 篇皆已存在才停」，或直接以 URL/標題集合做差集。

8. **只 insert 不 update**（`main_pipeline.py:76-85`）
   兩支 API 都提供 `發布日期`（HPA 另有 `修改日期`），但目前未寫入 Mongo。
   文章改版後知識庫永不更新，與 CARE 的「資訊過時」知識回報需求直接衝突。
   建議存入 `published_at` / `updated_at`，並改為依 `修改日期` 判斷是否 upsert。

另補一項次要事項：`scraper_api.py:32`、`scraper_tfc.py:37,86` 使用
`verify=False` 關閉 TLS 憑證驗證。對政府網站無此必要，建議移除。

報告末尾加一節「**建議不要做的事**」，說明為何不需改用 Firecrawl：
91% 資料來自兩支回傳結構化 JSON 的政府 API；CARE 端已有 Firecrawl 實測結果
（266 筆首頁導覽列噪音）；真正需要網頁爬取的只有 TFC 的 132 chunks（2.9%）。

- [ ] **Step 2: 自我檢查**

確認報告中沒有任何未經實測的斷言。特別檢查第 1 項是否已明確標註
「需 A/B 驗證才能宣稱改善」。

- [ ] **Step 3: Commit**

```bash
git add docs/care-data-issues.md
git commit -m "docs: CARE-data ETL 問題報告（含實測數據）"
```

---

## 完成後

1. 兩個 change 的 `tasks.md` 全數勾選且 `./init.sh` 全綠
2. 依 `openspec/config.yaml` 工作流程，合併後執行：
   `openspec archive rag-eval-metrics` 與 `openspec archive rag-retrieval-tuning`
3. 把各階段的 `hit_rate` / `mean_mrr` / `mean_ndcg_at_5` / `citation_coverage`
   整理成一張對照表，更新 `evals/rag/README.md` 末尾的實測記錄
   （取代目前的 `top-5 vector 0.29 → cohere 0.44`，並標明口徑已變更）
4. 把 `docs/care-data-issues.md` 交給 `Capoo0618/CARE-data` 維護者
