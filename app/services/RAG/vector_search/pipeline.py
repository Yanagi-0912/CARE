from typing import Any, List

from .config import VectorSearchConfig

#這是 mongodb 表轉做法對對向量索引。表準左法就是aggregate 聚合管線
# aggregate 不是sql的 aggregate 而是一串stage 不停地處理一些事情
#這邊有兩個stage vectorsearch 跟 project
def build_vector_search_pipeline(
    config: VectorSearchConfig,
    *,#關鍵字回傳避免參數傳錯 其他地方 call的時候
    query_embedding: List[float],#句子的向量
    k: int,#要回傳幾筆 
    num_candidates: int,#在搜尋向量資料庫前不是每一筆都會經過向量比對算距離，而是玉一個叫做ann 索引的方式把一區機率相近的候選數量
) -> List[dict[str, Any]]:

    return [
        {
            "$vectorSearch": {#stage 1 把整個 collection 挑出跟query 最相近的一批，並附上相似的分數
                "index": config.vector_index,#輸出為以排序，topk
                "path": config.vector_field,
                "queryVector": query_embedding,
                "numCandidates": num_candidates,
                "limit": k,
            }
        },
        {
            "$project": {#對每一筆搜尋結果只保留你要的欄位 文字 id score
                config.text_field: 1,
                "_id": 1,
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]
