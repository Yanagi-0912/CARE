## Context

`rag-eval-metrics` change 補上多正解標籤（`expected_title_substrings` 等）後，Cohere 精排目前在 eval 上勝出：

| 指標 | RRF 混合排序 | Cohere 精排 |
| --- | --- | --- |
| `hit_rate@5` | 0.818 | 0.864 |
| `mean_mrr` | 0.558 | 0.583 |
| `mean_ndcg@5` | 0.613 | 0.644 |

這份數字說明 Cohere 精排「答對了沒」的能力已經沒問題。但把 Cohere top-5 的候選攤開來看（實測跑 `--compare-rerank` 並印出 `retrieved_titles`），kb-013「如何預防中風？」的 5 個席位只來自 2 篇文章——「3大關鍵行動」佔 2 席、「中風8大危險因子」佔 3 席。這不是「答錯了」（golden set 判準是 substring 命中，這題仍然命中），而是**進 prompt 的 context 來源多樣性不足**：模型只看得到 2 篇文章的角度，`_append_sources` 最多列 3 筆來源的設計也因此形同虛設。

**誠實的動機聲明**：這個問題是 eval 聚合指標量不到的——golden set 只問「命中了沒」，不問「來源夠不夠多元」。本 change 的動機是答案品質（context 來源多樣性），**不是**要拉高 hit_rate / mrr / ndcg@5 這些數字；去重前後這些指標預期只有雜訊等級的變化，方向也不保證變好。若去重後 eval 數字持平甚至略降，那是預期內的結果，不代表 change 失敗。

## Goals / Non-Goals

**Goals：**

- 讓進生成 prompt 的 5 個 chunk 盡量分散在不同文章上，改善答案的來源多樣性與可引用性
- 不改變精排本身的相關性判斷邏輯（不重新訓練、不換 reranker、不調 Cohere 參數）
- 文章身分判定與既有「同一來源」判斷（`_append_sources` 用的 `_source_key`）保持一致，不要有兩套互相打架的「什麼算同一篇文章」定義

**Non-Goals：**

- 不承諾、不宣稱去重會提升 `hit_rate@5` / `mean_mrr` / `mean_ndcg@5`（見上方誠實聲明與下方驗證結果）
- 不改變 `RAG_RETRIEVE_CANDIDATES`（wide retrieve 40 筆）、`RAG_RERANK_TOP_N`（最終進 prompt 5 筆）、`CITE_TOP_K`（最多列 3 筆來源）等既有數量參數
- 不改變 `rank_mode == "none"` 的裸檢索觀測路徑（`scripts/rag_eval.py`）——production 永遠有 reranker，這個模式本來就是刻意繞過 reranker 觀察原始檢索順序，去重是「精排*後*」的處理，套用在裸檢索順序上沒有意義

## Decisions

### D1. 為何在完整排序上去重，而不是在 top-5 上去重

`_retrieve_and_rerank` 呼叫 reranker 時，`top_n` 從固定的 `RERANK_TOP_N=5` 改成 `len(docs)`（即 wide retrieve 撈回的全部候選數，預設 40）。如果只在被截斷後的 top-5 上做去重，去重函式根本看不到「這篇文章原本有沒有排名第 6、第 7 但因為前面已經有 2 個 chunk 而該退場」的候選——換句話說，只有看過完整排序，去重才能真正「把被同一篇文章擠掉的名額還給其他文章」，而不是在一個已經被擠壓過的候選池裡打轉。

### D2. 為何預設 `RAG_RERANK_MAX_CHUNKS_PER_ARTICLE=2` 而不是 `1`

上游 ETL 以 500 字元硬切、常從句中斷開產生 chunk（見 `docs/care-data-issues.md`）。這種切法下，同一篇文章相鄰的兩個 chunk 經常是「上半段講原因、下半段講怎麼做」這類語意互補、而不是重複的關係——把上限設成 1（每篇文章只留最高分那個 chunk）會系統性地砍掉這種互補內容，讓答案看到的資訊反而更破碎，不是更完整。設成 2 讓「同文章最多兩個互補片段」有機會一起進 prompt，同時仍然防止同一篇文章壟斷 3 個以上席位。這個值透過 `RAG_RERANK_MAX_CHUNKS_PER_ARTICLE` 開放調整，未來若有更細緻的「相鄰 chunk 合併」機制，可以再重新評估。

### D3. 文章身分判定：重用 `RagAnswerService._source_key`，不重新發明

`_source_key`（有 url 用 url，無 url 用 `source_name+original_title`）已經是 `_append_sources` 判斷「同一來源」的既有邏輯。`dedup_ranked_docs` 直接呼叫這個 staticmethod，而不是另外寫一套「什麼算同一篇文章」的規則。好處：兩處判斷同一件事時用的是同一份程式碼，不會出現「精排去重覺得是同一篇，但來源清單覺得是不同篇」這種不一致。

### D4. 成本：`top_n` 改成 40 不增加 Cohere 費用

