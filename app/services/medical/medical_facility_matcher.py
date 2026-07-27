# 這個檔案負責比對使用者輸入的醫療院所名稱與資料庫中的醫療院所名稱，並返回最相似的醫療院所資訊。

from app.schemas import MedicalFacility, ClinicDaySchedule
import re
import urllib.parse
FACILITY_SUFFIXES = ("醫院", "診所", "衛生所", "藥局", "藥房")
TYPE_KEYWORDS = ("診所", "醫院", "藥局")
WEEKDAY_LABELS = {
    "monday": "週一", "tuesday": "週二", "wednesday": "週三",
    "thursday": "週四", "friday": "週五", "saturday": "週六", "sunday": "週日",
}

# 精確縣市名稱清單，改用明確清單逐一比對前綴，確保只刪除確實屬於縣市行政名稱的部分。
_COUNTY_CITY_PREFIXES: tuple[str, ...] = (
    "臺北市", "台北市", "新北市", "桃園市", "臺中市", "台中市",
    "臺南市", "台南市", "高雄市", "基隆市", "新竹市", "嘉義市",
    "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣",
    "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "台東縣",
    "澎湖縣", "金門縣", "連江縣",
)

# 口語化地區名稱清單（不含縣/市字元），用於 find_facility_by_name 偵測地區前綴。
# 使用者輸入「花蓮中正診所」時，「花蓮」不觸發縣市前綴移除，
# 須透過此清單另行偵測，並補入 address 欄位篩選。
_INFORMAL_CITY_NAMES: tuple[str, ...] = (
    "臺北", "台北", "新北", "桃園", "臺中", "台中",
    "臺南", "台南", "高雄", "基隆", "新竹", "嘉義",
    "苗栗", "彰化", "南投", "雲林", "屏東", "宜蘭",
    "花蓮", "臺東", "台東", "澎湖", "金門", "連江",
)

"""
定義常見的醫療機構同義詞對照表（建議統一用正規化後的"臺"字做 Key）
原則1. 右方的value盡量言簡意賅(ex:馬偕紀念醫院 優於 臺灣基督長老教會馬偕醫療財團法人)
原則2. 右方的value請保持連續，避免regex比對出錯(ex:馬偕醫療財團法人 優於 馬偕財團法人)
原則3. 請查看資料庫內醫療院所名稱再決定 value，避免憑感覺寫
""" 

FACILITY_ALIASES = {
    "成大": "成功大學",
    "臺大": "臺灣大學",
    "高醫": "高雄醫學大學",
    "榮總": "榮民總醫院",
    "三總": "三軍總醫院",
    "長庚": "長庚醫療",
    "奇美": "奇美醫療",
    "慈濟": "慈濟醫療",
    "馬偕": "馬偕紀念醫院",
    
}
def normalize_facility_name(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    normalized = re.sub(r"[，,。．.？?！!：:；;「」『』()（）\[\]【】]", "", normalized)

    # 改用精確縣市清單移除前綴，避免廣義正則誤刪機構名中含「市」「縣」的部分
    for prefix in _COUNTY_CITY_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break

    # 統一將台轉為"臺"(因為資料庫內是用"臺")
    normalized = normalized.replace("台", "臺")

    # 移除院所類型後綴（如「醫院」「衛生所」），保留核心名稱關鍵字供比對。
    for suffix in FACILITY_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break

    # 移除殘留的鄉鎮市區後綴
    for suffix in ("鄉", "鎮", "市", "區"):
        if normalized.endswith(suffix) and len(normalized) > 1:
            normalized = normalized[:-1]
            break

    return normalized

def detect_type_keyword(text: str) -> str | None:
    for keyword in TYPE_KEYWORDS:
        if keyword in (text or ""):
            return keyword
    return None

def _build_text_map_url(facility: MedicalFacility) -> str:
    """專門給純文字格式化使用的地圖連結"""
    query = f"{facility.latitude},{facility.longitude}" if facility.latitude and facility.longitude else (facility.address or facility.name)
    return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(query)}"

def _build_text_tel_uri(phone: str | None) -> str | None:
    """專門給純文字格式化使用的電話 URI"""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return f"tel:{digits}" if len(digits) >= 6 else None

build_tel_uri = _build_text_tel_uri

def format_departments(departments: list[str] | None) -> str:
    if not departments:
        return "無資料"
    clean_departments = [item for item in departments if item]
    return "、".join(clean_departments) if clean_departments else "無資料"

def format_clinic_time(clinic_time: dict[str, ClinicDaySchedule] | None) -> str:
    if not clinic_time:
        return "無資料"
    parts: list[str] = []
    for day_key, label in WEEKDAY_LABELS.items():
        day = clinic_time.get(day_key)
        if day is None:
            continue
        if day.isClosed:
            parts.append(f"{label}：休診")
            continue
        # 讀取新 schema 的 slots 欄位，每個 slot 為 ClinicTimeSlot 物件（open/close）
        ranges = [
            f"{slot.open}-{slot.close}"
            for slot in day.slots
            if slot.open and slot.close
        ]
        if ranges:
            parts.append(f"{label}：{'、'.join(ranges)}")
        else:
            parts.append(f"{label}：無資料")
            
    if parts:
        return "\n".join(parts)
    return "無資料"


def format_facility_detail(facility: MedicalFacility) -> str:
    """輸出純文字詳細資訊"""
    tel_uri = _build_text_tel_uri(facility.phone)
    phone_text = facility.phone or "無資料"
    lines = [
        f"已為您查到：{facility.name}",
        f"類型：{facility.type or '無資料'}",
        f"地址：{facility.address or '無資料'}",
        f"電話：{phone_text}",
        f"營業時間：\n{format_clinic_time(facility.clinic_time)}",
        f"診療科別：{format_departments(facility.departments)}",
        f"地圖連結：{_build_text_map_url(facility)}",
    ]
    if tel_uri:
        lines.append(f"撥打電話：{tel_uri}")
    return "\n".join(lines)

def format_candidate_list(facilities: list[MedicalFacility], total_count: int, *, has_location: bool) -> str:
    lines = ["找到多筆相似院所，請選擇您要查詢的院所："]
    for index, facility in enumerate(facilities[:3], 1):
        distance = f"（距離約 {facility.distance_meters:.0f} 公尺）" if has_location and facility.distance_meters is not None else ""
        lines.append(f"{index}. {facility.name}{distance}\n   類型：{facility.type or '無資料'}\n   地址：{facility.address or '無資料'}")
    if total_count > 3:
        lines.append("結果超過 3 筆，您可以提供更明確的地區或完整名稱以縮小範圍。")
    return "\n".join(lines)