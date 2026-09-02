# 實作計畫：每日醫療消息卡與認同分享

> 依 `proposal.md` 與 `design.md`。每個小節結束時 pytest 應為綠、且是一個獨立可審查的 commit。

## 全域約束（每項任務都適用）

- **禁止 monkey patch**（`unittest.mock.patch` 修改全域或別處導入的實例）。一律以依賴注入
  傳入替身——每個新 service 的建構子都要能接收 collection／replier／grader 等外部相依。
  這是 `openspec/config.yaml` 的 rules.tasks 明文規定。
- **LINE 回覆一律純文字或 Flex，不得輸出 Markdown**（`specs/line-reply-rules`）。
- **`Medication.indication`、`Medication.spc_indication`、`Medication.spc_indication_summary`
  SHALL NOT 出現在任何推播或分享訊息中。** 模型檔上有明文禁令（`app/models/medication.py`），
  理由是適應症直接揭露病情。本功能新增的三張卡片都要有測試鎖住這一點。
- 所有 Flex 文字節點的 `size` 一律取自傳入的 `theme.FlexTheme`，不得寫死。
- 所有 Flex bubble 送出前必須過 `resources/flex_messages/size_guard.fits()`。
- 面向使用者的文案一律走 `app/i18n/messages.py` 的 `t()`，不得寫死中文字串。
- 完成定義（DoD）：`./init.sh` 全綠且有清楚的 git commit。

## 1. 資料模型與 collection

- [x] 1.1 `app/models/medical_news.py`：
      - `NewsKind = Literal["drug_news", "kb_article"]`
      - `def make_news_ref(kind: NewsKind, key: str) -> str`——回傳 `f"{kind}:{sha256(key).hexdigest()[:32]}"`。
        雜湊而非原字串：`kb_article` 的 key 是文章 url，Mongo 單一索引鍵上限 1024 bytes，
        長 url 會讓 insert 直接失敗，而失敗點會落在推播路徑上。
      - `class DrugNews(BaseModel)`：`id`（alias `_id`）、`drug_key: str`、
        `key_kind: Literal["ingredient", "name_zh"]`、`url: str`、`title: str`、
        `source_name: str`、`published_at: Optional[str]`、`summary: str`、
        `concern_kind: Literal["recall", "safety", "supply", "education"]`、
        `content_hash: str`、`indexed_at: datetime`
      - `class MedicalNewsDelivery(BaseModel)`：`user_id`、`news_ref`、
        `tier: Literal[1, 2]`、`pushed_at`、`shared_at: Optional[datetime]`、
        `share_recipient_count: int = 0`
      - `class MedicalNewsShare(BaseModel)`：`recipient_id`、`news_ref`、`sharer_id`、`sent_at`
      - 全部沿用 `model_config = ConfigDict(populate_by_name=True)`，與 `app/models/medication.py` 一致
- [x] 1.2 `app/db/mongodb.py`：新增 `get_drug_news_collection()`、
      `get_medical_news_deliveries_collection()`、`get_medical_news_shares_collection()`，
      形狀比照既有的 `get_medications_collection()`
- [x] 1.3 `app/repositories/medical_news_repository.py`，三個 class，方法皆為 `@staticmethod`
      且末參數為 `collection: Optional[Any] = None`（與 `medication_repository.py` 同一慣例，
      測試靠這個參數注入替身）：
      - `DrugNewsRepository.upsert_by_url(news: DrugNews) -> bool`——以 `url` 為鍵，
        回傳是否為新插入
      - `DrugNewsRepository.find_by_drug_keys(drug_keys: list[str], since: str) -> list[DrugNews]`——
        `published_at >= since`，依 `published_at` 遞減排序
      - `MedicalNewsDeliveryRepository.claim(user_id: str, news_ref: str, tier: int) -> bool`——
        `insert_one` 成功回 True，`DuplicateKeyError` 回 False。**這就是去重與原子搶佔**
        （design 決策 10），不得改成先查再寫
      - `MedicalNewsDeliveryRepository.list_pushed_refs(user_id: str, since: datetime) -> set[str]`
      - `MedicalNewsDeliveryRepository.mark_shared(user_id: str, news_ref: str, recipient_count: int) -> None`
      - `MedicalNewsDeliveryRepository.count_shares_today(user_id: str, day_start: datetime) -> int`
      - `MedicalNewsShareRepository.claim(recipient_id: str, news_ref: str, sharer_id: str) -> bool`——
        同上以唯一索引搶佔
      - 模組層函式 `async def ensure_indexes(...) -> None`（不掛在任何 class 下，因為它要
        一次建立三個 collection 的索引）——`drug_news` 的 `url` unique 與 `(drug_key, published_at desc)`；
        `medical_news_deliveries` 的 `(user_id, news_ref)` unique；
        `medical_news_shares` 的 `(recipient_id, news_ref)` unique
