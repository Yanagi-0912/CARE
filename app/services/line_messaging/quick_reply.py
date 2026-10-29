"""在既有快速回覆後附上功能導覽入口，保留當前操作的按鈕。"""

from linebot.v3.messaging import MessageAction, QuickReply, QuickReplyItem

from app.i18n import t
from app.services.line_messaging.feature_guide_intent import is_feature_guide_intent

MAX_QUICK_REPLY_ITEMS = 13


def with_feature_guide(
    quick_reply: QuickReply | None = None, language: str | None = None
) -> QuickReply:
    items = list(quick_reply.items or []) if quick_reply is not None else []
    # LINE 最多 13 顆；位置、走失確認等當前操作優先保留。
    items = items[:MAX_QUICK_REPLY_ITEMS]
    has_guide = any(
        isinstance(item.action, MessageAction) and is_feature_guide_intent(item.action.text)
        for item in items
    )
    if not has_guide and len(items) < MAX_QUICK_REPLY_ITEMS:
        label = t("welcome.quick.guide", language)
        items.append(QuickReplyItem(action=MessageAction(label=label, text=label)))
    return QuickReply(items=items)
