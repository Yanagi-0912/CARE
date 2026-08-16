import logging
from typing import Any, NamedTuple, Optional, Union

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from app.models.medication import SLOT_DISPLAY_NAMES
from resources.flex_messages import theme

logger = logging.getLogger(__name__)


def get_slot_display_name(slot_type: str, language: str | None = None) -> str:
    """取得時段的在地化名稱；未知時段回退為原始值。"""
    if slot_type not in SLOT_DISPLAY_NAMES:
        return slot_type
    return t(f"slot.{slot_type}", language)


def _header(label: str, ft: theme.FlexTheme, background: str = theme.BRAND) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": background,
        "paddingAll": "lg",
        "contents": [
            {
                "type": "text",
                "text": label,
                "color": theme.TEXT_ON_BRAND,
                "weight": "bold",
                "size": ft.heading,
                "wrap": True,
            }
        ],
    }


def _slot_block(
    slot_name: str, scheduled_time: str, ft: theme.FlexTheme, language: str | None
) -> dict[str, Any]:
    """時段與時間的重點區塊，是使用者最需要一眼看到的資訊。"""
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.BRAND_TINT,
        "cornerRadius": "md",
        "paddingAll": "lg",
        "spacing": "xs",
        "contents": [
            {
                "type": "text",
                "text": slot_name,
                "weight": "bold",
                "size": ft.title,
                "color": theme.BRAND_DARK,
                "wrap": True,
            },
            {
                "type": "text",
                "text": t("flex.med.scheduled_at", language).format(
                    time=scheduled_time
                ),
                "size": ft.body,
                "color": theme.BRAND_DARK,
                "wrap": True,
            },
        ],
    }


# 藥品清單顯示上限。一次藥袋掃描可能辨識出十幾種藥，Flex 有大小上限，使用者在
# 推播通知列也不會逐行細讀。超過的部分收斂成一行計數，形狀比照
# MISSED_SUMMARY_MAX_ROWS 的做法，故意不另創一套截斷邏輯。
MEDICATION_LIST_MAX_ITEMS = 5


class MedicationListEntry(NamedTuple):
    """藥品清單一列的資料：藥名，加上可選的縮圖 URL。

    `image_url` 有值時該列呈現「縮圖＋藥名」，None 時維持純文字列——是否有值
    完全由呼叫端決定，這裡不做任何「該不該顯示照片」的判斷。呼叫端（排程器的
    `_TickMedicationNameCache`）只在藥品 `license_number` 已確定且落地縮圖存在
    時才會帶入非 None 的 `image_url`（spec「證號不確定時不得顯示藥丸照片」）；
    這支模組收到什麼就照樣呈現，不重複做這個把關，避免把關邏輯分散在兩處。
    """

    name: str
    image_url: Optional[str] = None


# 呼叫端可以直接傳純字串（既有呼叫方式，等同 image_url=None，家屬卡片與
# 「已完成」卡片的既有呼叫點都還是這樣傳），也可以傳 MedicationListEntry
# 帶入縮圖 URL。兩者在同一個清單中混用是常態而非例外：同一時段的藥有的
# 證號已確定、有的還沒（spec「同時段圖文混排」）。
MedicationListItem = Union[str, MedicationListEntry]


def _as_entry(item: MedicationListItem) -> MedicationListEntry:
    return item if isinstance(item, MedicationListEntry) else MedicationListEntry(name=item)


def _medication_list_rows(
    items: list[MedicationListItem], language: str | None
) -> list[MedicationListEntry]:
    """把藥品清單收斂成顯示用的列；超過上限時最後一行收斂為單行純文字計數。"""
    entries = [_as_entry(item) for item in items]
    shown = entries[:MEDICATION_LIST_MAX_ITEMS]
    rows = list(shown)
    remaining = len(entries) - len(shown)
    if remaining > 0:
        # 收斂後的計數行只是一句提示文字，不代表任何一張藥證，不該也不能帶縮圖。
        rows.append(
            MedicationListEntry(
                name=t("flex.med.medication_list_more", language).format(count=remaining)
            )
        )
    return rows