- [x] 1.4 測試 `tests/unit/models/test_medical_news_models.py`
      - `test_make_news_ref_is_stable_for_same_key`：同一組 (kind, key) 兩次呼叫結果相同
      - `test_make_news_ref_differs_across_kinds`：`drug_news:` 與 `kb_article:` 同 key 不相撞
      - `test_make_news_ref_length_is_bounded`：任意長度 url 產出的 ref 長度固定，不隨輸入成長
- [x] 1.5 測試 `tests/unit/repositories/test_medical_news_repository.py`（以 fake collection
      物件注入，不 monkey patch）
      - `test_claim_returns_false_on_duplicate_key`：第二次 claim 同一組 (user_id, news_ref)
        回 False——鎖住「推過就不再推」
      - `test_claim_returns_true_on_first_insert`
      - `test_find_by_drug_keys_filters_by_published_at`
      - `test_upsert_by_url_reports_insert_versus_update`
- [x] 1.6 commit：`feat(medical-news): 資料模型與 repository`

## 2. 用藥藥品鍵查詢

- [ ] 2.1 `app/repositories/medication_repository.py::MedicationRepository` 新增兩個
      `@staticmethod`：
      - `list_active_drug_keys(date_str: str, collection=None) -> list[str]`——對
        `{"enabled": True, "$and": _active_date_window(date_str)}` 取 `name` 與 `generic_name`
        的 distinct 聯集，去掉空值。**這支查詢刻意不帶 user_id**：索引服務要的是全體不重複
        藥名，與是誰在吃無關（design 決策 2）
      - `list_active_by_user(user_id: str, date_str: str, collection=None) -> list[Medication]`——
        沿用 `_active_date_window`，與既有的 `find_active_by_ids` 同一個日期濾網
- [ ] 2.2 測試 `tests/unit/repositories/test_medication_repository.py` 追加
      - `test_list_active_drug_keys_unions_name_and_generic_name`
      - `test_list_active_drug_keys_excludes_expired_course`：`end_date` 早於當日的藥不出現
      - `test_list_active_drug_keys_has_no_user_filter`：斷言送出的 query 不含 `user_id` 鍵
      - `test_list_active_by_user_respects_date_window`
- [ ] 2.3 commit：`feat(medical-news): 用藥藥品鍵查詢`

## 3. 相關性防線（純函式）

- [ ] 3.1 `app/services/medical_news/relevance.py`：
      - `def mentions_drug(text: str, drug_key: str) -> bool`——以
        `drug_catalog_service.normalize_drug_name()` 正規化兩邊後做子字串比對。
        **SHALL NOT 做模糊比對**：`DrugCatalogService._match_by_fuzzy` 的門檻是為藥袋 OCR
        的讀錯而設，這裡的輸入是官方公告的正確文字，放寬只會製造偽陽性（design 決策 5-2）
      - `FORBIDDEN_ADVICE_PATTERNS: tuple[str, ...]`——停藥、換藥、增量、減量、加量、
        自行調整、不要再吃、停止服用、改吃
      - `def violates_output_guard(text: str) -> bool`——命中任一 pattern 即 True
      - `def has_usable_date(published_at: str | None) -> bool`
      - `def is_recent(published_at: str, today: str, max_age_days: int) -> bool`
