"""
搜尋結果的「誠實揭露」判定規則，供 LINE Flex 與 LIFF REST API 共用。

這裡只放**判定**，不放文案。兩個通道對同一件事必須有同一個門檻（否則 LINE 說
「資料有限」而 LIFF 什麼都不說，等於同一個系統對同一筆結果講兩套話），但呈現
方式必須各自決定：LINE 由伺服器組好中文字串直接送出，LIFF 則是把結構化事實回給
前端、由 react-i18next 用使用者在設定頁選的語言渲染。伺服器端的
`get_request_language()` 對 LIFF 請求沒有中介層設定，一律是 zh-TW，因此
在這裡組字串會讓非中文使用者永遠拿到中文副標。
"""

from app.services.medical.medical_service import (
    NEARBY_SEARCH_STEPS,
    NearbySearchResult,
)

# 藥局「查到了、但荒謬地遠」的判定門檻，沿用搜尋階梯的第一級（5 公里）。
# 為什麼是這個數字：藥局是「順路領藥、臨時買藥」的生活機能，超過 5 公里已不可能
# 是使用者心中「附近的藥局」；而第一級距內找得到就代表該地區的收錄密度正常，
# 唯有必須擴大到第一級之外才找得到，才說明結果是資料缺口撐出來的、而非地理事實。
# 直接綁定 NEARBY_SEARCH_STEPS[0] 而不另外寫死 5000，是為了讓門檻與搜尋階梯
# 的定義保持同一個來源，日後調整階梯時不會出現兩套互相矛盾的「附近」。
PHARMACY_DATA_GAP_METERS = NEARBY_SEARCH_STEPS[0]


def pharmacy_data_gap_meters(result: NearbySearchResult) -> float | None:
    """
    查到藥局、但最近一家已遠超生活圈時，回傳「最近一家的距離」；否則 None。

    為什麼需要：資料庫只收錄 116 家藥局，全台實際有數千家。實測台北車站查藥局
    會回傳 5 家、全部在 18 公里外，且因為湊滿了 5 筆而 satisfied=True，副標走
    「已擴大範圍找到 5 家」——使用者站在步行範圍內就有數十家藥局的地方，卻拿到
    一張看起來完全正常的卡片。「查無結果」的專屬文案只在 0 筆時觸發，涵蓋不到
    這個其實更常見的情境，因此另外補這條判定。

    回傳距離而非布林值，是為了讓呈現層能講出具體數字（「最近的在 18 公里外」）——
    只說「資料有限」使用者無從判斷要不要改用其他方式找藥局。
    """
    match = result.facility_type_match
    if match is None or match.category != "藥局" or not result.facilities:
        return None
    nearest = min((f.distance_meters or 0) for f in result.facilities)
    if nearest <= PHARMACY_DATA_GAP_METERS:
        return None
    return nearest
