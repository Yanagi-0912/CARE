# Hybrid Retrieval 驗證手冊

分支：`feat/hybrid-retrieval-rrf`（commit `ae71dc9`，從 `main` 開）
狀態：**程式完成、單元測試全綠、尚未 push、尚未對真實知識庫驗證**

這份文件是給「換一台電腦、有 Atlas 權限」的人照著跑，跑完才決定要不要 merge。

---

## TL;DR

現有檢索只有 `$vectorSearch`（比對語意）。醫療查詢裡的藥名、劑量、疾病名是**罕見精確詞**，稠密向量對這類 token 是弱項，而 BM25 正好相反。這個分支加上 BM25 檢索並以 RRF 融合兩份排名。

**預設關閉。** 要驗證才需要開。

---

## 一、為什麼要做

使用者問：

> 乙醯胺酚一天最多吃幾顆

純向量可能撈回一堆「有關但答不了」的文件（〈止痛藥使用注意事項〉〈普拿疼常見問題〉），而真正回答問題的〈乙醯胺酚劑量上限與肝損傷風險〉**完全不進候選池** —— 那篇大半是劑量表格，整體語意向量不像一個「關於吃藥的問句」。

關鍵在於 **reranker 救不回沒進池子的東西**。它只能重排你交給它的候選集。所以 hybrid 的職責是「把對的文件弄進池子」，reranker 的職責是「把它拉到前面」。

BM25 對同一個查詢會直接命中，因為「乙醯胺酚」在語料庫裡罕見 → 判定為強訊號。

### 為什麼用 RRF 而不是把分數相加

- 向量的 cosine 相似度落在 `0~1`
- BM25 分數**沒有上界**，而且與整個語料庫的統計有關

兩者尺度不可比，相加沒有意義。RRF 只取名次：

```
score(doc) = Σ  1 / (k + rank_i(doc))          k 預設 60
```

---

## 二、程式改了什麼

| 檔案 | 內容 |
|---|---|
| `app/services/rag/rank_fusion.py` | **新增**。`reciprocal_rank_fusion()` 純函式 |
| `app/services/rag/retriever.py` | **新增** `MongoAtlasTextRetriever`（`$search`）與 `HybridRetriever` |
| `app/services/rag/__init__.py` | 匯出新類別 |
| `app/core/config.py` | `RAG_HYBRID_ENABLED`、`MONGODB_TEXT_INDEX`、`RAG_RRF_K` |
| `app/dependencies.py` | 依開關注入 hybrid 或純向量 |
| `resources/atlas_text_search_index.json` | Atlas Search 索引定義 |
| `tests/unit/services/rag/test_rank_fusion.py` | **新增** 17 個案例 |
| `tests/unit/services/rag/test_retriever.py` | 追加 15 個案例 |

**下游完全沒改。** 三個 retriever 共用同一個 `ainvoke(query) -> list[Document]` 介面，所以 `RagAnswerService`、rerank、CRAG、citation 都不知道差別。

### 三層安全機制

1. `RAG_HYBRID_ENABLED` 預設 `false`
2. 開了但 `MONGODB_TEXT_INDEX` 未設 → 記 warning、維持純向量
3. `$search` 報錯（例如索引還沒建） → fail-open 降級為純向量

也就是說**先部署再建索引是安全的**，中間期間行為等同現狀。

---

## 三、回家要跑的步驟

### Step 0：切到分支

```bash
cd CARE
git checkout feat/hybrid-retrieval-rrf
```

若這台機器還沒有這條分支，它只存在於前一台電腦的本地 —— 需要先從那台 push，或依本文件重做。

### Step 1：建 Atlas Search 索引

Atlas UI → 左側 **Search & Vector Search** → **Create Search Index**

- 類型選 **Atlas Search**（**不是** Vector Search，這是兩個不同的索引）
- 選 **JSON Editor**
- 貼上 `resources/atlas_text_search_index.json` 裡的 `mappings` 區塊
- 索引名稱建議 `care_text_index`
- 選對 database / collection（與 `MONGODB_COLLECTION` 相同）