- [ ] 3.2 測試 `tests/unit/services/medical_news/test_relevance.py`
      - `test_mentions_drug_matches_after_normalization`：「普拿疼錠500毫克」文字命中「普拿疼」
      - `test_mentions_drug_rejects_similar_but_different_name`：「胃能錠」不命中「欲胃能錠」
        ——鎖住「不得做模糊比對」
      - `test_violates_output_guard_catches_stop_medication_advice`：「建議停藥」為 True
      - `test_violates_output_guard_allows_consult_pharmacist`：「請與您的醫師或藥師確認」為 False
      - `test_has_usable_date_rejects_none_and_blank`
      - `test_is_recent_excludes_beyond_threshold`
- [ ] 3.3 commit：`feat(medical-news): 相關性與輸出防線純函式`

## 4. 結構化相關性判定

- [ ] 4.1 `app/services/medical_news/grader.py`，形狀**逐條比照**
      `app/services/rag/retrieval_grader.py`（同樣的 SCHEMA 常數、Protocol、
      `invoke_*` 注入點三件套）：
      - `NEWS_SCHEMA`：`{is_about_this_drug: bool, concern_kind: enum[recall, safety,
        supply, education, none], summary: string}`，三個欄位皆 required
      - `class NewsJudgement(NamedTuple)`：`is_about_this_drug: bool`、
        `concern_kind: str`、`summary: str`
      - `class NewsGrader(Protocol)`：`async def judge(self, drug_key: str, title: str,
        text: str) -> NewsJudgement`
      - `class GeminiNewsGrader`：建構子 `(gemini_service=None, *, invoke_judge:
        Callable[[str], Awaitable[dict]] | None = None)`——`invoke_judge` 是測試注入點，
        與 `GeminiRetrievalGrader.invoke_grade` 同一個角色
      - prompt 明確要求：摘要**必須是中性第三人稱**、不得使用「您」「你的藥」等第二人稱，
        且不得包含任何停藥或調整劑量的建議（design 決策 6 與 5）
- [ ] 4.2 `judge()` 對不合法輸出（缺欄位、enum 不合法、非 dict）SHALL 拋
      `ValueError`，由呼叫端接住並 fail closed。**SHALL NOT 在此處吞掉例外回一個預設值**
      ——那會讓 fail closed 變成 fail open
- [ ] 4.3 測試 `tests/unit/services/medical_news/test_grader.py`（以 `invoke_judge` 注入）
      - `test_judge_parses_structured_payload`
      - `test_judge_raises_on_unknown_concern_kind`
      - `test_judge_raises_on_missing_field`
      - `test_judge_raises_on_non_dict_payload`
- [ ] 4.4 commit：`feat(medical-news): 結構化相關性判定`

## 5. 索引服務（每日，與使用者無關）

- [ ] 5.1 `app/services/medical_news/index_service.py`：
      `class DrugNewsIndexService`，建構子
      `(*, web_client: WebSearchClient, grader: NewsGrader, repository=DrugNewsRepository,
      medication_repository=MedicationRepository, max_age_days: int, search_limit: int)`
- [ ] 5.2 `async def run_once(self, today: str) -> IndexRunResult`，流程：
      1. `medication_repository.list_active_drug_keys(today)` 取不重複藥名
      2. 每個藥名組 query `f"{drug_key} 藥品 回收 OR 安全資訊 OR 警訊"`，經
         `whitelist.with_whitelist_site_filter()` 後呼叫 `web_client.search()`
      3. 每筆 hit 先過 `whitelist.is_allowed_url()`，不過即丟棄
      4. 再過 `relevance.mentions_drug(hit.title + hit.description, drug_key)`，
         不過即丟棄（**這一步在 LLM 之前，省的是錢也是偽陽性**）
      5. 通過者呼叫 `web_client.scrape_page(url)` 取全文，再呼叫 `grader.judge()`
      6. `is_about_this_drug` 為假或 `concern_kind == "none"` 即丟棄
      7. `relevance.violates_output_guard(summary)` 為真即**整則丟棄，不改寫**
      8. `relevance.has_usable_date()` 為假即丟棄（不進 Tier 1）
      9. `repository.upsert_by_url()` 寫入
