from langchain_core.tools import tool
from app.services.medical.medical_service import (
    MedicalService,
    NO_FACILITY_MESSAGE,
    format_facility_list,
)

_medical_service: MedicalService | None = None


def configure_medical_tools(medical_service: MedicalService) -> None:
    """DI 初始化時呼叫，注入 MedicalService 實例。"""
    global _medical_service
    _medical_service = medical_service





@tool
async def find_nearby_hospitals(lat: float, lng: float) -> str:
    """
    當已取得用戶的 GPS 座標後，呼叫此工具搜尋附近的醫療院所。
    通常由系統在收到用戶的位置訊息後自動呼叫，不由用戶文字觸發。
    """
    if _medical_service is None:
        return "醫療服務未初始化，請稍後再試。"

    facilities = await _medical_service.find_nearby_hospitals(lat, lng)
    if not facilities:
        return NO_FACILITY_MESSAGE

    return format_facility_list(facilities)


@tool
async def request_location_quick_reply() -> str:
    """
    當使用者想要尋找、前往、或詢問醫療院所/醫院/診所/藥局的位置，
    且我們尚未取得其經緯度座標時，呼叫此工具以引導使用者傳送其當前位置。
    """
    return "請點擊下方的『分享位置資訊』按鈕傳送您的位置，我馬上為您尋找附近的醫療院所！"
