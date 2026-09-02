"""非處方藥加入提醒後，通知家人的卡片。

與 `safety_flex.build_family_alert_flex` 是同一條通道上的兩張卡：那張處理
「聊天中提到了高風險藥品」，這張處理「把一個不用處方就能買到的藥加進了提醒」。

## altText 為什麼不帶藥名與用途

altText 就是通知列與鎖定畫面上顯示的那一行，可能被非預期的人看到。藥名與用途
留在卡片內容裡——收件人已由通知政策收斂為 GUARDIAN／CAREGIVER，他們依授權矩陣
本來就看得到 SENSITIVE 資料，但「看得到」與「顯示在鎖定畫面上」不是同一件事。

這也是本檔案與 `safety_flex` 該檔頂端那條「`Medication.indication` 不進推播」
慣例的分界：那條慣例真正要擋的是「病情細節出現在不受控的顯示面」。把它分成
altText（不帶）與卡片內容（帶）兩層之後，用途可以送到有權看的人手上，而不會
在鎖定畫面上洩漏。
"""

from typing import Any, Optional

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from resources.flex_messages import theme


def _row(label: str, value: str, ft) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "spacing": "none",
        "margin": "md",
        "contents": [
            {
                "type": "text",
                "text": label,
                "size": ft.caption,
                "color": theme.TEXT_MUTED,
                "wrap": True,
            },
            {
                "type": "text",
                "text": value,
                "size": ft.body,
                "color": theme.TEXT,
                "wrap": True,
            },
        ],
    }


def build_otc_family_flex(
    patient_name: str,
    drug_name: str,
    indication: Optional[str] = None,
    existing_drug_name: Optional[str] = None,
    shared_ingredients: tuple[str, ...] = (),
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> FlexMessage:
    """非處方藥通知卡。

    有 `existing_drug_name` 與 `shared_ingredients` 就是「成分重複」版，否則是
    「新增了一個非處方藥」版——兩者共用同一張卡而不是拆成兩支，因為它們的差別
    只在多出兩列與換一句結語；拆開會讓版面在兩處各自漂移。

    語言與字級取的是**收件家人本人**的設定，不是當事人的，比照
    `build_family_alert_flex`。背景推播沒有 request context 可繼承。
    """
    ft = theme.resolve_theme(font_size)
    is_overlap = bool(existing_drug_name and shared_ingredients)
    suffix = "overlap" if is_overlap else "added"

    body_contents: list[dict[str, Any]] = [
        {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": theme.SURFACE_ALT,
            "cornerRadius": "md",
            "paddingAll": "lg",
            "spacing": "xs",
            "contents": [
                {
                    "type": "text",
                    "text": patient_name,
                    "weight": "bold",
                    "size": ft.title,
                    "color": theme.TEXT,
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": t(f"flex.otc.intro.{suffix}", language),
                    "size": ft.body,
                    "color": theme.TEXT_MUTED,
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": drug_name,
                    "weight": "bold",
                    "size": ft.body,
                    "color": theme.TEXT,
                    "wrap": True,
                    "margin": "sm",
                },
            ],
        }
    ]

    if indication:
        body_contents.append(_row(t("flex.otc.label.indication", language), indication, ft))

    if is_overlap:
        body_contents.append(
            _row(t("flex.otc.label.existing", language), existing_drug_name or "", ft)
        )
        body_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "spacing": "none",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": t("flex.otc.label.shared", language),
                        "size": ft.caption,
                        "color": theme.TEXT_MUTED,
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        # 成分是英文學名，刻意不翻譯也不改寫：家人若要拿去問
                        # 藥師或查資料，學名才是共通的那個詞。
                        "text": "、".join(shared_ingredients),
                        "size": ft.body,
                        "color": theme.STATUS_CLOSED,
                        "weight": "bold",
                        "wrap": True,
                    },
                ],
            }
        )

    body_contents.append(
        {
            "type": "text",
            "text": t(f"flex.otc.please_check.{suffix}", language),
            "size": ft.body,
            "color": theme.TEXT_MUTED,
            "wrap": True,
            "margin": "md",
        }
    )

    bubble_dict = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": theme.BRAND,
            "paddingAll": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": t(f"flex.otc.header.{suffix}", language),
                    "color": theme.TEXT_ON_BRAND,
                    "weight": "bold",
                    "size": ft.heading,
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "xl",
            "backgroundColor": theme.SURFACE,
            "spacing": "md",
            "contents": body_contents,
        },
    }

    return FlexMessage(
        altText=t(f"flex.otc.alt.{suffix}", language).format(name=patient_name),
        contents=FlexContainer.from_dict(bubble_dict),
    )