- [ ] 5.3 逐藥品以 `try/except` 包住，單一藥品的搜尋逾時或抓取失敗 SHALL 只跳過該藥、
      記一筆 log，SHALL NOT 中斷整輪（design 錯誤處理表）
- [ ] 5.4 `IndexRunResult(NamedTuple)`：`keys_scanned`、`hits_fetched`、`stored`、`skipped`，
      供 log 與後續量測 Tier 1 命中率（design 證據缺口 3）使用
- [ ] 5.5 測試 `tests/unit/services/medical_news/test_index_service.py`（注入 fake
      web_client 與 fake grader）
      - `test_non_whitelisted_url_is_dropped_before_scrape`：斷言 fake client 的
        `scrape_page` 未被呼叫
      - `test_literal_mismatch_is_dropped_before_grader`：斷言 fake grader 的 `judge`
        未被呼叫——鎖住「字面比對先於模型」的順序
      - `test_grader_exception_skips_only_that_drug`：三個藥名其中一個的 grader 拋例外，
        另外兩個仍寫入
      - `test_output_guard_violation_is_discarded_not_rewritten`：summary 含「建議停藥」→
        `upsert_by_url` 未被呼叫
      - `test_missing_published_at_is_not_stored`
      - `test_search_timeout_does_not_abort_run`
- [ ] 5.6 commit：`feat(medical-news): 每日索引服務`

## 6. Tier 2 選材（知識庫近期文章）

- [ ] 6.1 `app/services/medical_news/kb_digest_service.py`：
      `class KbDigestService`，建構子 `(*, collection, max_age_days: int)`，
      collection 為 `settings.MONGODB_COLLECTION`（`health_articles_chunks`）
- [ ] 6.2 `async def recent_articles(self, today: str, limit: int) -> list[KbArticle]`：
      - `KbArticle(NamedTuple)`：`url: str`、`title: str`、`source_name: str`、
        `published_at: str`、`excerpt: str`
      - **SHALL 先以 `url` 收斂為文章再排序**；`url` 為空的來源（食藥署 `DataAction`
        feed 結構上不提供網址）SHALL 整批排除——消息卡必須有可點的來源連結，
        分享卡尤其（design 決策 3）
      - `excerpt` 取該文章 `chunk_index` 最小的那個 chunk 的前 N 字，**不得任取一個
        chunk**：chunk 是切出來的片段，中段的 chunk 單獨呈現常常是半句話（design 決策 9）
- [ ] 6.3 測試 `tests/unit/services/medical_news/test_kb_digest_service.py`
      - `test_chunks_are_collapsed_into_one_article_per_url`：同一 url 的三個 chunk →
        回傳一筆
      - `test_articles_without_url_are_excluded`
      - `test_excerpt_comes_from_first_chunk`
      - `test_results_sorted_by_published_at_desc`
      - `test_articles_older_than_max_age_excluded`
- [ ] 6.4 commit：`feat(medical-news): Tier 2 知識庫選材`

## 7. 消息卡 Flex builder

- [ ] 7.1 `app/services/line_messaging/flex/medical_news_flex.py`，三支 builder，
      版面語彙沿用 `medication_flex.py`（`_header`／`_body`／`_footer`／`_paragraph`）：
      - `build_tier1_news_flex(*, news_ref, drug_name, title, summary, source_name, url,
        language, font_size) -> dict`——header 文案 `t("news.tier1_header")`
        （「與您正在服用的藥有關」），醒目底色（`theme.ALERT` 系），body 含藥名一行、
        標題、中性摘要，footer 兩顆按鈕：來源 URI action + `postback` 的
        `action=share_medical_news&news_ref=<ref>`
      - `build_tier2_news_flex(*, news_ref, title, summary, source_name, url, language,
        font_size) -> dict`——header 文案 `t("news.tier2_header")`（「今日醫療小知識」），
        低調底色（`theme.BRAND`），**不得出現任何藥名**
      - `build_shared_news_flex(*, sharer_name, title, summary, source_name, url,
        language, font_size) -> dict`——header「〈某某〉分享給您」，**無分享按鈕**
        （避免無限轉傳），**不得出現藥名或 Tier 標示**
