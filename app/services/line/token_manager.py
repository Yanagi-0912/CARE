import requests
import logging
from datetime import datetime, timedelta
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


class LineTokenManager:
    def __init__(self):
        self.channel_id = settings.LINE_CHANNEL_ID
        self.channel_secret = settings.LINE_CHANNEL_SECRET
        
        # Token 緩存
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
    
    def get_token(self) -> str:
        # 檢查緩存是否有效
        if self._is_token_valid():
            logger.debug("使用緩存的 access token")
            return self._access_token
        
        # 獲取新的 token
        logger.info("緩存的 token 已過期或不存在，正在獲取新的 token...")
        return self._fetch_new_token()
    
    def _is_token_valid(self) -> bool:
        if not self._access_token or not self._token_expires_at:
            return False
        
        # 提前 5 分鐘刷新，避免在使用時過期
        buffer_time = timedelta(minutes=5)
        return datetime.now() < (self._token_expires_at - buffer_time)
    
    def _fetch_new_token(self) -> str:
        if not self.channel_id or not self.channel_secret:
            raise ValueError(
                "無法獲取 token：LINE_CHANNEL_ID 和 LINE_CHANNEL_SECRET 未設定。"
                "請在 .env 檔案中設定這些變數。"
            )
        
        url = "https://api.line.me/oauth2/v3/token"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {
            "grant_type": "client_credentials",
            "client_id": self.channel_id,
            "client_secret": self.channel_secret
        }
        
        try:
            response = requests.post(url, headers=headers, data=data, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            access_token = result.get("access_token")
            expires_in = result.get("expires_in", 2592000)  # 預設 30 天 (秒)
            
            if not access_token:
                raise ValueError("API 返回的響應中沒有 access_token")
            
            # 緩存 token 和過期時間
            self._access_token = access_token
            self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info(
                f"成功獲取新的 access token，"
                f"有效期至: {self._token_expires_at.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            return access_token
            
        except requests.exceptions.RequestException as e:
            error_msg = f"獲取 access token 失敗: {e}"
            if hasattr(e, 'response') and e.response is not None:
                error_msg += f"\nAPI 響應: {e.response.text}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
line_token_manager = LineTokenManager()
