import os
import pymongo
from pymongo import UpdateOne
from dotenv import load_dotenv

def main():
    # 載入 .env 檔案中的環境變數
    load_dotenv()
    
    # 從 .env 取得 MONGODB_URL
    mongodb_url = os.getenv("MONGODB_URL")
    if not mongodb_url:
        print("錯誤：找不到 MONGODB_URL，請確認 .env 檔案設定是否確實存在。")
        return

    print("正在連線至 MongoDB...")
    client = pymongo.MongoClient(mongodb_url)
    
    # 連接至指定資料庫與集合 (Collection)
    # 注意：這裡使用您提供的 CARE_datebase 名稱，若其實是 database 請手動修改
    db = client["CARE_database"]
    collection = db["medicalFacilities"]
    
    print(f"成功連線至資料庫: {db.name}, 集合: {collection.name}")
    
    # 篩選出擁有 latitude 與 longitude，但可以重新寫入 location 的資料
    query = {
        "latitude": {"$exists": True, "$ne": None, "$ne": ""},
        "longitude": {"$exists": True, "$ne": None, "$ne": ""}
    }
    
    total_docs = collection.count_documents(query)
    print(f"預計檢查並更新 {total_docs} 筆資料...")
    
    if total_docs == 0:
        print("沒有找到包含經緯度的資料，請確認資料庫與集合名稱是否正確。")
    else:
        print("開始轉換並更新資料 (使用 Bulk Write 大幅增進寫入效能)...")
        # 使用 bulk_write 來做批次更新，2.5萬筆資料大概幾秒內就能完成，避免單筆寫入太慢
        operations = []
        cursor = collection.find(query)
        
        success_count = 0
        
        for doc in cursor:
            try:
                # 確保經緯度能轉為浮點數
                lat = float(doc["latitude"])
                lon = float(doc["longitude"])
                
                # 準備更新操作
                ops = UpdateOne(
                    {"_id": doc["_id"]},
                    {"$set": {
                        "location": {
                            "type": "Point",
                            "coordinates": [lon, lat] # GeoJSON 規定：[經度 longitude, 緯度 latitude]
                        }
                    }}
                )
                operations.append(ops)
                success_count += 1
                
                # 每 1000 筆整批送出一次到 MongoDB，節省網路來回時間
                if len(operations) >= 1000:
                    collection.bulk_write(operations)
                    operations = []
                    print(f"已處理 {success_count} 筆資料...")
                    
            except (ValueError, TypeError):
                # 遇到經緯度是字串但非數字 (例如 "未知") 則略過
                continue
                
        # 寫入最後未滿 1000 筆的剩餘資料
        if operations:
            collection.bulk_write(operations)
            print(f"已處理 {success_count} 筆資料...")
            
        print(f"✅ 成功將 {success_count} 筆資料的經緯度轉換為 GeoJSON 格式！")

    # 無論有沒有更新資料，都確保建立好 2dsphere 索引
    print("正在為 `location` 欄位建立 2dsphere 空間索引...")
    collection.create_index([("location", pymongo.GEOSPHERE)])
    print("✅ 2dsphere 索引建立完成！現在可以開始飛速進行最近距離查詢了！")

if __name__ == "__main__":
    main()
