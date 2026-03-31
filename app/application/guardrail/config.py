CLASSIFICATION_PROMPT = (
    "你是一個訊息分類器。請判斷以下使用者訊息是否與「健康、醫療、身體狀況、疾病、藥物、營養、運動健身、心理健康」相關。\n\n"
    "使用者訊息：\n"
)

CLASSIFICATION_GENERATION_CONFIG = {
    "temperature": 0.1,
    "maxOutputTokens": 200,
    "responseMimeType": "application/json",
    "responseSchema": {
        "type": "object",
        "properties": {
            "is_health_related": {
                "type": "boolean",
            }
        },
        "required": ["is_health_related"],
    },
}
