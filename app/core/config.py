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
    MODEL_NAME: str = os.getenv("MODEL_NAME", "gemini-2.5-flash")

    # Line Messaging API 配置
    LINE_CHANNEL_ID: str = os.getenv("LINE_CHANNEL_ID")
    LINE_CHANNEL_SECRET: str = os.getenv("LINE_CHANNEL_SECRET")

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

    # 使用者上傳文件暫存向量庫（與官方 MONGODB_COLLECTION 分離）
    MONGODB_USER_DOCS_COLLECTION: str = os.getenv("MONGODB_USER_DOCS_COLLECTION", "")
    MONGODB_USER_DOCS_VECTOR_INDEX: str = os.getenv(
        "MONGODB_USER_DOCS_VECTOR_INDEX", ""
    )
    USER_DOCS_TTL_SECONDS: int = int(os.getenv("USER_DOCS_TTL_SECONDS", "86400"))

    # Consultation / Redis 配置
    REDIS_URL: str = os.getenv("REDIS_URL", "")

    # Consultation daily summary scheduler
    CONSULTATION_DAILY_SUMMARY_TIME: str = os.getenv(
        "CONSULTATION_DAILY_SUMMARY_TIME", "02:00"
    )

    # Firecrawl（WebSearchService／RAG web fallback）
    FIRECRAWL_API_KEY: str = os.getenv("FIRECRAWL_API_KEY", "")

    # Cohere Rerank（未設定 API key 時降級為向量 score top-n）
    COHERE_API_KEY: str = os.getenv("COHERE_API_KEY", "")
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
    # 家庭 RBAC 的全域總閘（kill switch）。預設 **關閉**——本能力比既有行為
    # 嚴格，切換當下會中斷既有的照顧行為，因此先跑影子模式：照常計算兩種
    # 判定並記錄差異，但依 legacy 放行，行為與導入前完全相同。
    #
    # 開啟後仍不是全體一起強制：強制以**資料擁有者**為邊界逐一啟用（見
    # FamilyTree.rbac_migration_state），兩者是 AND 關係。這個開關的角色是
    # 出事時讓全體立刻回到變更前的行為，不必逐一改資料。
    FAMILY_RBAC_ENFORCED: bool = os.getenv(
        "FAMILY_RBAC_ENFORCED", "false"
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


settings = Settings()
