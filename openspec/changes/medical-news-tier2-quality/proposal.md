# Tier 2 選材的內容品質與個人化

## Why

`medical-news-push` 的 Tier 2 保底路徑上線後，實地查了一次它實際會推出去的內容
（2026-09-04，直接對 `CARE_database.health_articles_chunks` 量測），結果與 design
的假設不符。

`kb_digest_service` 的模組註解寫著「這批語料本來就是為長輩寫的衛教與闢謠文章，
正好是 Tier 2 要的東西」。對台灣事實查核中心與食藥署闢謠專區成立，對**衛福部
闢謠網站那批不成立**——它名為闢謠，實際混了大量政策新聞稿。而它的發稿頻率最高，
因此佔滿選材窗口的多數。

模擬當日 `recent_articles(today, limit=10)` 會挑出的池子，10 篇裡有 4 篇的目標
讀者根本不是長輩：

```
[09-03] 衛福部  「孩」要更健康！國健署「健康護盤」行動 翻轉兒童肥胖   ← 兒童
[09-02] 衛福部  國健署攜手軍醫局 打造無菸健康戰力 國軍弟兄…          ← 國軍
[09-01] 衛福部  戒菸專線青年大使出動！「年輕人影響年輕人」…          ← 年輕人
[08-27] 衛福部  試管嬰兒補助再加碼 支持不孕夫妻圓生育夢              ← 育齡夫妻
```

這正是 spec「每日一則與兩層選材」所描述的稀釋失效模式，而且沒有任何訊號——不報錯、
不留 log，只表現為使用者不再點卡片。也是 design.md 證據缺口 3「整個功能退化成
Tier 2 電子報」的具體形態：不只退化成電子報，還是一份內容不對的電子報。

同一次查證另外發現兩件事：

1. **Tier 2 完全沒有相關性把關。** Tier 1 有 `GeminiNewsGrader.judge()` 加
   `relevance` 的三道過濾；Tier 2 的 `_pick_tier2` 只做「這位使用者收過沒」。
2. **沒有推播歷史的使用者會全部拿到同一篇。** 池子是全體共用的，去重條件對新
   使用者一律成立，於是所有人命中 `pool[0]`。功能上線那幾天所有長輩收到同一張卡。

## What Changes

**修改能力 `medical-news-push` 的 Tier 2 選材**，新增兩道內容過濾與一道個人化：

1. `relevance.is_policy_announcement`（新增純函式）——標題黑名單，不花額度。比照
   CARE-data 對「食藥署公告」的 `ADMIN_NOISE_KEYWORDS`，那次在來源端擋掉了，這批
   沒有。量測：最新 300 篇擋下 37 篇，全部來自衛福部，逐篇檢視無誤擋。
2. `KbArticleGrader` / `GeminiKbArticleGrader`（新增模組
   `app/services/medical_news/article_grader.py`）——LLM 判「這篇對高齡讀者有沒有
   用」，擋黑名單擋不掉的那種（標題像衛教、內容也是真衛教，但對象不是長輩）。
   形狀比照既有的 `grader.py`：SCHEMA 常數、Protocol、`invoke_*` 注入點。
3. `MedicalNewsPushScheduler._pick_tier2` 依 `user_id` 的 `blake2b` 錯開池子起點。

**訂正兩處未經查證的註解**（不改行為）：`kb_digest_service.recent_articles` 與
`relevance._ROC_YEAR_OFFSET` 都寫著「這批語料同時存在西元與民國兩種格式」「衛福部
頁面常見民國」。實際量測 `chunk_index=1` 且有網址的 2,422 筆，**民國格式 0 筆**。

**新增設定**：`MEDICAL_NEWS_TIER2_GRADER_ENABLED`（預設 true）、
`MEDICAL_NEWS_TIER2_GRADE_MAX_CALLS`（預設 30）。

**不修改**：Tier 1 的索引與選材、RAG 檢索（同一批語料在檢索路徑上不套用這些過濾，
理由見 design 決策 2）、CARE-data 的 ETL、卡片版面、分享路徑。

## Impact

**API/route**：不新增、不修改任何對外 route。改動全在每日推播的選材路徑上。

**額度**：新增的 LLM 呼叫是 O(每日候選數)，上限 `MEDICAL_NEWS_TIER2_GRADE_MAX_CALLS`
＝每天 30 次，**與使用者人數無關**（池子全體共用、選材一天只跑一次）。撞到配額時
把 `MEDICAL_NEWS_TIER2_GRADER_ENABLED` 設 false 即可，Tier 2 仍照常供應。

**測試計畫**：
- `tests/unit/services/medical_news/test_relevance.py`：政策新聞稿判定，正反例皆取自
  真實標題
- `tests/unit/services/medical_news/test_article_grader.py`：輸出解析與 fail closed
- `tests/unit/services/medical_news/test_kb_digest_service.py`：兩道過濾的順序、額度
  用完的行為、grader 缺席時的降級
- `tests/unit/services/medical_news/test_push_scheduler.py`：偏移的穩定性與去重優先
