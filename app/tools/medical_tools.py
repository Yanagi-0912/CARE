from langchain_core.tools import tool
from app.application.medical.medical_service import (
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
def request_location() -> str:
    """
    當使用者想要尋找、前往、或詢問醫療院所/醫院/診所/藥局的位置時，必須先呼叫此工具。
    此工具會要求用戶傳送目前的 GPS 位置，以便搜尋附近院所。
    在拿到座標之前，不要直接回傳院所清單或地址，也不要用文字叫使用者傳位置。
    適用情境範例：'附近有哪些醫院'、'我要去醫院'、'最近的藥局在哪'、
    '幫我找診所'、'推薦醫院'、'我要看醫生'。
    """
    # 回傳特殊標記，讓上層知道要發送要求位置的 Quick Reply
    return "__REQUEST_LOCATION__"


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