Cohere Rerank API 的計價單位是 **search unit**：1 個 search unit = 1 次 query 對最多 100 份文件的排序，與回傳結果要幾筆（`top_n`）無關（[Cohere pricing](https://cohere.com/pricing) 對 rerank 的計費說明）。本 change 呼叫 `reranker.rerank(query, docs, top_n=len(docs))`，`docs` 就是原本已經送進 Cohere 的 40 筆 wide retrieve 候選——**送進 API 的文件數沒有變，只有回傳結果要幾筆這個參數變了**。因此把 `top_n` 從 5 改成 40（即 `len(docs)`）不會讓一次請求變成兩個 search unit，也不會增加呼叫次數，成本不變。

## Task：dedup 前後 `--compare-rerank` 對照

實跑指令：`python scripts/rag_eval.py --compare-rerank --top-n 5 --out /tmp/rag-compare-dedup.json`（2026-08-09，已套用本 change 全部程式碼變更，`RAG_RERANK_MAX_CHUNKS_PER_ARTICLE=2` 為預設值，未額外設環境變數；golden set 38 題、22 題有檢索期望被計分）。

| 指標 | RRF（去重前，既有基準） | RRF（去重後，本次實跑） | Cohere（去重前，既有基準） | Cohere（去重後，本次實跑，**見下方限速警告**） |
| --- | --- | --- | --- | --- |
| `hit_rate@5` | 0.818 | 0.818（18/22，逐字相同） | 0.864 | 0.909（20/22） |
| `mean_mrr` | 0.558 | 0.558（逐字相同） | 0.583 | 0.647 |
| `mean_ndcg@5` | 0.613 | 0.610（−0.003） | 0.644 | 0.701 |

**RRF 分支**（`VectorScoreReranker`，本地排序、不打 API，數字可信）：去重前後 `hit_rate` 與 `mean_mrr` 逐字相同，`mean_ndcg@5` 微幅下降 0.003（38 題中屬雜訊等級）。這與 Goals/Non-Goals 的預期一致——去重不承諾、也不應該讓聚合指標變好，這裡量到的是「持平、雜訊等級變化」，誠實記錄。

**Cohere 分支：必須先報告一個限速汙染問題，這批數字不能直接當作「dedup 讓 Cohere 變好」的證據。** 本次實跑時 Cohere API 大量回傳 `429 Too Many Requests`（trial key 速率限制），`CohereReranker` 遇到 429 會依既有降級邏輯靜默 fallback 成 `VectorScoreReranker`（見 `app/services/rag/cohere_reranker.py` 既有行為，非本 change 新增）。逐案比對 cohere 分支與 vector 分支的 `retrieved_urls`：22 個計分案例中有 **9 個**（`kb-007`, `kb-008`, `kb-023`, `kb-024`, `kb-025`, `kb-026`, `kb-029`, `kb-030`, `kb-032`，約 41%）兩分支結果逐字相同——這是 fallback 的強訊號（相同的 wide-retrieve 候選、相同的排序演算法、相同的去重與截斷，若不是走同一份程式碼路徑，兩個分支不會剛好逐字一致）。也就是說，本次「去重後」的 Cohere 數字實際上是「約 59% 真 Cohere ＋約 41% 悄悄變成 RRF」的混合結果，而 RRF 分支本身在這份 golden set 上表現已經不錯（`hit_rate@5=0.818`）——這會把 Cohere 分支的數字往 RRF 的方向拉高，`hit_rate@5` 從 0.864 看似漲到 0.909 很可能主要是這個混合效應，而不是「去重讓 Cohere 排序變準」。**依限速重試風險（trial key 常見上限約 10 req/min，重試極可能再次撞到同一限制），本次不再重跑求乾淨數字**；如需乾淨對照，需要付費 tier 的 Cohere key 或在呼叫間加節流延遲後重跑。

**結論：聚合指標這次量到的東西不足以支持或反駁「dedup 影響 eval 分數」的任何方向性宣稱**——RRF 分支（無 API 汙染）持平在雜訊範圍內，符合 Non-Goals 的預期；Cohere 分支的數字因限速汙染不可信，不採用。真正乾淨、可信的驗證是下面的 kb-013 質化對照。

### kb-013 相異文章數對照（本 change 的主要驗證，不是聚合指標）

kb-013（「如何預防中風？」）的 cohere 分支**未**出現在上述疑似 fallback 名單中——它的 `retrieved_urls` 與 vector 分支不同，且呈現「同一 url 相鄰出現兩次」的 pattern（dedup 生效的特徵；若是 fallback 成 vector 排序，理當跟 vector 分支一樣 5 個 url 互不相同），可信是真實 Cohere 排序結果。

**去重前**（`design.md` Context 一節引用的既有實測，即本 change 動機的原始案例）：Cohere top-5 只涵蓋 **2 篇文章**——「3大關鍵行動 預防中風 守護腦健康」佔 2 席、「腦中風8大危險因子」系列佔 3 席。

**去重後**（本次實跑，`/tmp/rag-compare-dedup.json` 的 cohere 分支，`retrieved_urls` 對應 `retrieved_titles`）：

| 名次 | 標題 | 文章（url） |
| --- | --- | --- |
| 1 | 3大關鍵行動 預防中風 守護腦健康 | pid=19537 |
| 2 | 3大關鍵行動 預防中風 守護腦健康 | pid=19537 |
| 3 | 中風8大危險因子 掌握6招預防 | pid=18504 |
| 4 | 中風8大危險因子 掌握6招預防 | pid=18504 |
| 5 | 遠離腦中風　自我監控揪出危險因子 8大危險因子符合其中3項屬高危險群 | pid=17255 |

top-5 現在涵蓋 **3 篇文章**（pid=19537 ×2、pid=18504 ×2、pid=17255 ×1），沒有任何一篇超過 `max_per_article=2` 的上限——與 design 目標（同文章最多 2 席、把名額還給其他文章）一致。**相異文章數：2 → 3。** 沒有變成 5（wide retrieve candidates 裡這個 query 底下可能沒有 5 篇同樣高度相關的獨立文章；「衛福部闢謠網站」在腦中風主題下本身就有多篇高度重疊的近似文章），但確實把原本被單一文章（3 席）壟斷的名額釋出 1 席給第三篇文章，且無文章超過上限，符合預期行為。
