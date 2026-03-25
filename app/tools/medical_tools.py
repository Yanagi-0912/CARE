medical_function_declarations = [
    {
        "name": "request_location",
        "description": (
            "當使用者詢問「附近」的醫療院所/醫院/診所/藥局時，必須先呼叫此工具。"
            "此工具會要求用戶傳送目前的 GPS 位置，以便搜尋附近院所。"
            "在拿到座標之前，不要直接回傳院所清單或地址。"
            "適用情境範例：'附近有哪些醫院'、'我要找診所'、'最近的藥局在哪'。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},  # 無需參數，user_id 由系統層提供
        },
    },
    {
        "name": "find_nearby_hospitals",
        "description": (
            "當已取得用戶的 GPS 座標後，呼叫此工具搜尋附近的醫療院所。"
            "通常由系統在收到用戶的位置訊息後自動呼叫，不由用戶文字觸發。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "lat": {
                    "type": "number",
                    "description": "用戶位置的緯度座標，範圍 -90.0 到 90.0。",
                },
                "lng": {
                    "type": "number",
                    "description": "用戶位置的經度座標，範圍 -180.0 到 180.0。",
                },
            },
            "required": ["lat", "lng"],
        },
    },
]
