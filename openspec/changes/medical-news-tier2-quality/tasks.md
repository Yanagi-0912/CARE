# Tasks

## 1. 訂正未經查證的註解（不改行為）

- [x] 1.1 `app/services/medical_news/relevance.py` 的 `_ROC_YEAR_OFFSET` 註解：
      刪掉「食藥署與衛福部的頁面普遍以民國年呈現」，改記 2026-09-04 的量測結果
      （2,422 筆中民國格式 0 筆），並寫明民國支援為何仍要保留
- [x] 1.2 `app/services/medical_news/kb_digest_service.py` 的 `recent_articles`
      docstring：日期過濾放 Python 的理由改為真正的那個（佔位字串與未來日期容忍
      度不是 Mongo 比較運算子表達得出來的），並註明字串排序等同時序排序
- [x] 1.3 `tests/.../test_kb_digest_service.py::test_roc_dates_are_accepted` 的
      docstring 同步訂正；測試本身保留

## 2. 標題黑名單（第一道，不花額度）

- [x] 2.1 `relevance.POLICY_ANNOUNCEMENT_KEYWORDS` 與 `is_policy_announcement`，
      註解記錄量測方法與結果
- [x] 2.2 `kb_digest_service._to_article` 套用，位置在 grader 之前
- [x] 2.3 `tests/unit/services/medical_news/test_relevance.py`：10 個真實正例、
      10 個真實反例、空標題
- [x] 2.4 `tests/unit/services/medical_news/test_kb_digest_service.py`：
      `test_policy_announcements_are_excluded`

## 3. Tier 2 的 LLM 內容判定（第二道）

- [x] 3.1 新增 `app/services/medical_news/article_grader.py`：`ARTICLE_SCHEMA`、
      `ArticleJudgement`、`KbArticleGrader` Protocol、`parse_article_judgement`、
      `GeminiKbArticleGrader`
- [x] 3.2 `KbDigestService` 接受 `grader` 與 `max_grade_calls`；fail closed，
      額度用完即停止選材
- [x] 3.3 `tier2_pool` 觀測 log，`grader_errors` 非零時提到 error 級別
- [x] 3.4 `settings.MEDICAL_NEWS_TIER2_GRADER_ENABLED` 與
      `MEDICAL_NEWS_TIER2_GRADE_MAX_CALLS`
- [x] 3.5 `app/dependencies.py` 組裝；grader 缺席時 Tier 2 仍照常供應
- [x] 3.6 `tests/unit/services/medical_news/test_article_grader.py`：輸出解析、
      字串 verdict 不得被 coerce、例外不得被吞
- [x] 3.7 `tests/.../test_kb_digest_service.py`：兩道過濾的順序、額度用完的行為、
      grader 缺席時的降級

## 4. 池子的個人化錯開

- [x] 4.1 `push_scheduler._pool_offset`（blake2b，非內建 `hash()`）
- [x] 4.2 `_pick_tier2` 依 `user_id` 錯開起點，去重仍優先
- [x] 4.3 `tests/.../test_push_scheduler.py`：偏移穩定性直接驗算 blake2b 期望值、
      新使用者不得全部拿到同一篇、去重優先於偏移、全部收過時不推

## 5. 驗收

- [x] 5.1 `python -m pytest tests/` 全綠（3,271 passed）
- [ ] 5.2 上線後觀察 `tier2_pool` 的 `picked` / `rejected` / `grader_errors` 分佈
      （design 證據缺口 1），並依結果調 prompt 而非關閉防線