def _medication_row_node(row: MedicationListEntry, ft: theme.FlexTheme) -> dict[str, Any]:
    """單一藥品列的 Flex 節點：沒有縮圖時是純文字列（結構與本功能導入前逐位元組
    相同），有縮圖時外包一層水平排列的 box，縮圖與藥名並排。
    """
    text_node = {
        "type": "text",
        "text": row.name,
        "size": ft.body,
        "color": theme.TEXT_MUTED,
        "wrap": True,
    }
    if not row.image_url:
        return text_node
    return {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "alignItems": "center",
        "contents": [
            {
                "type": "image",
                "url": row.image_url,
                "size": "xxs",
                "aspectMode": "cover",
                "aspectRatio": "1:1",
                "flex": 0,
            },
            text_node,
        ],
    }


def _medication_list_block(
    medication_names: Optional[list[MedicationListItem]],
    ft: theme.FlexTheme,
    language: str | None,
    heading_key: str = "flex.med.medication_list_heading",
) -> Optional[dict[str, Any]]:
    """服藥提醒／已完成／二次催促／家屬警報文案共用的藥品清單區塊。

    `medication_names` 為 None 或空清單時回傳 None，呼叫端據此完全不插入這個
    區塊——既有規則的 medication_ids 皆為空陣列，版面必須與本變更前逐一致，
    不能出現空白的藥品區塊或只有標題沒有內容的殘影。
    每一列只放藥名（可選再加縮圖）：適應症等其他欄位由呼叫端在解析階段就已經
    濾除，這裡不重複把關。

    `heading_key` 讓四張卡片共用同一套排版與截斷規則，只換標題的時態與語氣
    （應服／已服用／尚未服用）。刻意不為此另開三份幾乎相同的 builder：藥名
    的顯示上限與收斂形狀只該有一個定義，否則往後改上限時必漏改其中一處。
    """
    if not medication_names:
        return None
    rows = _medication_list_rows(medication_names, language)
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.SURFACE_ALT,
        "cornerRadius": "md",
        "paddingAll": "lg",
        "spacing": "xs",
        "margin": "md",
        "contents": [
            {
                "type": "text",
                "text": t(heading_key, language),
                "weight": "bold",
                "size": ft.body,
                "color": theme.TEXT,
                "wrap": True,
            },
            *[_medication_row_node(row, ft) for row in rows],
        ],
    }


def _body(contents: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "xl",
        "backgroundColor": theme.SURFACE,
        "spacing": "md",
        "contents": contents,
    }


def _footer(button: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "lg",
        "contents": [button],
    }


def _paragraph(text: str, ft: theme.FlexTheme, color: str = theme.TEXT_MUTED, **extra) -> dict[str, Any]:
    return {
        "type": "text",
        "text": text,
        "size": ft.body,
        "color": color,
        "wrap": True,
        **extra,
    }