- [ ] 7.2 三支 builder 都在最後呼叫 `size_guard.fits()`；不合格時先把 `summary` 截短再試，
      仍不合格則拋 `ValueError`，由呼叫端退回 `push_text`（design 錯誤處理表）
- [ ] 7.3 固定行動呼籲：Tier 1 卡 body 末行為 `t("news.consult_professional")`
      （「請與您的醫師或藥師確認」）。這行是常數文案，不由模型產生
- [ ] 7.4 `app/i18n/messages.py` 補上 `news.tier1_header`、`news.tier2_header`、
      `news.shared_by`、`news.consult_professional`、`news.share_button`、
      `news.shared_ok`、`news.no_family`、`news.share_limit_reached`，
      七種語言比照既有 `meds.*` 的補法
- [ ] 7.5 測試 `tests/unit/services/line_messaging/flex/test_medical_news_flex.py`
      - `test_font_size_scales_all_text_nodes`：三種字級各產一張，逐一斷言 text 節點的
        `size` 與 `theme._SIZE_SCALE` 對應
      - `test_tier2_card_contains_no_drug_name`
      - `test_shared_card_contains_no_drug_name`：把藥名傳進上游資料仍不得出現在輸出
      - `test_shared_card_has_no_share_button`
      - `test_share_postback_carries_news_ref`
      - `test_tier1_card_has_consult_professional_line`
      - `test_oversized_summary_is_truncated_then_fits`
      - `test_indication_fields_never_rendered`：即使呼叫端誤傳 indication 文字，
        builder 的介面上根本沒有該參數——以簽章斷言（`inspect.signature`）鎖住
- [ ] 7.6 commit：`feat(medical-news): 消息卡 Flex builder`

## 8. 推播排程器（每日，與使用者相關）

- [ ] 8.1 `app/services/medical_news/push_scheduler.py`：`class MedicalNewsPushScheduler`，
      建構子 `(*, replier: LineReplier, user_profile_service: UserProfileService | None,
      kb_digest: KbDigestService, run_time: str,
      drug_news_repository=DrugNewsRepository,
      delivery_repository=MedicalNewsDeliveryRepository,
      medication_repository=MedicationRepository)`。
      後三個相依以參數帶入而非直接 import 使用：測試禁止 monkey patch，排程器的選材邏輯
      必須能在不碰資料庫的情況下被驗證。
      心跳 `HEARTBEAT_NAME = "medical_news"`，`expected_interval_seconds = 24*60*60`，
      `tolerance_factor = 1.5`——**與索引排程分開登記**，理由同
      `consultation/scheduler.py` 的註解：合併會讓其中一支停擺被另一支的心跳掩蓋
- [ ] 8.2 `async def _tick(self)` 的選材順序：
      1. 取當日所有有 active medication 的 `user_id`
      2. 對每位使用者：`list_active_by_user()` → 藥名集合 →
         `DrugNewsRepository.find_by_drug_keys()` → 濾掉
         `list_pushed_refs()` 已推過的 → 取 `concern_kind` 優先序
         （`recall` > `safety` > `supply` > `education`）最高、再取 `published_at` 最新的一則
         → **Tier 1**
      3. 沒有 Tier 1 命中時，取 `kb_digest.recent_articles()` 第一筆未推過的 → **Tier 2**
      4. `MedicalNewsDeliveryRepository.claim()` 成功才推——這是多實例下的搶佔
      5. 推完即 `return`：**每位使用者每日至多一則**（design 決策 8）
- [ ] 8.3 `_resolve_display_prefs(user_id)` 逐字沿用 `medication_scheduler.py:280` 的做法
      （背景工作沒有 request context，每則推播各自解析語言與字級）
