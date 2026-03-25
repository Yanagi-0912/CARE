from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, ReplyMessageRequest
#先匯入line的 sdk

class LineMessagingClient: # 讓 message_service 不需要直接呼叫 line的 sdk
    def reply_message(self, access_token: str, request: ReplyMessageRequest) -> None:
        line_config = Configuration(access_token=access_token) #把 token 塞進 sdk config
        with ApiClient(line_config) as api_client: #建立 line api client
            line_bot_api = MessagingApi(api_client) #建立物件
            line_bot_api.reply_message(request)

#整個 client 只管 line sdk 怎麼算出去
# 在 message_service 管要回啥