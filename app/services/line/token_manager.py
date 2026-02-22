"""
LINE Bot Token 管理服務
負責動態獲取和刷新 Channel Access Token
"""
import requests
import logging
from datetime import datetime, timedelta
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


class LineTokenManager:
    """
    LINE Channel Access Token 管理器
    
    使用 Channel ID 和 Channel Secret 動態獲取 short-lived token，
    並自動處理 token 的緩存和刷新。
    """
    
    def __init__(self):
        """初始化 Token 管理器"""
        self.channel_id = settings.LINE_CHANNEL_ID
        self.channel_secret = settings.LINE_CHANNEL_SECRET
        self.static_token = settings.LINE_CHANNEL_ACCESS_TOKEN
        
        # Token 緩存
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        
        # 驗證配置
        self._validate_config()
    
    def _validate_config(self):
        """驗證 LINE 配置是否完整"""
        if self.static_token:
            logger.info("使用靜態 Long-lived token（來自環境變數）")
            return
        
        if not self.channel_id or not self.channel_secret:
            logger.warning(
                "LINE_CHANNEL_ID 或 LINE_CHANNEL_SECRET 未設定。"
                "請在 .env 檔案中設定這些變數以使用動態 token。"
            )
    
    def get_token(self) -> str:
        """
        獲取有效的 Channel Access Token
        
        優先級：
        1. 如果設定了靜態 token（LINE_CHANNEL_ACCESS_TOKEN），直接使用
        2. 如果有緩存且未過期，使用緩存的 token
        3. 動態獲取新的 token
        
        Returns:
            str: 有效的 Channel Access Token
            
        Raises:
            ValueError: 當無法獲取 token 時
        """
        # 優先使用靜態 token
        if self.static_token:
            return self.static_token
        
        # 檢查緩存是否有效
        if self._is_token_valid():
            logger.debug("使用緩存的 access token")
            return self._access_token
        
        # 獲取新的 token
        logger.info("緩存的 token 已過期或不存在，正在獲取新的 token...")
        return self._fetch_new_token()
    
    def _is_token_valid(self) -> bool:
        """
        檢查緩存的 token 是否仍然有效
        
        Returns:
            bool: token 是否有效
        """
        if not self._access_token or not self._token_expires_at:
            return False
        
        # 提前 5 分鐘刷新，避免在使用時過期
        buffer_time = timedelta(minutes=5)
        return datetime.now() < (self._token_expires_at - buffer_time)
    
    def _fetch_new_token(self) -> str:
        """
        從 LINE API 獲取新的 Channel Access Token
        
        使用 OAuth 2.0 Client Credentials Flow
        
        Returns:
            str: 新的 access token
            
        Raises:
            ValueError: 當 API 請求失敗時
        """
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
    
    def refresh_token(self) -> str:
        """
        強制刷新 token（清除緩存並獲取新 token）
        
        Returns:
            str: 新的 access token
        """
        logger.info("強制刷新 access token")
        self._access_token = None
        self._token_expires_at = None
        return self._fetch_new_token()
    
    def get_token_info(self) -> dict:
        """
        獲取當前 token 的狀態資訊（用於調試）
        
        Returns:
            dict: token 狀態資訊
        """
        return {
            "using_static_token": bool(self.static_token),
            "has_cached_token": bool(self._access_token),
            "token_expires_at": self._token_expires_at.isoformat() if self._token_expires_at else None,
            "is_valid": self._is_token_valid(),
            "channel_id_set": bool(self.channel_id),
            "channel_secret_set": bool(self.channel_secret)
        }


# 創建全局單例
line_token_manager = LineTokenManager()