def build_patient_medication_flex(
    log_id: str,
    slot_type: str,
    scheduled_time: str,
    disabled: bool = False,
    taken_at_str: Optional[str] = None,
    medication_names: Optional[list[MedicationListItem]] = None,
    language: str | None = None,
    font_size: str | None = None,
) -> FlexMessage:
    """
    建立傳送給用藥者的服藥提醒 Flex Message。
    - disabled=False: 顯示【我已用藥】可點擊按鈕
    - disabled=True: 顯示已完成的停用狀態 (點擊後動態替換)

    `medication_names` 兩種狀態都會呈現，只換標題的時態（應服／已服用）。
    已完成的卡片同樣列出藥名，是因為它是使用者事後唯一能回頭查的憑據——
    只寫「感謝您的紀錄」而不寫吃了什麼，等於把提醒卡上有的資訊在確認後就
    丟掉，家屬與使用者都無從核對那一次到底服了哪幾種藥。
    規則沒有關聯藥品、或關聯的藥品皆已失效時傳入 None／空清單，版面與本參數
    新增前完全相同。
    """
    ft = theme.resolve_theme(font_size)
    slot_name = get_slot_display_name(slot_type, language)

    if not disabled:
        alt_text = t("flex.med.alt.reminder", language).format(slot=slot_name)
        taken_label = t("flex.med.button.taken", language)
        body_contents = [_slot_block(slot_name, scheduled_time, ft, language)]
        med_block = _medication_list_block(medication_names, ft, language)
        if med_block is not None:
            body_contents.append(med_block)
        body_contents.append(
            _paragraph(t("flex.med.instruction", language), ft, margin="md")
        )
        bubble_dict = {
            "type": "bubble",
            "header": _header(t("flex.med.header.reminder", language), ft),
            "body": _body(body_contents),
            "footer": _footer(
                ft.primary_button(
                    taken_label,
                    {
                        "type": "postback",
                        "label": taken_label,
                        "data": f"action=confirm_medication&log_id={log_id}",
                        "displayText": t("flex.med.display.taken", language),
                    },
                )
            ),
        }
    else:
        alt_text = t("flex.med.alt.done", language).format(slot=slot_name)
        completion_text = (
            t("flex.med.done_at", language).format(time=taken_at_str)
            if taken_at_str
            else t("flex.med.done", language)
        )
        body_contents = [_slot_block(slot_name, scheduled_time, ft, language)]
        med_block = _medication_list_block(
            medication_names,
            ft,
            language,
            heading_key="flex.med.medication_list_heading_done",
        )
        if med_block is not None:
            body_contents.append(med_block)
        body_contents.append(
            _paragraph(t("flex.med.thanks", language), ft, margin="md")
        )
        bubble_dict = {
            "type": "bubble",
            "header": _header(
                t("flex.med.header.done", language), ft, background=theme.STATUS_UNKNOWN
            ),
            "body": _body(body_contents),
            "footer": _footer(
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": theme.NEUTRAL_BG,
                    "cornerRadius": "md",
                    "paddingAll": "lg",
                    "contents": [
                        {
                            "type": "text",
                            "text": completion_text,
                            "color": theme.TEXT_FAINT,
                            "weight": "bold",
                            "size": ft.button,
                            "align": "center",
                            "wrap": True,
                        }
                    ],
                }
            ),
        }

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(altText=alt_text, contents=container)


def build_patient_urgent_reminder_flex(
    log_id: str,
    slot_type: str,
    scheduled_time: str,
    medication_names: Optional[list[MedicationListItem]] = None,
    language: str | None = None,
    font_size: str | None = None,
) -> FlexMessage:
    """T+20min 傳送給用藥者的二次催促 Flex Message

    `medication_names` 為 None／空清單時版面與本參數新增前完全相同，見
    `_medication_list_block`。
    """
    ft = theme.resolve_theme(font_size)
    slot_name = get_slot_display_name(slot_type, language)
    taken_label = t("flex.med.button.taken", language)

    body_contents = [_slot_block(slot_name, scheduled_time, ft, language)]
    med_block = _medication_list_block(medication_names, ft, language)
    if med_block is not None:
        body_contents.append(med_block)
    body_contents.append(
        _paragraph(t("flex.med.urgent_body", language), ft, margin="md")
    )

    bubble_dict = {
        "type": "bubble",
        "header": _header(
            t("flex.med.header.urgent", language), ft, background=theme.STATUS_CLOSED
        ),
        "body": _body(body_contents),
        "footer": _footer(
            ft.primary_button(
                taken_label,
                {
                    "type": "postback",
                    "label": taken_label,
                    "data": f"action=confirm_medication&log_id={log_id}",
                    "displayText": t("flex.med.display.taken", language),
                },
            )
        ),
    }

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(
        altText=t("flex.med.alt.urgent", language).format(slot=slot_name),
        contents=container,
    )


