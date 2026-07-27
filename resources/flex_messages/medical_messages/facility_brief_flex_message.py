"""
在這裡包裝醫療院所資訊成flex message,套用的flex message模板是醫療院所
的簡略資訊(名稱、距離、地址、地圖按鈕、電話按鈕)，不包含診療科別、營業時間等詳細資訊。

"""

import re
import urllib.parse
from typing import Any
from app.schemas import MedicalFacility


def _build_flex_map_uri(facility: MedicalFacility) -> str:
    """生成最符合 LINE 導航按鈕規格的 Google Map 連結"""
    # 優先級:經緯度->名稱->地址
    if facility.latitude and facility.longitude:
        query = f"{facility.latitude},{facility.longitude}"
    elif facility.name:
        query = facility.name
    elif facility.address:
        query = facility.address
    else:
        query = "醫療院所"

    encoded_query = urllib.parse.quote(query)
    # 設定dir直接導航到該地點， travelmode=driving 預設為開車
    return f"https://www.google.com/maps/dir/?api=1&destination={encoded_query}&travelmode=driving"


def _build_flex_tel_uri(phone: str | None) -> str:
    """處理 Flex Message 撥號按鈕專用的電話格式"""
    if not phone:
        return "tel:"
    digits = re.sub(r"\D", "", phone)
    return f"tel:{digits}" if len(digits) >= 6 else "tel:"


def create_facility_item_box(facility: MedicalFacility) -> dict[str, Any]:
    """建立單一醫療院所的 Flex Message Box 結構"""
    dist_text = (
        f"距離 {facility.distance_meters:.0f} 公尺"
        if facility.distance_meters is not None
        else "距離未知"
    )

    # 呼叫自己內部定義的 UI 專用 URL 函數
    map_uri = _build_flex_map_uri(facility)

    # 1. 建立必定會顯示的「前往地圖」按鈕 Box
    map_button_box = {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#E0E0E0",
        "cornerRadius": "md",
        "paddingAll": "md",
        "flex": 1,
        "action": {
            "type": "uri",
            "label": "前往地圖",
            "uri": map_uri,
        },
        "contents": [
            {
                "type": "text",
                "text": "前往地圖",
                "color": "#111111",
                "weight": "bold",
                "size": "20px",
                "align": "center",
            }
        ],
    }

    # 2. 宣告一個按鈕列表容器，先把地圖按鈕放進去
    buttons_contents = [map_button_box]

    # 3.只有當電話存在，且經過清洗後是有效號碼（長度 >= 6）時，才加入電話按鈕
    if facility.phone:
        tel_uri = _build_flex_tel_uri(facility.phone)
        if tel_uri != "tel:":
            tel_button_box = {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#2E7D32",
                "cornerRadius": "md",
                "paddingAll": "md",
                "flex": 1,
                "action": {
                    "type": "uri",
                    "label": "📞 撥打電話",
                    "uri": tel_uri,
                },
                "contents": [
                    {
                        "type": "text",
                        "text": "📞 撥打電話",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "20px",
                        "align": "center",
                    }
                ],
            }
            # 有電話按鈕時，將它放到地圖按鈕的前面（維持你原本電話在左、地圖在右的順序）
            buttons_contents.insert(0, tel_button_box)

    return {
        "type": "box",
        "layout": "vertical",
        "margin": "xxl",
        "spacing": "md",
        "action": {
            "type": "postback",  # postback action 會在使用者點擊時，將資料傳回你的 webhook，方便後續回船對應院所的flex message
            "label": "查看詳情",
            "data": f"action=view_facility_detail&facility_id={facility.id}",
            "displayText": f"查看 {facility.name or '該院所'} 的詳細資訊",
        },
        "contents": [
            {
                "type": "text",
                "text": facility.name or "未知名稱",
                "weight": "bold",
                "wrap": True,
                "size": "xxl",
                "color": "#111111",
            },
            {
                "type": "text",
                "text": dist_text,
                "size": "20px",
                "weight": "bold",
                "color": "#1B5E20",
            },
            {
                "type": "text",
                "text": facility.address or "暫無地址資訊",
                "wrap": True,
                "size": "20px",
                "color": "#333333",
            },
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "md",
                "margin": "lg",
                "contents": buttons_contents,  # 這裡帶入動態組好的按鈕列表
            },
        ],
    }


def generate_facility_list_flex_message(
    facilities: list[MedicalFacility], total_count: int | None = None
) -> dict[str, Any]:
    """
    根據醫療院所列表，動態渲染完整的 LINE Flex Message 物件結構 (含 Wrapper)。

    total_count 為 None 時，視為「附近搜尋」情境，標題固定顯示「附近醫療院所」。
    total_count 有值時，視為「名稱查詢候選清單」情境，標題改為「找到多筆相似院所」，
    並在 total_count 大於實際顯示筆數時，於卡片列表末端加入提示文字。
    """
    is_candidate_list = total_count is not None
    # 這裡的 is_candidate_list 變數用來判斷是否為候選清單情境，影響標題與提示文字的顯示
    if is_candidate_list:
        title_text = "找到多筆相似院所"
        subtitle_text = f"為您找到 {len(facilities)} 間相似院所，點擊查看詳細資訊"
    else:
        title_text = "附近醫療院所"
        subtitle_text = f"為您找到附近 {len(facilities)} 間醫療院所，點擊查看詳細資訊"

    contents: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": title_text,
            "weight": "bold",
            "size": "4xl",
            "wrap": True,
            "color": "#111111",
        },
        {
            "type": "text",
            "wrap": True,
            "text": subtitle_text,
            "color": "#333333",
            "size": "24px",
        },
        {
            "type": "separator",
            "margin": "md",
            "color": "#CCCCCC",
        },
    ]

    for idx, facility in enumerate(facilities):
        contents.append(create_facility_item_box(facility))
        if idx < len(facilities) - 1:
            contents.append(
                {
                    "type": "separator",
                    "margin": "xxl",
                    "color": "#DDDDDD",
                }
            )

    # 候選清單情境下，若總筆數超過本次顯示筆數，於列表末端補上提示文字
    if is_candidate_list and total_count > len(facilities):
        contents.append(
            {
                "type": "separator",
                "margin": "xxl",
                "color": "#DDDDDD",
            }
        )
        contents.append(
            {
                "type": "text",
                "text": "結果超過顯示上限，您可以提供更明確的地區或完整名稱以縮小範圍。",
                "wrap": True,
                "size": "20px",
                "color": "#777777",
                "margin": "xxl",
            }
        )

    return {
        "type": "flex",
        "altText": "醫療院所查詢結果",
        "contents": {
            "type": "bubble",
            "size": "giga",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "xl",
                "contents": contents,
            },
        },
    }
