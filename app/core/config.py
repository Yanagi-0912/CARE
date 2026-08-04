import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

class Settings:
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


settings = Settings()