- [ ] 8.4 推播失敗 SHALL NOT 重試、SHALL NOT 補推。已 `claim` 的紀錄不回滾——
      延遲後的消息卡已失去時效意義，補推只是騷擾（比照用藥提醒的 misfire grace）
- [ ] 8.5 `def start_medical_news_push_scheduler(*, enabled=True, replier, ...)`，
      簽章與回傳形狀比照 `start_medication_scheduler`
- [ ] 8.6 測試 `tests/unit/services/medical_news/test_push_scheduler.py`
      - `test_tier1_preferred_over_tier2`
      - `test_falls_back_to_tier2_when_no_drug_news`
      - `test_user_without_medications_still_gets_tier2`
      - `test_at_most_one_card_per_user_per_day`：某使用者三種藥皆有命中 → `push_flex`
        只被呼叫一次
      - `test_claim_failure_skips_push`：`claim` 回 False → 不推（模擬另一實例已搶到）
      - `test_concern_kind_priority_order`：`recall` 勝過同日的 `education`
      - `test_push_failure_is_not_retried`
      - `test_heartbeat_registered_separately_from_index_scheduler`
- [ ] 8.7 commit：`feat(medical-news): 每日推播排程器`

## 9. 認同分享

- [ ] 9.1 `app/services/medical_news/share_service.py`：`class MedicalNewsShareService`，
      建構子 `(*, replier, family_tree_service, user_profile_service, daily_share_limit: int)`
- [ ] 9.2 `async def share(self, *, sharer_id: str, news_ref: str, reply_token: str,
      language: str, font_size: str) -> None`：
      1. `count_shares_today()` 已達上限 → 回 `t("news.share_limit_reached")`，不送
      2. 取 `family_tree_service.get_family_tree(sharer_id)` 的 `family_members`。
         **SHALL NOT 呼叫 `FamilyAuthorizationService.notification_recipients()`**
         ——那張表答的是「他出事時通知誰」，與主動分享是不同的信任（design 決策 7）
      3. 族譜為空 → 回 `t("news.no_family")`，不送
      4. 逐位收件人 `MedicalNewsShareRepository.claim()`，成功者才 `push_flex`
         `build_shared_news_flex()`；push 失敗只記 log
      5. `mark_shared()`，回 `t("news.shared_ok")` 帶實際成功筆數
- [ ] 9.3 分享卡的內容 SHALL 只來自 `DrugNews.title`／`summary`／`source_name`／`url`
      或 `KbArticle` 的對應欄位。**SHALL NOT 帶入 `drug_key`、藥名或分享者的任何用藥狀態**
      ——這是零洩漏的承重條件（design 決策 6）
- [ ] 9.4 `app/services/line_messaging/dispatcher/dispatcher.py::_dispatch_postback`
      新增 `elif action == "share_medical_news":` 分支，位置緊接 `already_done` 之後，
      形狀比照既有分支：取 `news_ref = params.get("news_ref", [""])[0]`，
      空值時 `logger.warning` 並 return
- [ ] 9.5 測試 `tests/unit/services/medical_news/test_share_service.py`
      - `test_shared_card_payload_excludes_drug_name`：Tier 1 的 `DrugNews` 有 `drug_key`，
        斷言傳給 builder 的 kwargs 不含它
      - `test_does_not_call_notification_recipients`：注入的 fake authorization service
        的 `notification_recipients` 未被呼叫——鎖住 design 決策 7
      - `test_empty_family_replies_with_guidance`
      - `test_duplicate_recipient_claim_prevents_second_send`：兩位家人先後分享同一則給
        同一位收件人 → 該收件人只收到一次
      - `test_daily_limit_blocks_further_shares`
      - `test_push_failure_for_one_recipient_does_not_abort_others`
- [ ] 9.6 測試 `tests/unit/services/line_messaging/test_dispatcher.py` 追加
      - `test_share_medical_news_postback_routes_to_share_service`
      - `test_share_postback_without_news_ref_is_ignored`
- [ ] 9.7 commit：`feat(medical-news): 認同分享與 postback 接線`

## 10. 設定與組裝

