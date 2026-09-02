import logging
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings

logger = logging.getLogger(__name__)

class MongoDBManager:
    """
    MongoDB 管理工具
    """
    
    _mongodb_url: str = ""
    _client: Optional[AsyncIOMotorClient] = None

    @classmethod
    def configure(cls, mongodb_url: str) -> None:
        """
        設定 MongoDB_url
        """
        cls._mongodb_url = mongodb_url or ""

    @classmethod
    def get_client(cls) -> AsyncIOMotorClient:
        """
        取得 MongoDB 連線
        """
        if cls._client is None:
            mongodb_url = cls._mongodb_url.strip()
            if not mongodb_url:
                raise ValueError("未設定 MongoDB_url")
            logger.info("Initializing async MongoDB connection (Motor)...")
            cls._client = AsyncIOMotorClient(mongodb_url)
        return cls._client

    @classmethod
    def get_database(cls) -> AsyncIOMotorDatabase:
        """
        取得資料庫實例，資料庫名稱統一從 settings.MONGODB_DB 讀取，
        避免各個 collection getter 各自寫死資料庫名稱。
        """
        db_name = settings.MONGODB_DB
        if not db_name:
            raise ValueError("未設定 MONGODB_DB 參數")
        return cls.get_client()[db_name]

    @classmethod
    def get_users_collection(cls):
        """
        取得 users collection
        """
        return cls.get_database()["users"]

    @classmethod
    def get_medical_collection(cls):
        """
        取得 medicalFacilities collection
        """
        return cls.get_database()["medicalFacilities"]

    @classmethod
    def get_family_tree_collection(cls):
        """
        取得 family_trees collection
        """
        return cls.get_database()["family_trees"]

    @classmethod
    def get_pending_invitations_collection(cls):
        """
        取得 pending_invitations collection
        """
        return cls.get_database()["pending_invitations"]

    @classmethod
    def get_family_delegations_collection(cls):
        """
        取得 family_delegations collection（受委任 GUARDIAN 的授權紀錄）

        與 family_trees 分開存放：族譜是擁有者自己維護的成員名單，委任則是
        「不經擁有者同意就取得其資料權限」的例外路徑，兩者的寫入資格與稽核
        要求完全不同。混在同一份文件裡，一次族譜更新就可能連帶動到委任。
        """
        return cls.get_database()["family_delegations"]

    @classmethod
    def get_family_role_audit_collection(cls):
        """
        取得 family_role_audit collection（角色與委任變更的稽核紀錄）

        僅可追加：指派 GUARDIAN 是本系統唯一「一次點擊就讓某人讀得到長輩全部
        對話」的操作，事後必須能回答「誰在什麼時候給了誰權限」。
        """
        return cls.get_database()["family_role_audit"]

    @classmethod
    def get_consultation_summaries_collection(cls):
        """
        取得 consultation_summaries collection
        """
        return cls.get_database()["consultation_summaries"]

    @classmethod
    def get_medication_reminders_collection(cls):
        """
        取得 medication_reminders collection
        """
        return cls.get_database()["medication_reminders"]

    @classmethod
    def get_medication_logs_collection(cls):
        """
        取得 medication_logs collection
        """
        return cls.get_database()["medication_logs"]

    @classmethod
    def get_medications_collection(cls):
        """
        取得 medications collection
        """
        return cls.get_database()["medications"]

    @classmethod
    def get_prescription_drafts_collection(cls):
        """
        取得 prescription_drafts collection
        """
        return cls.get_database()["prescription_drafts"]

    @classmethod
    def get_knowledge_reports_collection(cls):
        """
        取得 knowledge_reports collection
        """
        return cls.get_database()["knowledge_reports"]

    @classmethod
    def get_knowledge_report_previews_collection(cls):
        """
        取得 knowledge_report_previews collection

        內容快照獨立成一個集合而非塞進 knowledge_reports：待審列表每頁回 50 筆，
        內容放進報告文件會讓回應從幾 KB 變成幾 MB；且快照該過期、報告不該，
        TTL 索引沒辦法只清掉一個欄位（design.md 決策 2）。
        """
        return cls.get_database()["knowledge_report_previews"]

    @classmethod
    def get_drug_news_collection(cls):
        """
        取得 drug_news collection（藥名／成分的近期官方消息索引）

        內容與使用者無關，因此服用同一種藥的所有人共用同一批文件——這是索引服務
        按藥名而非按人快取的前提（見 openspec/changes/medical-news-push/design.md 決策 2）。
        """
        return cls.get_database()["drug_news"]

    @classmethod
    def get_medical_news_deliveries_collection(cls):
        """
        取得 medical_news_deliveries collection（某位使用者收過哪些消息卡）

        它的 (user_id, news_ref) 唯一索引一物二用：既是去重，也是多實例下的推播權
        搶佔。文件存在本身就代表「已推播」。
        """
        return cls.get_database()["medical_news_deliveries"]

    @classmethod
    def get_medical_news_shares_collection(cls):
        """
        取得 medical_news_shares collection（某位收件人被分享過哪些消息）

        防的是「三位家人都按了認同，同一位長輩收到三張一樣的卡」。
        """
        return cls.get_database()["medical_news_shares"]

    @classmethod
    def get_safety_alerts_collection(cls):
        """
        取得 safety_alerts collection（用藥風險通報的節流紀錄）
        """
        return cls.get_database()["safety_alerts"]

