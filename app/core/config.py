import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# 唯一會關掉背景排程器的角色。判斷寫成「只有明確設成 api 才不跑」，而不是
# 「只有 all／scheduler 才跑」——兩者對合法值的結果相同，但對打錯字的結果
# 相反，而這個方向的錯誤代價不對稱：
#   多跑一份排程器 → 既有的原子搶佔多擋一次（那個機制本來就要應付滾動更新
#                    期間必然出現的雙實例），使用者無感。
#   一份都沒跑     → 當下所有服藥時段永久錯過，而 medication-reminders 明訂
#                    「錯過時段不補推播」，沒有任何補救路徑。
# 所以未知值一律當成「要跑」。
_SCHEDULER_OPT_OUT_ROLE = "api"


def should_run_schedulers(role: str) -> bool:
    """這個角色該不該啟動背景排程器。

    抽成純函式而非寫死在 lifespan 裡，是為了讓這個判斷能被窮舉測試——
    lifespan 本身要建資料庫索引並組裝多個服務，在單元測試裡跑不動。
    """
    return role.strip().lower() != _SCHEDULER_OPT_OUT_ROLE

class Settings:
    # 這個行程扮演的角色，決定 lifespan 要不要啟動背景排程器。
    #   all       —— API + 排程器都跑（預設，與拆分前完全相同的行為）
    #   api       —— 只服務請求，不啟動任何排程器
    #   scheduler —— 只啟動排程器（仍然跑 uvicorn，讓 /health 探針沿用既有設定）
    #
    # 預設刻意是 all 而不是 api：部署設定若還沒更新就先上了新版程式碼，
    # 行為必須與現況一致。反過來預設 api 的話，這種情況下沒有任何行程會跑
    # 排程器，而用藥提醒「錯過時段不補推播」——漏掉就是永久漏掉，沒有補救。
    APP_ROLE: str = os.getenv("APP_ROLE", "all").strip().lower() or "all"

    # Gemini API 配置
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "gemini-3.8-flash")
    # 主流程三段可以各自換模型，沒填（或 ConfigMap 給空字串）就沿用 MODEL_NAME。
    # 只開這三段：急迫度、藥袋辨識、用藥風險、陪診摘要錯了會直接傷到人，
    # 一律留在 MODEL_NAME，不跟著換。
    AGENT_MODEL_NAME: str = os.getenv("AGENT_MODEL_NAME") or MODEL_NAME
    RAG_GENERATE_MODEL_NAME: str = os.getenv("RAG_GENERATE_MODEL_NAME") or MODEL_NAME
    WEB_GENERATE_MODEL_NAME: str = os.getenv("WEB_GENERATE_MODEL_NAME") or MODEL_NAME

    # Line Messaging API 配置
    LINE_CHANNEL_ID: str = os.getenv("LINE_CHANNEL_ID")
    LINE_CHANNEL_SECRET: str = os.getenv("LINE_CHANNEL_SECRET")
    # 每一次 LINE Messaging API 呼叫（reply／push／loading）的逾時秒數。
    # SDK 預設是 None＝無上限；呼叫已改到工作執行緒，但無上限仍會讓一個
    # 排程 tick 或一則 webhook 永遠掛著。LINE API 正常在 1 秒內回應，
    # 10 秒足以涵蓋跨區抖動。
    LINE_API_TIMEOUT_SECONDS: float = float(os.getenv("LINE_API_TIMEOUT_SECONDS", "10"))

    # RAG / Embedding 配置
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")

    # 多媒體解析 webhook
    MEDIA_PARSE_WEBHOOK_URL: str = os.getenv("MEDIA_PARSE_WEBHOOK_URL", "")

    # Public app URL used to build LINE-accessible media links.
    # Example: https://example.com or https://xxx.ngrok-free.app
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "")
    TTS_AUDIO_URL_PATH: str = os.getenv("TTS_AUDIO_URL_PATH", "/tts")
    N8N_TTS_WEBHOOK_URL: str = os.getenv("N8N_TTS_WEBHOOK_URL", "")
    N8N_TTS_WEBHOOK_SECRET: str = os.getenv("N8N_TTS_WEBHOOK_SECRET", "")
    N8N_TTS_TIMEOUT_SECONDS: int = int(os.getenv("N8N_TTS_TIMEOUT_SECONDS", "20"))
    TTS_DEFAULT_VOICE: str = os.getenv("TTS_DEFAULT_VOICE", "")
    # 獨立的語音合成服務（CARE-infra 的 care-tts，程式是 app/tts_main.py），例：
    # http://care-tts:8000。有值時 backend 把合成交給它，LINE 也從它下載音檔；空字串時
    # backend 自己合成、自己提供 /tts（本機開發）。
    TTS_SERVICE_URL: str = os.getenv("TTS_SERVICE_URL", "")

    # 台語語音（Taigi AI Labs，見 app/services/speech/taigi_client.py）。金鑰由
    # care-backend-secret 注入；沒設時語言選台語的使用者照舊走 faster-whisper 與
    # edge-tts 國語。
    TAIGI_API_KEY: str = os.getenv("TAIGI_API_KEY", "")
    TAIGI_BASE_URL: str = os.getenv("TAIGI_BASE_URL", "https://learn-language.tokyo")

    # LINE LIFF 配置
    LIFF_CHANNEL_ID: str = os.getenv("LIFF_CHANNEL_ID", "")
    LIFF_URL: str = os.getenv("LIFF_URL", "")
    LIFF_ID: str = os.getenv("LIFF_ID", "")

    # Rich Menu 語系→ID 對照（JSON 字串；未設則讀 resources/rich_menu_ids.json）
    RICH_MENU_IDS_JSON: str = os.getenv("RICH_MENU_IDS_JSON", "")

    # App Auth JWT 配置（LIFF 登入後由後端簽發）
    AUTH_JWT_SECRET: str = os.getenv("AUTH_JWT_SECRET", "dev-only-change-me")
    AUTH_JWT_ALGORITHM: str = os.getenv("AUTH_JWT_ALGORITHM", "HS256")
    AUTH_JWT_EXPIRES_MINUTES: int = int(os.getenv("AUTH_JWT_EXPIRES_MINUTES", "120"))

    # MongoDB Vector Search 配置
    MONGODB_URI: str = os.getenv("MONGODB_URI", "")
    MONGODB_DB: str = os.getenv("MONGODB_DB", "")
    MONGODB_COLLECTION: str = os.getenv("MONGODB_COLLECTION", "")
    MONGODB_VECTOR_INDEX: str = os.getenv("MONGODB_VECTOR_INDEX", "")
    MONGODB_VECTOR_FIELD: str = os.getenv("MONGODB_VECTOR_FIELD", "embedding")
    MONGODB_TEXT_FIELD: str = os.getenv("MONGODB_TEXT_FIELD", "text")
    MONGODB_VECTOR_DIM: int = int(os.getenv("MONGODB_VECTOR_DIM", "0"))
    # Atlas Search index（BM25 用；與 MONGODB_VECTOR_INDEX 是兩個不同的索引）
    MONGODB_TEXT_INDEX: str = os.getenv("MONGODB_TEXT_INDEX", "")

    # PostgreSQL + pgvector：RAG 的向量檢索。
    #
    # 2026-09-19 從 Atlas 搬過來——3072 維向量一筆 42 KB，把免費層 512 MB 撐爆，
    # 寫入被鎖導致後端 ensure_indexes() 失敗、新版 pod 起不來。搬完 Atlas 降到
    # 約 51 MB。內文與 BM25 仍在 Atlas（lucene.cjk 中文分詞在 PG 沒有等價品）。
    #
    # MONGODB_VECTOR_INDEX 保留但已不再被 RAG 主路徑使用：使用者上傳文件與
    # 查核主張比對還走 Atlas $vectorSearch，兩者搬遷另案處理。
    PGVECTOR_DSN: str = os.getenv("PGVECTOR_DSN", "")
    PGVECTOR_TABLE: str = os.getenv("PGVECTOR_TABLE", "health_articles_chunks")
    # halfvec(3072)：pgvector 的 vector 型別索引上限 2000 維，3072 維建不了
    # HNSW；halfvec 上限 4000 維可以。實測無索引暴力掃描 213 ms、HNSW 1.6 ms，
    # 且 top-10 與 Atlas 完全一致。
    PGVECTOR_VECTOR_COLUMN: str = os.getenv("PGVECTOR_VECTOR_COLUMN", "embedding_half")

    # 使用者上傳文件暫存向量庫（與官方 MONGODB_COLLECTION 分離）
    MONGODB_USER_DOCS_COLLECTION: str = os.getenv("MONGODB_USER_DOCS_COLLECTION", "")
    MONGODB_USER_DOCS_VECTOR_INDEX: str = os.getenv(
        "MONGODB_USER_DOCS_VECTOR_INDEX", ""
    )
    USER_DOCS_TTL_SECONDS: int = int(os.getenv("USER_DOCS_TTL_SECONDS", "86400"))

    # Consultation / Redis 配置
    REDIS_URL: str = os.getenv("REDIS_URL", "")

    # Consultation daily summary scheduler（台北時間 HH:MM；容器時區是 UTC 也照台北解讀）
    CONSULTATION_DAILY_SUMMARY_TIME: str = os.getenv(
        "CONSULTATION_DAILY_SUMMARY_TIME", "02:00"
    )

    # Firecrawl（WebSearchService／RAG web fallback）
    FIRECRAWL_API_KEY: str = os.getenv("FIRECRAWL_API_KEY", "")

    # Cohere Rerank（未設定 API key 時降級為向量 score top-n）
    COHERE_API_KEY: str = os.getenv("COHERE_API_KEY", "")
    # TypeSafe Jev：guardrail 升級那一步的分類（services/guardrail/jev.py）。
    # 沒設就直接問 Gemini，行為與導入前相同。
    TYPESAFE_API_KEY: str = os.getenv("TYPESAFE_API_KEY", "")
    COHERE_RERANK_MODEL: str = os.getenv("COHERE_RERANK_MODEL", "rerank-v4.0-pro")
    RAG_RETRIEVE_CANDIDATES: int = int(os.getenv("RAG_RETRIEVE_CANDIDATES", "40"))
    RAG_RERANK_TOP_N: int = int(os.getenv("RAG_RERANK_TOP_N", "5"))
    COHERE_RERANK_TIMEOUT_SECONDS: float = float(
        os.getenv("COHERE_RERANK_TIMEOUT_SECONDS", "5")
    )

    # Hybrid retrieval（向量 + BM25 以 RRF 融合）
    # 預設關閉：需先在 Atlas 建好 MONGODB_TEXT_INDEX（analyzer 用 lucene.cjk）
    RAG_HYBRID_ENABLED: bool = os.getenv("RAG_HYBRID_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    RAG_RRF_K: int = int(os.getenv("RAG_RRF_K", "60"))
    # 每條腿（向量／BM25）的逾時（秒）。0＝不設限。逾時的那條腿當作失敗，用另
    # 一條腿的結果繼續。5 秒是實測正常最慢（0.54 秒）的約 9 倍，來由見
    # rag/retriever.DEFAULT_LEG_TIMEOUT_SECONDS。
    RAG_RETRIEVE_LEG_TIMEOUT_SECONDS: float = float(
        os.getenv("RAG_RETRIEVE_LEG_TIMEOUT_SECONDS", "5")
    )

    # 融合方式：convex（預設，正規化分數的凸組合）或 rrf（Reciprocal Rank Fusion）。
    #
    # 為什麼補上 convex：k=60 是 RRF 原始論文（Cormack et al., SIGIR 2009）
    # 在 TREC 上用的值，本專案從未校準過它。Bruch et al.（TOIS 42(1), 2023；
    # arXiv:2210.11934）量到 RRF 對參數敏感、凸組合在 in-domain 與 out-of-domain
    # 都較好，而且權重「只需少量標註查詢」就能調——這正好對上 golden.jsonl
    # 只有 55 題的現實。
    #
    # 為什麼預設 convex：2026-09-12 golden set 重新稽核後（計分題 17→26）以
    # scripts/rag_fusion_sweep.py 驗證——vector 切面 RRF 22/26 → 25/26、cohere
    # 切面 25/26 → 26/26，兩切面皆零退步，holdout(n=5) 無差異。
    # RAG_TEXT_TITLE_BOOST 的增益接上 Cohere 後就消失了，這次沒有：融合決定的是
    # 「哪 40 筆進得了 Cohere」，kb-026 的正解在 RRF 下排第 42 名，根本沒進候選池。
    #
    # **線上生效的就是這裡的預設值。**正式環境讀的是 CARE-infra helm
    # values.yaml 產生的 ConfigMap（CARE 的 .env 不會進 image），而那裡沒有設
    # 這兩個鍵。要退回 RRF：在 values.yaml 的 backend.config 加
    # RAG_FUSION_MODE: "rrf"。
    RAG_FUSION_MODE: str = os.getenv("RAG_FUSION_MODE", "convex")

    # 凸組合裡向量腿的權重，文字腿拿 1-alpha。只在 RAG_FUSION_MODE=convex 時
    # 生效。0.6 由上述掃描選出：vector 切面平滑單峰（0.3:20 0.5:22 **0.6:25**
    # 0.7:24 0.8+:23），cohere 切面 0.5 與 0.6 並列最高。這是在本專案語料上校準
    # 的值——知識庫大改或換 embedding 模型後要重掃。
    RAG_FUSION_ALPHA: float = float(os.getenv("RAG_FUSION_ALPHA", "0.6"))

    # BM25 也比對文章標題。chunk_content 本身不含標題（切塊時被切掉了），
    # 而 embedding 與 rerank 兩處都會把標題補回文本，只有 BM25 這條腿看不到，
    # 藥名／疾病名只出現在標題時會整篇漏掉。空字串＝關閉，退回只比對內文。
    MONGODB_TEXT_TITLE_FIELD: str = os.getenv(
        "MONGODB_TEXT_TITLE_FIELD", "original_title"
    )
    # 這是「降權」不是「加權」——0.3 是在 golden set 上掃出來的，不是猜的。
    # 標題必須壓得比內文輕，有兩個獨立原因：
    #   1. 標題是文章層級屬性，同一篇的每個 chunk 共用它。標題一命中，該文章
    #      「所有」chunk 一起加分，BM25 的 top-k 會被少數幾篇洗版，候選文章數
    #      驟降（實測 38 篇→25 篇），正確文章反而被自己的鄰居擠出去。
    #   2. 標題是短欄位，BM25 的長度正規化本來就給它很高的分數；再加權會讓
    #      「標題字面沾到詞」壓過「內文真的在講這件事」。實測問「心臟病有哪些
    #      危險因子」時，boost≥1.0 會讓前四名全變成標題含「危險因子」的中風文章。
    # 掃描結果（--rank-mode vector, top-5, n=22 · hit_rate）：
    #   關閉 .727／0.1 .773／0.2 .818／**0.3 .864**／0.4 .864／0.5 .773／
    #   1.0 .727／1.5 .682。平滑單峰，holdout(n=5) 4/5→5/5 同向。
    #
    # 但要誠實標註效益的邊界：**接上 Cohere 精排後這個增益就消失了**
    # （--rank-mode cohere 三個切面的 hit_rate 都是 18/22、15/17、3/5，
    # 開關前後完全相同，MRR/nDCG 差異落在 Cohere 自身的 run-to-run 噪音內：
    # 同設定連跑三次 MRR 0.598/0.613/0.590）。原因是 reranker 看得到全部 40
    # 筆候選，本來就會把那 3 篇救回來。
    #
    # 所以留著它的理由不是「線上更準」，而是**降級路徑的保險**：Cohere 逾時
    # 或 429 時會退回 VectorScoreReranker，那正是這個設定值 +3 hits 的那一層。
    RAG_TEXT_TITLE_BOOST: float = float(os.getenv("RAG_TEXT_TITLE_BOOST", "0.3"))

    # 向量檢索最低分門檻。預設 0.0＝不過濾；過濾職責在 reranker。
    RAG_VECTOR_MIN_SCORE: float = float(os.getenv("RAG_VECTOR_MIN_SCORE", "0.0"))

    # CRAG grader 失敗時的精排分數門檻。**只在那條降級路徑生效**，正常路徑
    # 不受影響——不是要用數字取代 CRAG，是在 CRAG 不可用時補一張網。
    #
    # 為什麼需要：衛教問答的相關性把關全靠 CRAG（判 incorrect 就轉網搜），
    # 而 RAG_VECTOR_MIN_SCORE 預設 0.0，等於整條管線沒有數值下限。grader
    # 逾時或配額用盡時，既有降級是「不分級直接生成」，於是一組可能毫不相關
    # 的 chunk 會被拿去生成醫療答案，prompt 裡「內容不足請說不知道」只是
    # 軟約束。查核路徑有 fail-closed 的同一性驗證，衛教路徑過去沒有對應的網。
    #
    # 0.3 是保守起步值：Cohere relevance_score 的分佈上，明顯不相關的內容
    # 多落在 0.2 以下。應以 golden set 校準後再調——調高會讓 grader 失效期間
    # 更常轉網搜或拒答，調低則失去這張網的意義。
    #
    # **這個門檻只套用在 Cohere 的 relevance_score 上**，不套用在融合分數或
    # 原始 cosine（見 `_filter_by_degraded_score`）。那兩個尺度量過了，區分
    # 不了相關與不相關：融合分數的不相關均值 0.650 比相關的 0.632 還高，
    # cosine 兩者只差 0.0069 且完全重疊（scripts/rag_degraded_floor_scan.py，
    # 22 題 110 篇）。因此 Cohere 不可用時是整批轉網搜，不是用別的分數頂替。
    RAG_DEGRADED_MIN_SCORE: float = float(os.getenv("RAG_DEGRADED_MIN_SCORE", "0.3"))


    # 精排後之文章層級去重：同一篇文章最多留幾個 chunk 進 top-n（避免單一
    # 文章的多個 chunk 擠爆 top-n 名額，犧牲來源多樣性）。
    RAG_RERANK_MAX_CHUNKS_PER_ARTICLE: int = int(
        os.getenv("RAG_RERANK_MAX_CHUNKS_PER_ARTICLE", "2")
    )

    # 查核判定卡總開關。關閉時代理工具集不提供 verify_claim，行為完全回到
    # 本功能導入前的樣子（claim-verdict-card/design.md Migration Plan），
    # 不需要任何資料回滾。
    CLAIM_VERIFICATION_ENABLED: bool = os.getenv(
        "CLAIM_VERIFICATION_ENABLED", "true"
    ).lower() in ("1", "true", "yes", "on")
    # 主張比對的最低相似度門檻。tasks 1.2 校準（2026-08-18）：30 篇 TFC
    # 查核報告，每篇由 LLM 改寫成 2 句真實使用者口語問法（共 60 題），
    # 實測 matcher 命中率：
    #   0.84 命中率 70%／**0.86 命中率 68%**／0.88 命中率 58%／0.90 命中率 45%
    # 舊預設 0.9 會讓超過半數已查核謠言查不到。取 0.86：再往上每加 0.02，
    # 命中率約再掉 10 個百分點，0.86 是命中率明顯下滑前的最後一格，
    # 兼顧「誤配是唯一嚴重失效模式、門檻寧缺勿濫」（design.md 決策 3）與
    # 堪用的覆蓋率。
    #
    # 上面那組數字是離線的（60 題 LLM 改寫問法），不是線上分佈。要再調之前
    # 先看 `stage=claim_match` 的 top 分數分佈——特別是 outcome=below_threshold
    # 那批離門檻多遠；差 0.01 的近失與根本沒有候選是完全不同的問題，離線題庫
    # 分不出來。同一個 rid 上的 stage=claim_verify outcome=identity_rejected
    # 是另一半：門檻放寬會直接讓那批變多。
    CLAIM_MATCH_MIN_SCORE: float = float(os.getenv("CLAIM_MATCH_MIN_SCORE", "0.86"))

    # Light CRAG（檢索充足性分級；關閉則等同舊行為）
    RAG_CRAG_ENABLED: bool = os.getenv("RAG_CRAG_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    RAG_WEB_FALLBACK_ENABLED: bool = os.getenv(
        "RAG_WEB_FALLBACK_ENABLED", "true"
    ).lower() in ("1", "true", "yes", "on")

    # 整條 RAG 管線的總逾時（秒）。0＝不設限。到點回 [RAG_ERR:TIMEOUT]，agent
    # 請使用者稍後再問。45 秒＝LINE loading 動畫上限 60 秒，扣掉 RAG 以外的段落；
    # 實測最慢一題 18.7 秒，正常題目不會被切。來由見
    # rag/answer_service.DEFAULT_RAG_ANSWER_TIMEOUT_SECONDS。
    RAG_ANSWER_TIMEOUT_SECONDS: float = float(
        os.getenv("RAG_ANSWER_TIMEOUT_SECONDS", "45")
    )

    # 投機生成：CRAG 分級期間先把生成跑起來，分級放行同一批 docs 就直接採用。
    #
    # 實測分級 2.6-7.0s、生成 3.9-9.5s，且 84% 的題目分級結果為 correct
    # （golden set 55 題實測），那些題目等於白賺整段分級時間。
    #
    # 代價：另外 16% 會多一次白跑的生成（付 token，不付延遲），單次請求的
    # Gemini 併發從 1 升到 2。撞到配額或速率限制時把這個關掉是第一步。
    RAG_SPECULATIVE_GENERATE: bool = os.getenv(
        "RAG_SPECULATIVE_GENERATE", "true"
    ).lower() in ("1", "true", "yes", "on")

    # MongoDB 連線逾時（見 app/db/mongo_client.py）。
    #
    # 最重要的是 socket：PyMongo 預設 `socketTimeoutMS=None`＝**無限**，
    # 連線建立後對方不回應就永遠掛著。這是潛在缺陷，與是否觀測到無關。
    #
    # 值刻意寬鬆：健康網路下建立連線 0.7-0.9 秒、穩態查詢 50-250ms，這些
    # 數字遠離正常分佈。設得太緊會在網路不佳時把「慢」變成「錯」——開發機
    # 的網路品質變異很大（本檔曾因一條殘留路由量到 12 秒的假數字，詳見
    # app/db/mongo_client.py 的更正紀錄）。
    MONGODB_SERVER_SELECTION_TIMEOUT_MS: int = int(
        os.getenv("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "20000")
    )
    MONGODB_CONNECT_TIMEOUT_MS: int = int(
        os.getenv("MONGODB_CONNECT_TIMEOUT_MS", "20000")
    )
    MONGODB_SOCKET_TIMEOUT_MS: int = int(
        os.getenv("MONGODB_SOCKET_TIMEOUT_MS", "30000")
    )

    # 參考來源網址的存活檢查（見 services/rag/link_check.py）。
    #
    # 白名單看網域後綴、CRAG 看內容相關性，兩者都不管「這個 url 現在還在
    # 不在」。庫裡的 url 是 ingest 當下的快照，站台改版或子系統除役之後
    # 就成了點不開的來源按鈕——實測 sp1.hso.mohw.gov.tw 整台 TCP 不通，
    # 但它是 gov.tw，白名單一路放行。
    #
    # 預設開啟：降級方向是安全的（少顯示連結，不會顯示錯的），而附上打不開
    # 的來源對衛教問答的傷害大於沒有來源——來源的作用是讓使用者能自己驗證。
    RAG_LINK_CHECK_ENABLED: bool = os.getenv(
        "RAG_LINK_CHECK_ENABLED", "true"
    ).lower() in ("1", "true", "yes", "on")
    # **這是單次 HTTP 請求的逾時，不是使用者實際等待的上限。** 一個網址最多
    # 打四次（HEAD 被擋退 GET、判死後再確認一輪），實測 3s 設定下單一網址
    # 最壞 6.31s；整批的上限由 LinkChecker 的總預算控制
    # （timeout×2＋confirm_delay，預設值下 6.5s）。要估使用者等多久看那個。
    #
    # 3s 是「多數站台的 HEAD 都該在此之內回應」與「不拖垮整輪」之間的取捨，
    # 尚未以線上分佈校準——要調的話先看 stage=rag_link_check 的 ms 分佈，
    # 不要憑感覺加。
    #
    # 註：本註解原本寫「LINE reply token 上限 30s」，該數字**未查證**故移除。
    # 它若為真，影響遠大於本設定：實測整輪（agent＋RAG）有 25.1／43.6／46.2
    # 秒的樣本，那些回覆會直接送不出去。reply 失敗會走
    # reply.py 的 `logger.exception("Failed to send LINE message")`，
    # 要驗證去線上 log 找那筆與 stage=agent_graph 的 ms 對照，不要沿用推測。
    RAG_LINK_CHECK_TIMEOUT_SECONDS: float = float(
        os.getenv("RAG_LINK_CHECK_TIMEOUT_SECONDS", "3")
    )
    # 判活的快取久、判死的快取短。判死可能來自對方站台的暫時性故障或我方
    # 出口網路抖動，短 TTL 是「逾時一律視為不可用」那個保守取捨的補償：
    # 站台恢復後最多 10 分鐘就會重新顯示連結。
    RAG_LINK_CHECK_OK_TTL_SECONDS: float = float(
        os.getenv("RAG_LINK_CHECK_OK_TTL_SECONDS", "86400")
    )
    RAG_LINK_CHECK_DEAD_TTL_SECONDS: float = float(
        os.getenv("RAG_LINK_CHECK_DEAD_TTL_SECONDS", "600")
    )

    # 入庫／核准的來源白名單（逗號分隔的網域後綴）。只有落在此清單的網址能
    # 進向量庫、能被核准。判準見 openspec/changes/harden-url-whitelist/design.md
    # Decision 4：機構層級的權威性、內容穩定可長期存取、無商業銷售動機、
    # 註冊門檻構成實質限制、對台灣使用者的可用性。
    RAG_ALLOWED_DOMAIN_SUFFIXES: str = os.getenv(
        "RAG_ALLOWED_DOMAIN_SUFFIXES",
        "gov.tw,nhri.edu.tw,who.int,cdc.gov,nih.gov,medlineplus.gov",
    )
    # 網搜（Firecrawl）用的 site: 篩選字串，與上方入庫白名單各自獨立設定
    # （design.md Decision 5）：網搜只是收窄召回，真正把關的是入庫白名單。
    RAG_WEB_SEARCH_SITE_FILTER: str = os.getenv(
        "RAG_WEB_SEARCH_SITE_FILTER", "site:gov.tw"
    )
    # 網搜英文那一路的網域（逗號分隔，送給 Firecrawl v2 的 includeDomains）。
    # 中文那一路仍用上面的 site: 篩選查 gov.tw。罕見病在 gov.tw 常沒有中文
    # 資料，要用英文醫學名詞查 nih.gov 才找得到（例：persistent genital arousal
    # disorder 在 nih.gov 回 5 筆 PMC／PubMed，中文原句在 gov.tw 只命中一份
    # 不相關的 PDF）。這裡的網域必須也在入庫白名單內，因為搜回來的結果照樣
    # 用白名單過濾。空字串＝不搜英文。
    RAG_WEB_SEARCH_EN_DOMAINS: str = os.getenv(
        "RAG_WEB_SEARCH_EN_DOMAINS", "nih.gov,medlineplus.gov"
    )
    # 手動知識回報的濫用防護。只計 source="manual" 的回報：若把 agent tool 與
    # web fallback 自動建報也算進來，使用者在 LINE 多問幾個知識庫答不出來的
    # 問題就會把自己的手動額度用光，而他完全不會知道自己「用掉」了什麼
    # （design.md 決策 4）。視窗是滾動 24 小時而非自然日，否則午夜前後可以送
    # 兩倍。設成 env 是因為真實用量還沒有資料，第一版一定要調。
    KNOWLEDGE_REPORT_MANUAL_DAILY_QUOTA: int = int(
        os.getenv("KNOWLEDGE_REPORT_MANUAL_DAILY_QUOTA", "10")
    )
    KNOWLEDGE_REPORT_MAX_SOURCE_URLS: int = int(
        os.getenv("KNOWLEDGE_REPORT_MAX_SOURCE_URLS", "3")
    )
    # 核准前的內容預覽。TTL 決定「admin 看過的那份內容」還能當多久的核准依據：
    # 太短會讓正常審核途中就逾期、太長則失去「你看到的就是現在的內容」的意義。
    KNOWLEDGE_PREVIEW_TTL_MINUTES: int = int(
        os.getenv("KNOWLEDGE_PREVIEW_TTL_MINUTES", "60")
    )
    # 單次預覽的 URL 數量上限。每個 URL 都是一次外部抓取（Firecrawl 逾時下限
    # 45 秒），沒有上限時一筆回報就能讓背景工作跑上好幾分鐘並吃掉抓取額度。
    KNOWLEDGE_PREVIEW_MAX_URLS: int = int(os.getenv("KNOWLEDGE_PREVIEW_MAX_URLS", "5"))
    # 回傳給前端的原文長度上限。超過就截斷並標示，伺服器端仍保留全文供 ingest；
    # 這是誠實的做法——改成回傳摘要的話，核准的對象與收錄的對象就不是同一份
    # 文字，TOCTOU 只是換個形狀重新出現（design.md 決策 3）。
    KNOWLEDGE_PREVIEW_RETURN_MAX_CHARS: int = int(
        os.getenv("KNOWLEDGE_PREVIEW_RETURN_MAX_CHARS", "20000")
    )
    # 藥袋辨識。預設開啟；整條路徑仍可獨立開關，出問題時把它設回 false
    # 即可停用，已建立的藥品與提醒關聯不受影響（推播的藥品區塊在關聯為空或
    # 藥品失效時會自動退回原版面），不需要資料回滾。
    PRESCRIPTION_SCAN_ENABLED: bool = os.getenv(
        "PRESCRIPTION_SCAN_ENABLED", "true"
    ).lower() in ("1", "true", "yes", "on")
    # 家庭 RBAC 的全域總閘（kill switch）。預設**開啟**。
    #
    # 開啟不等於全體強制：強制以**資料擁有者**為邊界逐一啟用（見
    # FamilyTree.rbac_migration_state），兩者是 AND 關係。擁有者替每一位家人都
    # 指派角色的那一刻才會切成 enforced（app/services/family/rbac_migration.py）；
    # 還沒指派完的家庭照舊跑影子模式，行為與導入前相同。
    #
    # 原本預設關閉的理由是「切換當下會中斷既有的照顧行為」。那時沒有任何路徑會
    # 把擁有者切成 enforced，開關打開也沒有人被強制；改成擁有者指派完才切之後，
    # 被強制的只有親手做完決定的家庭，不會有人在沒人決定的情況下失去功能。
    #
    # 這個開關的角色是出事時讓全體立刻回到變更前的行為，不必逐一改資料：
    # 讓 backend 與 scheduler 的 pod 拿到 FAMILY_RBAC_ENFORCED=false。注意
    # CARE-infra 的 backend ConfigMap 是逐一列鍵的
    # （helm/care/templates/configmap-backend.yaml），只在 values.yaml 加這個鍵
    # 進不到 pod，要連模板那一行一起加。
    FAMILY_RBAC_ENFORCED: bool = os.getenv(
        "FAMILY_RBAC_ENFORCED", "true"
    ).lower() in ("1", "true", "yes", "on")
    # 委任授權的**啟用**閘門。預設關閉，且在核可流程（身分驗證、醫療證明、
    # 法定監護證明）由後續的產品／法務 change 定義之前不得開啟。
    #
    # 與 FAMILY_RBAC_ENFORCED 是兩個不同的東西：那個管「授權判定要不要強制」，
    # 這個管「能不能建立新的委任」。撤銷不受本開關限制——閘門管的是能不能給
    # 出去，不是能不能收回來。
    FAMILY_DELEGATION_ACTIVATION_ENABLED: bool = os.getenv(
        "FAMILY_DELEGATION_ACTIVATION_ENABLED", "false"
    ).lower() in ("1", "true", "yes", "on")
    PRESCRIPTION_SCAN_MAX_IMAGE_BYTES: int = int(
        os.getenv("PRESCRIPTION_SCAN_MAX_IMAGE_BYTES", str(8 * 1024 * 1024))
    )
    PRESCRIPTION_SCAN_TIMEOUT_SECONDS: int = int(
        os.getenv("PRESCRIPTION_SCAN_TIMEOUT_SECONDS", "60")
    )
    # 看診錄音的上傳上限 40 MB（LIFF 上傳與 LINE 聊天室下載共用）。真正的限制是時間：
    # 只整理前 30 分鐘（clinic_transcribe.MAX_AUDIO_SECONDS，理由是記憶體與等待時間）。
    # 錄音的位元率各家不同，這個值是抓 30 分鐘在常見位元率下的寬鬆上界，用來擋住
    # 明顯的誤用（傳一部影片上來），不是精準的時間換算。
    CLINIC_RECORDING_MAX_BYTES: int = int(
        os.getenv("CLINIC_RECORDING_MAX_BYTES", str(40 * 1024 * 1024))
    )
    PRESCRIPTION_DRAFT_TTL_MINUTES: int = int(
        os.getenv("PRESCRIPTION_DRAFT_TTL_MINUTES", "60")
    )
    # 藥證庫為建置期產出的靜態檔，執行期不對外連線。
    DRUG_CATALOG_PATH: str = os.getenv(
        "DRUG_CATALOG_PATH", "resources/drug_catalog.json"
    )
    # 低於此相似度視為未命中。保守起步：寧可多一次人工核對，
    # 也不要讓錯讀的藥名通過校驗而被當成高信心。
    DRUG_CATALOG_MATCH_THRESHOLD: float = float(
        os.getenv("DRUG_CATALOG_MATCH_THRESHOLD", "0.88")
    )
    # 藥丸縮圖同樣是建置期落地的靜態資源（scripts/build_drug_catalog.py
    # --fetch-images 產出並提交進 repo），執行期不對外連線，見
    # openspec/changes/drug-appearance-photo/design.md 決策 2、4。
    DRUG_APPEARANCE_IMAGE_DIR: str = os.getenv(
        "DRUG_APPEARANCE_IMAGE_DIR", "resources/drug_appearance"
    )
    # 對外路徑前綴，比照 TTS_AUDIO_URL_PATH 的作法接在 PUBLIC_BASE_URL 之後。
    # 檔名本身已是證號的雜湊（不可枚舉、不帶用藥資訊），這段路徑前綴不需要
    # 也不應該再帶任何識別碼。
    DRUG_APPEARANCE_IMAGE_URL_PATH: str = os.getenv(
        "DRUG_APPEARANCE_IMAGE_URL_PATH", "/drug-appearance"
    )
    # 仿單適應症同樣是建置期落地的靜態檔（scripts/build_drug_catalog.py
    # --fetch-indications 產出），執行期不對外連線。刻意與 drug_catalog.json
    # 分開：藥證庫的字元 n-gram 反向索引是效能敏感結構，適應症對藥名比對毫無
    # 貢獻，併入只會讓它與常駐記憶體無謂變大（實測 15.9 MB → 22.2 MB）。
    # 見 openspec/changes/drug-indication/design.md 決策 1。
    DRUG_INDICATION_PATH: str = os.getenv(
        "DRUG_INDICATION_PATH", "resources/drug_indications.json"
    )
    # 摘要的字數上限。仿單適應症常涵蓋多個適應症與使用條件，壓得太短會讓
    # 「須合併其他藥物使用」這類條件被犧牲掉，而摘要 SHALL NOT 遺漏任何一個
    # 適應症；訂得太寬則失去摘要的意義。這個值只約束建置期的摘要生成，
    # 不影響原文——原文一律完整保留且可展開。
    DRUG_INDICATION_SUMMARY_MAX_CHARS: int = int(
        os.getenv("DRUG_INDICATION_SUMMARY_MAX_CHARS", "60")
    )

    # 用藥風險偵測。預設開啟，沿用 PRESCRIPTION_SCAN_ENABLED 的形狀：不必在
    # 每個環境各設一次，但出問題時把它設回 false 就能整條停用（設為 false 時
    # 完全不執行抽取、判定與推播，行為與本能力導入前相同），不需要 deploy 才
    # 救得回來。
    #
    # 這個開關比藥袋辨識更需要留著：通報家人是不可逆的動作——訊息推出去收不
    # 回來，收件人是一整個家庭，而誤報一次的代價（長輩覺得被監視，從此不再
    # 發問）遠大於漏報一次。誤報率還沒有真實流量的數據（見 design.md 的
    # Open Questions），這是唯一的煞車。
    SAFETY_ALERT_ENABLED: bool = os.getenv("SAFETY_ALERT_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    # 同一位使用者對同一個藥名的通報節流視窗（小時）。設成 env 是因為真實的
    # 重複提問頻率還沒有資料，第一版一定要調（design.md Open Question）。
    SAFETY_ALERT_DEDUPE_HOURS: int = int(os.getenv("SAFETY_ALERT_DEDUPE_HOURS", "24"))
    # 抽取呼叫逾時（秒）。本能力是背景旁路，使用者並未在等它的結果，逾時一律
    # 靜默結束，不通報也不通知使用者。
    SAFETY_ALERT_TIMEOUT_SECONDS: int = int(
        os.getenv("SAFETY_ALERT_TIMEOUT_SECONDS", "20")
    )
    # 非處方藥成分重複偵測的總開關。與 SAFETY_ALERT_ENABLED 分開而不共用一個
    # 旗標：兩者的誤報型態完全不同，任一邊需要緊急關閉時不該連坐另一邊。
    #
    # 高風險通報的誤報來自「聊天中提到藥名」的抽取，是語意判斷；這條的誤報來自
    # 藥證庫的成分欄位與白名單，是資料判斷。實測隨機配對觸發率 3.0%，但真實的
    # 用藥組合分布與隨機配對不同，上線後的實際打擾頻率仍是未知數——這是唯一的
    # 煞車。
    OTC_ALERT_ENABLED: bool = os.getenv("OTC_ALERT_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    # 個人健康紀錄超出範圍／經期異常推播的總開關（health-alerts spec「推播
    # 總開關」）。預設**關閉**，方向與上面兩個安全通報開關相反：那兩個是
    # 「預設開、出事才關」的煞車，這個是「推播文案審閱完成、LINE 憑證到位
    # 之前，先讓紀錄功能上線」的起跑線。關閉時等級照常判定與儲存，只是不
    # 推播——由 HealthAlertService 內部短路，呼叫端（health_measurement_service
    # ／menstrual_service）無需知道這個旗標。
    HEALTH_ALERTS_ENABLED: bool = os.getenv(
        "HEALTH_ALERTS_ENABLED", "false"
    ).lower() in ("1", "true", "yes", "on")

    # ── 每日醫療消息卡（medical-news-push）────────────────────────
    #
    # 整條的煞車。理由與 SAFETY_ALERT_ENABLED 相同、程度更強：這是**主動**
    # 推播，使用者沒有在問問題，而推錯一則「你在吃的藥出問題了」最可能的
    # 後果是長輩自行停藥。Tier 1 的偽陽性率目前沒有真實流量的數據
    # （design.md 證據缺口 4），這是唯一不需要 deploy 就救得回來的開關。
    MEDICAL_NEWS_ENABLED: bool = os.getenv(
        "MEDICAL_NEWS_ENABLED", "true"
    ).lower() in ("1", "true", "yes", "on")
    # 索引排在推播之前數小時：推播要用的是當天剛索引好的內容。兩者若太接近，
    # 索引還沒跑完推播就開始選材，當天的新消息會全部晚一天才送到。
    MEDICAL_NEWS_INDEX_TIME: str = os.getenv("MEDICAL_NEWS_INDEX_TIME", "03:00")
    MEDICAL_NEWS_PUSH_TIME: str = os.getenv("MEDICAL_NEWS_PUSH_TIME", "09:00")
    # 消息的時效上限（天）。30 天是暫定值——gov.tw 的日期抽取可靠度尚未量測
    # （design.md 證據缺口 2），這個值一定要依實際命中率調整，故設成 env。
    MEDICAL_NEWS_MAX_AGE_DAYS: int = int(
        os.getenv("MEDICAL_NEWS_MAX_AGE_DAYS", "30")
    )
    # 每個藥名每次取幾筆搜尋結果。搜尋成本是 O(不重複藥數 × 這個值)。
    MEDICAL_NEWS_SEARCH_LIMIT: int = int(
        os.getenv("MEDICAL_NEWS_SEARCH_LIMIT", "5")
    )
    # 每位使用者每日的分享次數上限。防的是把族譜當廣播用。
    MEDICAL_NEWS_DAILY_SHARE_LIMIT: int = int(
        os.getenv("MEDICAL_NEWS_DAILY_SHARE_LIMIT", "5")
    )

    # --- LINE 進站流程 ---
    #
    # 一則訊息從進 agent 到拿回回覆的總上限（秒）。這個值只包住 agent.invoke，
    # 不含之後的 TTS 與送 LINE；各段各自的上限如下，加總就是它的來由：
    #   RAG 那條腿      45s（rag/answer_service.DEFAULT_RAG_ANSWER_TIMEOUT_SECONDS）
    #   guardrail／急迫度判斷、agent 決策、最後生成：Gemini 呼叫本身沒有逾時
    #                   （見 answer_service.py:92），實測 thinking 重的題目 10–25s
    #   → 45 + 25 ≈ 70s，再留 20s 給 LINE 回傳／Mongo 的抖動，取 90s。
    # 使用者實際等待時間還要再加 agent 之後的 Taigi TTS（最多 20s，
    # speech/taigi_client.TTS_TIMEOUT_SECONDS）與送 LINE（LINE_API_TIMEOUT_SECONDS
    # 10s）：最壞約 120s。超過這個值代表某段卡死了（例如 Gemini 無逾時的呼叫
    # 掛住），與其讓使用者無限等，不如回一句「稍後再試」並放掉這一輪。
    AGENT_TOTAL_TIMEOUT_SECONDS: float = float(
        os.getenv("AGENT_TOTAL_TIMEOUT_SECONDS", "90")
    )
    # webhook 事件 id 的去重保留時間（秒）。LINE 的重送（isRedelivery）發生在
    # 我們沒在時限內回 200 之後，通常是幾秒到幾分鐘內；10 分鐘足以蓋住重送
    # 窗口，又不會讓 Redis 累積無用的 key。
    LINE_WEBHOOK_EVENT_TTL_SECONDS: int = int(
        os.getenv("LINE_WEBHOOK_EVENT_TTL_SECONDS", "600")
    )

    # --- 認證與家人權限 ---
    #
    # 執行環境。只有兩個值有意義：development 與其他。缺席時視為 production——
    # 缺一個環境變數的後果應該是「起不來」（startup_checks 會拒絕預設 JWT
    # 密鑰），不是「靜靜地用開發設定跑正式流量」。development 另外會打開
    # /docs 與 /openapi.json。
    APP_ENV: str = os.getenv("APP_ENV", "production").strip().lower() or "production"

    # 請求頻率上限（app/core/rate_limit.py；每個行程各自計數，2 個 replica 即 2 倍）。
    # LIFF 登入：前端每次開啟 LIFF 才登入一次，token 有效 120 分鐘；10 次／分鐘
    # 足以涵蓋同一個家用 NAT 後面的所有人，擋的是拿偷來的 id_token 反覆試。
    RATE_LIMIT_LIFF_LOGIN_PER_MINUTE: int = int(
        os.getenv("RATE_LIMIT_LIFF_LOGIN_PER_MINUTE", "10")
    )
    # 邀請碼驗證：未登入端點，一次點開連結只查一次；邀請碼是 64 位元隨機值，
    # 20 次／分鐘讓枚舉在數學上不可能，同時不影響真實使用者。
    RATE_LIMIT_INVITE_VERIFY_PER_MINUTE: int = int(
        os.getenv("RATE_LIMIT_INVITE_VERIFY_PER_MINUTE", "20")
    )
    # 手動摘要：每次都是一趟 Gemini 呼叫，摘要的是同一天的對話，重做幾次結果
    # 也不會不同；5 次／小時擋的是把它當免費 LLM 用。
    RATE_LIMIT_SUMMARY_GENERATE_PER_HOUR: int = int(
        os.getenv("RATE_LIMIT_SUMMARY_GENERATE_PER_HOUR", "5")
    )
    # 藥袋辨識：每次都是一趟 Gemini 視覺呼叫。一次回診通常 1～3 個藥袋、拍壞
    # 重拍一兩次，10 次／小時涵蓋得了。
    RATE_LIMIT_PRESCRIPTION_SCAN_PER_HOUR: int = int(
        os.getenv("RATE_LIMIT_PRESCRIPTION_SCAN_PER_HOUR", "10")
    )
    # 走失求救的位置上傳（每位使用者每分鐘）。長輩的定位頁每 20 秒傳一次，
    # 正常是每分鐘 3 次；家人地圖頁的回報（presence）每 15 秒一次、每分鐘 4 次，
    # 兩者各算在自己的帳號上。重新整理頁面、網路不穩重送都會多打幾次，給 3～4 倍餘裕。
    # 撞到上限的代價是地圖少一個點，不會漏通知，所以不必再放寬。
    RATE_LIMIT_LOST_LOCATION_PER_MINUTE: int = int(
        os.getenv("RATE_LIMIT_LOST_LOCATION_PER_MINUTE", "12")
    )
    # --- 認證與家人權限（結束）---

    # --- RAG／agent 管線 ---
    #
    # guardrail 升級給 Gemini 那一步的逾時（秒）。guardrail 與急迫度判斷並行跑在
    # 每一則訊息的最前面；急迫度早就有 4 秒逾時（urgency.DEFAULT_TIMEOUT_SECONDS），
    # guardrail 卻沒有——Gemini 一掛，「我阿公昏迷」的紅卡就被 guardrail 拖著，
    # 而它的結果對緊急短路根本用不到。逾時後 fail-open（允許 RAG），與分類失敗
    # 時的處置一致。4 秒與急迫度對齊：本機量 gemini-3.8-flash 的分類中位數約 2 秒。
    GUARDRAIL_LLM_TIMEOUT_SECONDS: float = float(
        os.getenv("GUARDRAIL_LLM_TIMEOUT_SECONDS", "4")
    )
    # 單次 Gemini 請求的逾時（秒）。langchain-google-genai 4.2.2 預設 timeout=None、
    # 重試 6 次：一次卡死的連線可以把 RAG 的 45 秒總預算（RAG_ANSWER_TIMEOUT_SECONDS）
    # 整個吃掉，之後還會再重試。30 秒的來由：管線裡最慢的單次呼叫是生成，實測
    # 3.9–9.5 秒（answer_service 投機生成那段的數字），30 秒是它的 3 倍；同時小於
    # 45 秒總預算，讓「一次連線卡住」不可能獨自把整條管線拖到逾時。重試次數在
    # gemini_service.DEFAULT_MAX_RETRIES，不放這裡：它不該隨環境改。
    GEMINI_REQUEST_TIMEOUT_SECONDS: float = float(
        os.getenv("GEMINI_REQUEST_TIMEOUT_SECONDS", "30")
    )


settings = Settings()
