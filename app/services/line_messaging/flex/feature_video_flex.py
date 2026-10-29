"""歡迎卡某一頁的操作示範影片（按「看操作示範」後回的那一則）。

影片放在 hero，bubble 用 mega：LINE 規定影片只能放 hero、bubble 要是
kilo／mega／giga、而且不能在輪播裡。LINE 11.22.0 以前的版本不支援影片元件，
會改顯示 altContent——這裡放預覽圖，點下去用瀏覽器開 mp4，舊版一樣看得到。

自動播放看使用者的 LINE 設定（照片與影片 > 自動播放影片），電腦版不會自動播，
所以內文提示點影片播放。
"""

from typing import Any

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from app.services.line_messaging import feature_videos
from resources.flex_messages import theme


def build_feature_video_flex(
    section: str,
    base_url: str,
    language: str | None = None,
    font_size: str | None = None,
) -> FlexMessage:
    ft = theme.resolve_theme(font_size)
    video = feature_videos.video_url(base_url, language, section)
    preview = feature_videos.preview_url(base_url, language, section)
    title = t("feature_video.title", language).format(
        section=t(f"welcome.section.{section}.title", language)
    )

    bubble: dict[str, Any] = {
        "type": "bubble",
        "size": "mega",
        "hero": {
            "type": "video",
            "url": video,
            "previewUrl": preview,
            "aspectRatio": feature_videos.ASPECT_RATIO,
            "altContent": {
                "type": "image",
                "url": preview,
                "size": "full",
                "aspectRatio": feature_videos.ASPECT_RATIO,
                "aspectMode": "cover",
                "action": {"type": "uri", "uri": video},
            },
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "lg",
            "spacing": "sm",
            "backgroundColor": theme.SURFACE,
            "contents": [
                {
                    "type": "text",
                    "text": title,
                    "size": ft.body,
                    "weight": "bold",
                    "color": theme.TEXT,
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": t("feature_video.hint", language),
                    "size": ft.caption,
                    "color": theme.TEXT_MUTED,
                    "wrap": True,
                },
            ],
        },
    }
    return FlexMessage(altText=title, contents=FlexContainer.from_dict(bubble))
