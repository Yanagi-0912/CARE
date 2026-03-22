import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Gemini API 配置
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "gemini-2.5-flash")

    # Line Messaging API 配置
    LINE_CHANNEL_ID: str = os.getenv("LINE_CHANNEL_ID")
    LINE_CHANNEL_SECRET: str = os.getenv("LINE_CHANNEL_SECRET")
    # 可選：如果不想使用動態 token，可設定 long-lived token
    LINE_CHANNEL_ACCESS_TOKEN: str = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")

    # RAG / Embedding 配置
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")

    # MongoDB Vector Search 配置
    MONGODB_URI: str = os.getenv("MONGODB_URI", "")
    MONGODB_DB: str = os.getenv("MONGODB_DB", "")
    MONGODB_COLLECTION: str = os.getenv("MONGODB_COLLECTION", "")
    MONGODB_VECTOR_INDEX: str = os.getenv("MONGODB_VECTOR_INDEX", "")
    MONGODB_VECTOR_FIELD: str = os.getenv("MONGODB_VECTOR_FIELD", "embedding")
    MONGODB_TEXT_FIELD: str = os.getenv("MONGODB_TEXT_FIELD", "text")
    MONGODB_VECTOR_DIM: int = int(os.getenv("MONGODB_VECTOR_DIM", "0"))


settings = Settings()
