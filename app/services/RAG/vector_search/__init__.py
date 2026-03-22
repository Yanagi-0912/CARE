# ============================================================
# vector_search 套件
# ============================================================
#
# 主要入口：MongoVectorSearchReader（reader.py）
#   → 負責協調：驗證 → 組管線 → 執行查詢 → 映射結果
#   → 從外部只需傳入 VectorSearchConfig，即可 await search_by_embedding()（Motor 非同步）
#
# 各模組分工：
#   config.py      VectorSearchConfig：Mongo 連線與欄位設定（DI 用）
#   reader.py      MongoVectorSearchReader：查詢流程的主要大腦（從這邊看起）
#   pipeline.py    build_vector_search_pipeline：組裝 $vectorSearch 管線
#   validation.py  查詢前的設定與向量維度檢查
#   mapping.py     Mongo 文件 → ChunkHit 型別轉換
#   types.py       ChunkHit / ChunkHits 型別定義
#
# 對外只需 import：
#   from app.services.RAG.vector_search import MongoVectorSearchReader, VectorSearchConfig
# ============================================================

from .config import VectorSearchConfig
from .reader import MongoVectorSearchReader
from .types import ChunkHit, ChunkHits

__all__ = [
    "ChunkHit",
    "ChunkHits",
    "MongoVectorSearchReader",
    "VectorSearchConfig",
]