> **最容易踩的坑**：analyzer 必須是 `lucene.cjk`。
> 中文沒有空白分詞，預設英文 analyzer 會把整句中文當成單一 token，
> BM25 會**靜默失效** —— 看起來有跑、不報錯，但撈不到東西。
> 索引定義檔裡已經設好了，照貼即可。

建完等狀態變成 **Active** 再繼續（幾分鐘）。

### Step 2：填 `.env`

```bash
# 從 Atlas: Connect → Drivers 取得連線字串
MONGODB_URI=mongodb+srv://<user>:<password>@carecluster.ej6ii9w.mongodb.net/?appName=CARECluster
MONGODB_DB=<從 Data Explorer 看>
MONGODB_COLLECTION=<從 Data Explorer 看>
MONGODB_VECTOR_INDEX=<從 Search & Vector Search 看>
MONGODB_VECTOR_DIM=3072

# Step 1 建的索引名
MONGODB_TEXT_INDEX=care_text_index

# 先保持關閉，Step 3 要量基準線
RAG_HYBRID_ENABLED=false
RAG_RRF_K=60
```

另外確認 `GEMINI_API_KEY`、`COHERE_API_KEY` 有值。

**密碼在 Atlas 看不到**（只存雜湊）。建議另開一個唯讀使用者專供 eval 用：
Database & Network Access → Add New Database User → 權限選 **Only read any database**。
這樣不必動到正式那組帳密（正式那組存在 CARE-infra 的 GitHub Actions secret，只能寫不能讀；
唯一能取回原值的地方是 `kubectl get secret care-backend-secret -o jsonpath='{.data.MONGODB_URI}' | base64 -d`）。

還要把這台機器的 IP 加進 **Network Access** 白名單，否則會 timeout（錯誤訊息容易被誤判成帳密錯）。

### Step 3：量基準線（hybrid 關閉）

```bash
# 先確認單元測試還是綠的
python -m pytest tests/ -q

# 純向量的 retrieval hit_rate
python scripts/rag_eval.py --rank-mode cohere --top-n 5 --out /tmp/baseline.json
```

把印出來的 `hit_rate` 記下來。`--rank-mode cohere --top-n 5` 是與線上口徑一致的設定。

題庫是 `evals/rag/golden.jsonl`，共 38 題（kb 34 題：train 28 / holdout 6；refuse 3；web 1）。
只有 kb 且有期望 substring 的題會計分，其餘 skip。

### Step 4：開 hybrid 再跑一次

```bash
RAG_HYBRID_ENABLED=true python scripts/rag_eval.py --rank-mode cohere --top-n 5 --out /tmp/hybrid.json
```

也建議看一下未精排的原始召回（hybrid 的直接效果在這裡最明顯）：

```bash
python scripts/rag_eval.py --out /tmp/baseline-wide.json
RAG_HYBRID_ENABLED=true python scripts/rag_eval.py --out /tmp/hybrid-wide.json
```

### Step 5：比較並決定

```bash
python - <<'PY'
import json
for name, path in [("baseline", "/tmp/baseline.json"), ("hybrid", "/tmp/hybrid.json")]:
    d = json.load(open(path, encoding="utf-8"))
    print(name, "hit_rate=", d.get("hit_rate"), "scored=", d.get("scored_cases"))
    print("  miss:", d.get("miss_ids"))
PY
```

**判斷標準：**

| 結果 | 動作 |
|---|---|
| `hit_rate` 上升、`miss_ids` 變少 | 值得 merge |
| 幾乎沒變 | **不要 merge**。多一次 Atlas 查詢的延遲不值得 |
| 下降 | 不要 merge。先確認 analyzer 是不是漏了 `lucene.cjk` |

比 `hit_rate` 更值得看的是 **`miss_ids` 的變化** —— 哪幾題被修好了、有沒有原本會的反而壞掉。
34 題的樣本很小，單一題的進出就會讓 `hit_rate` 跳 3 個百分點，所以要看具體是哪些題。

