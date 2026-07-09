from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, ReplyMessageRequest


class LineMessagingClient:
    def reply_message(self, access_token: str, request: ReplyMessageRequest) -> None:
        line_config = Configuration(access_token=access_token)
        with ApiClient(line_config) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(request)
