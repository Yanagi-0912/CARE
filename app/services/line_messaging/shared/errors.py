class LineError(Exception):
    """LINE 模組基底例外。"""


class LineValidationError(LineError):
    """輸入或 webhook 事件欄位驗證失敗。"""


class LineTokenError(LineError):
    """Channel access token 取得失敗（設定、HTTP 或回應格式）。"""