def build_caregiver_alert_flex(
    patient_name: str,
    slot_type: str,
    scheduled_time: str,
    medication_names: Optional[list[str]] = None,
    language: str | None = None,
    font_size: str | None = None,
) -> FlexMessage:
    """T+30min 傳送給通報對象家屬的逾時未用藥關心 Flex Message

    `medication_names` 列出這個時段漏掉的是哪幾種藥。收件人是規則的建立者
    （`alert_notify_user_id` 取自 `reminder.creator_user_id`），也就是當初替家人
    設定這些藥的人，藥名對他不是新揭露的資訊；少了它，警報只說得出「某人某個
    時段沒吃藥」，家屬無從判斷這次漏掉的是保養用藥還是不能斷的處方。
    仍然只放藥名——適應症不得進入任何推播訊息（見 `Medication.indication`）。
    為 None／空清單時版面與本參數新增前完全相同，見 `_medication_list_block`。
    """
    ft = theme.resolve_theme(font_size)
    slot_name = get_slot_display_name(slot_type, language)

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
                    "text": f"{slot_name}　{scheduled_time}",
                    "size": ft.body,
                    "color": theme.TEXT_MUTED,
                    "wrap": True,
                },
            ],
        },
        _paragraph(
            t("flex.med.overdue", language),
            ft,
            color=theme.STATUS_CLOSED,
            weight="bold",
            margin="md",
        ),
    ]
    med_block = _medication_list_block(
        medication_names,
        ft,
        language,
        heading_key="flex.med.medication_list_heading_missed",
    )
    if med_block is not None:
        body_contents.append(med_block)
    body_contents.append(_paragraph(t("flex.med.please_care", language), ft))

    bubble_dict = {
        "type": "bubble",
        "header": _header(t("flex.med.header.caregiver", language), ft),
        "body": _body(body_contents),
    }

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(
        altText=t("flex.med.alt.caregiver", language).format(name=patient_name),
        contents=container,
    )


# 一次中斷可能累積很多時段（停機 6 小時 × 多位家人），Flex 有大小上限，
# 家屬也不會逐行讀。超過的部分收斂成一行「另有 N 個時段」。
MISSED_SUMMARY_MAX_ROWS = 10


def build_caregiver_missed_summary_flex(
    missed: list[dict[str, str]],
    language: str | None = None,
    font_size: str | None = None,
) -> FlexMessage:
    """
    系統中斷期間錯過的時段，彙整成一則傳送給家屬的通知。

    `missed` 每筆需含 `patient_name`、`slot_type`、`scheduled_time`；同一位家屬照顧
    多人時會依 `patient_name` 分組。措辭與 T+30 逾時警報刻意分開：那則說的是
    「家人逾時未服藥」，這則說的是「我們沒能發出提醒，所以無法確認服藥與否」。
    """
    ft = theme.resolve_theme(font_size)
    total = len(missed)
    shown = missed[:MISSED_SUMMARY_MAX_ROWS]

    grouped: dict[str, list[str]] = {}
    for entry in shown:
        name = entry.get("patient_name") or "成員"
        slot_name = get_slot_display_name(entry.get("slot_type", ""), language)
        grouped.setdefault(name, []).append(
            f"{slot_name}　{entry.get('scheduled_time', '')}"
        )

    patient_blocks: list[dict[str, Any]] = []
    for name, rows in grouped.items():
        patient_blocks.append(
            {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": theme.SURFACE_ALT,
                "cornerRadius": "md",
                "paddingAll": "lg",
                "spacing": "xs",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": name,
                        "weight": "bold",
                        "size": ft.title,
                        "color": theme.TEXT,
                        "wrap": True,
                    },
                    *[
                        {
                            "type": "text",
                            "text": row,
                            "size": ft.body,
                            "color": theme.TEXT_MUTED,
                            "wrap": True,
                        }
                        for row in rows
                    ],
                ],
            }
        )

    if total > len(shown):
        patient_blocks.append(
            _paragraph(
                t("flex.med.missed_summary_more", language).format(
                    count=total - len(shown)
                ),
                ft,
                color=theme.TEXT_FAINT,
                margin="sm",
            )
        )

    bubble_dict = {
        "type": "bubble",
        "header": _header(
            t("flex.med.header.missed_summary", language),
            ft,
            background=theme.STATUS_UNKNOWN,
        ),
        "body": _body(
            [
                _paragraph(t("flex.med.missed_summary_body", language), ft),
                *patient_blocks,
                _paragraph(
                    t("flex.med.missed_summary_hint", language),
                    ft,
                    color=theme.TEXT,
                    weight="bold",
                    margin="md",
                ),
            ]
        ),
    }

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(
        altText=t("flex.med.alt.missed_summary", language).format(
            name=next(iter(grouped), "成員"), count=total
        ),
        contents=container,
    )