**這一步不能跳過。** 「業界都在做」不是理由 —— 2026 的產線報告（arXiv 2603.02153）指出
retrieval fusion 的召回增益常在 rerank 與截斷後被抵銷，有些配置下 Hit@10 反而從 0.51 掉到 0.48。
你們已經有 Cohere reranker，正好落在「融合幫助最小」的配置。所以要看自己的數字。

---

## 四、已知的坑

### Cohere 出現 SSL 憑證錯誤

```
ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in certificate chain
```

這是公司網路的 TLS 攔截，不是金鑰問題（在公司機器上重現過）。家用網路應該不會有。真遇到：

```bash
pip install truststore
```

然後在腳本最前面加：

```python
import truststore
truststore.inject_into_ssl()
```

**不要**改成關閉憑證驗證。

### 沒有用 MongoDB 的 `$rankFusion`

`$rankFusion` 實務上需要 **8.1+**、目前仍是 **Preview**、且免費層不可用。
叢集是 **8.0.28**，所以 RRF 是在**應用層**用 Python 做的。好處是與 MongoDB 版本無關、可單元測試。

### `.env.example` 的佔位值會讓測試爆掉

`REDIS_URL=redis://default:your_password@your_host:your_port` 裡的 `your_port` 無法轉成整數，
會讓 12 個測試檔連 collect 都失敗（`ValueError: Port could not be cast to integer`）。
本機測試把它設成 `redis://localhost:6379/0` 即可（不需要真的連得上）。

### `metadata["score"]` 會被覆寫

融合後 `score` 是 RRF 分數，不是原本的 cosine 或 BM25 分數。
這是**刻意的** —— 下游 `VectorScoreReranker` 照 `score` 排序，若留著尺度不可比的原始分數，
一個 BM25 的 `8.2` 會壓過 cosine 的 `0.9`。原始分數保留在 `vector_score` / `text_score`。

---

## 五、驗證通過之後

```bash
git push -u origin feat/hybrid-retrieval-rrf
gh pr create --base main --title "feat(rag): 向量 + BM25 hybrid retrieval，以 RRF 融合"
```

把 Step 5 的前後數字貼進 PR 描述。

**上線注意**：merge 到 `main` 會觸發 `trigger-deploy.yml` 的正式部署。
建議先以 `RAG_HYBRID_ENABLED=false` 合併上線（等同現狀、零風險），
確認服務正常後再單獨改環境變數打開。這樣開關與程式部署分離，出問題可以只關開關不用回滾。

正式環境的環境變數在 CARE-infra 的 GitHub Actions secret / `care-backend-secret`。

---

## 附錄：這條分支之外還沒做完的事

| 項目 | 狀態 |
|---|---|
| Rich Menu 指向 `/medications` | 程式在 `jamesbranch`（CARE），**未開 PR**。要重跑 `scripts/setup_rich_menu.py` 才會在 LINE 上生效 |
| `setup_rich_menu.py` 清理舊選單 | 同上，在 `jamesbranch` |
| 用藥提醒 LIFF 前端 | 在 CARE-LIFF 的 `jamesbranch`，**未開 PR** |
| 多輪對話的指代消解 | **未做**。`get_rag_answer` 只吃最新那句原文，「那它的副作用呢？」這種問法會拿沒有指涉對象的片段去檢索 |
| CRAG 的 per-chunk 過濾 | **未做**。目前 grader 對整批候選回一個等級，不會剔除個別 chunk |
| chunk metadata 太薄 | 只有 `url` / `source_name` / `ingested_at`，缺 `section_path`、parser 版本、confidence |
| 跨語言檢索 | **未量測**。支援六語但知識庫是中文，越南文／泰文問句的檢索品質不明 |
| 貼在對話裡的 API 金鑰 | **建議 rotate**（Gemini、Cohere 各一把） |