- [ ] 10.1 `app/core/config.py` 新增（形狀比照既有的 `DRUG_CATALOG_*`）：
      - `MEDICAL_NEWS_ENABLED`（預設 `"true"`）
      - `MEDICAL_NEWS_INDEX_TIME`（預設 `"03:00"`）
      - `MEDICAL_NEWS_PUSH_TIME`（預設 `"09:00"`）
      - `MEDICAL_NEWS_MAX_AGE_DAYS`（預設 `"30"`）
      - `MEDICAL_NEWS_SEARCH_LIMIT`（預設 `"5"`）
      - `MEDICAL_NEWS_DAILY_SHARE_LIMIT`（預設 `"5"`）
- [ ] 10.2 `app/dependencies.py`：建立三個 service 的單例並提供
      `get_drug_news_index_service()`、`get_kb_digest_service()`、
      `get_medical_news_share_service()`，形狀比照 `get_medication_service()`。
      **唯一組裝點仍是 `dependencies.py`**（`specs/backend-architecture`），
      service 內部不得自行 import 單例
- [ ] 10.3 `app/main.py` lifespan 的 `if run_schedulers:` 區塊內啟動兩支新排程器，
      並在 `finally` 一併 `stop()`。放在同一個 `run_schedulers` 判斷內，理由與既有
      註解相同：排程器只在扮演 scheduler 角色的行程啟動
- [ ] 10.4 `LineMessageDispatcher` 建構子注入 `medical_news_share_service`，
      預設 `None`（未設定時該 postback 分支只記 log，與 `_medication_service` 為 None
      時的既有處理一致）
- [ ] 10.5 啟動時呼叫 `medical_news_repository.ensure_indexes()`（模組層函式，見 1.3），位置比照既有的
      `MedicationLogRepository.ensure_indexes()`
- [ ] 10.6 測試 `tests/unit/test_dependencies.py` 追加
      - `test_medical_news_services_are_singletons`
      - `test_dispatcher_tolerates_missing_share_service`
- [ ] 10.7 commit：`feat(medical-news): 設定、組裝與排程啟動`

## 11. Spec delta

- [ ] 11.1 `openspec/changes/medical-news-push/specs/medical-news-push/spec.md`，
      條文涵蓋：兩層選材與版面必須可分辨、每人每日至多一則、來源限定官方域、
      無 url 的來源不得產生消息卡、判定失敗 fail closed（**條文須明載與 `rag-crag`
      刻意相反的理由**）、輸出防線兩層且命中即丟棄不改寫、摘要中性第三人稱、
      分享零洩漏、分享收件人不走 `NOTIFICATION_POLICY`、去重與搶佔共用唯一索引、
      `indication` 三個欄位不得進入推播
- [ ] 11.2 每條 Requirement 至少一個 Scenario，格式比照
      `openspec/specs/medication-reminders/spec.md`
- [ ] 11.3 commit：`docs(openspec): medical-news-push spec delta`

## 12. 驗證與收尾

- [ ] 12.1 `./init.sh` 全綠（所有 pytest 通過）
- [ ] 12.2 到 LINE Official Account Manager 的用量頁面確認本專案目前的方案與實際
      月用量（方案額度本身已查證，見 design 證據缺口 1 的表）。中用量以下不可加購，
      額度耗盡時用藥提醒會靜默失敗——**若逼近上限，正解是升高用量方案，不是把本功能
      降為每週一則**（用少發提醒省訊息費是省錯地方）
- [ ] 12.3 真機確認三種字級下 Tier 1／Tier 2 兩張卡片外觀確有可分辨的差異
      （design Open Question 1）。分不出來時 SHALL 回報並重新評估是否維持每日必推
- [ ] 12.4 真機確認分享卡不含任何藥名，且來源按鈕可開啟瀏覽器
- [ ] 12.5 記錄首兩週的 `IndexRunResult` 與 Tier 1／Tier 2 推播比例，回填 design.md
      的證據缺口 3（Tier 1 實際命中率）
- [ ] 12.6 清楚的 git commit 與 PR
- [ ] 12.7 合併後 `openspec archive medical-news-push`
