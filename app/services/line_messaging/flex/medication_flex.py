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


# 用藥提醒拉霸的語氣（MedicationLog.reminder_tone，見
# app/services/medication/reminder_variants.py）→ 說明句的 i18n key。這裡刻意用
# 字面值而不 import 拉霸模組：卡片是顯示層，不該反過來依賴排程與學習的邏輯；兩邊
# 的名稱由 test_medication_flex_tones.py 交叉檢查。不在表裡的語氣（包含 control，
# 以及之後拿掉的選項）一律用現行文字。
_INSTRUCTION_KEYS = {
    "family": "flex.med.instruction.family",
    "brief": "flex.med.instruction.brief",
}
_URGENT_BODY_KEYS = {
    "family": "flex.med.urgent_body.family",
    "brief": "flex.med.urgent_body.brief",
}


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


class MedicationGroup(NamedTuple):
    """一個服藥時機（飯前／飯後／無關聯）分區的推播資料，供 T+0／T+20 卡片與
    `MedicationService.medication_groups_for_log` 共用（見 design 決策 6）。

    `items` 是 `(medication_id, entry)` 的清單——postback 的
    `confirm_medication` 需要藥品 id 才能寫入 `taken_medication_ids`，
    `MedicationListEntry` 本身只有藥名與縮圖，不足以組出確認按鈕。

    暫時定義在這裡（`app/services/medication/` 的匯入者）而不是
    `app/services/medication/medication_groups.py`：後者會與這個模組互相
    import（flex 組裝需要 `MedicationGroup`，`MedicationService` 也需要），
    放在 flex 這一側、由 service 單向匯入可以先避開這個循環。這不是長久
    的分層——`MedicationGroup` 本質上是 medication 領域的資料形狀，被塞進
    flex 模組只是圖眼前方便；之後若要根除這個循環，該做的是把它（連同
    `MedicationListEntry`）搬到一個兩邊都能匯入、沒有既有依賴方向的中立
    模組（例如 `app/models/` 或 `app/services/medication/` 底下新開一個
    不被 flex 依賴的檔案），flex 這一側改成單向匯入它，而不是繼續留在這裡。
    """

    meal_timing: str
    scheduled_time: str
    items: list[tuple[str, MedicationListEntry]]


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
    相同），有縮圖時外包一層垂直排列的 box，照片一行、藥名一行。

    排列方向刻意是垂直而非並排。bubble 預設是 mega（約 300px 寬），扣掉 body
    與藥品區塊各自的 padding 之後，一列真正可用的寬度只剩約 220px；照片放到
    160px（見 theme._SIZE_SCALE 的 "thumbnail"）還要與藥名並排時，藥名只剩
    50 幾 px，長藥名會被擠成一行兩三個字的細長條——那比縮圖太小更難讀。上下
    排列讓兩者各自拿到整列寬度。

    代價是 bubble 變高（最多 5 筆，見 MEDICATION_LIST_MAX_ITEMS）。這是為了
    「看得清楚手上這顆藥」而接受的取捨：認不出藥的時候，一則精簡但看不清的
    提醒沒有任何用處。
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
        "layout": "vertical",
        "spacing": "sm",
        "margin": "sm",
        "contents": [
            {
                "type": "image",
                "url": row.image_url,
                # 縮圖尺寸跟字級一起放大（見 theme._SIZE_SCALE 的 "thumbnail"
                # 項）：本功能靠外觀認藥，字級調大的長輩不該仍被鎖在固定的
                # 最小尺寸，那樣藥名變大、照片卻原地不動。
                "size": ft.thumbnail,
                # 來源檔已經是等比縮放後置中補白的 160×160 正方形
                # （build_drug_catalog.py 的 -gravity center -extent），
                # 1:1 的框裡 cover 與 contain 等價，不會裁掉保留尺規的留白邊。
                "aspectMode": "cover",
                "aspectRatio": "1:1",
                # 照片與下方藥名共用同一條左邊界；預設的 center 會讓兩者對不齊。
                "align": "start",
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


def _group_heading_node(
    group: MedicationGroup, ft: theme.FlexTheme, language: str | None
) -> dict[str, Any]:
    """一個服藥時機分區的小標，例如「飯前　07:30」（design 決策 6）。

    全型空格與家屬彙整通知的 `{slot_name}　{scheduled_time}` 同一種排版，
    在 `flex.med.group_heading` 集中管理成模板字串，避免兩處各自硬編碼
    這個特殊空格。
    """
    return {
        "type": "text",
        "text": t("flex.med.group_heading", language).format(
            meal=t(f"meal.{group.meal_timing}", language), time=group.scheduled_time
        ),
        "weight": "bold",
        "size": ft.body,
        "color": theme.TEXT,
        "wrap": True,
    }


def _medication_row_with_button(
    entry: MedicationListEntry,
    medication_id: str,
    log_id: str,
    ft: theme.FlexTheme,
    language: str | None,
) -> dict[str, Any]:
    """逐藥確認的一列：既有的 `_medication_row_node` 內容＋一顆【已吃】按鈕
    （spec「逐藥確認」）。

    有縮圖與純文字兩種列，版面刻意不同：

    - 純文字列：水平排列，藥品內容吃掉較大比例（flex=2）、按鈕維持
      `FlexTheme` 定義的 `flex=1`——按鈕靠 `paddingAll: lg` 撐出 ≥44px 的
      可點擊高度，不需要額外調整；文字本身已有 `wrap: True`，寬度變窄時會
      自動換行而不是把按鈕擠出畫面外。
    - 有縮圖列：縮圖＋藥名維持垂直排列的整列寬度，按鈕改放到下方、同樣佔
      整列寬度，而不是跟縮圖並排。`_medication_row_node` 的縮圖尺寸
      （`theme._SIZE_SCALE` 的 "thumbnail"：large 180px／xlarge 200px）
      是照「認得出藥丸形狀顏色」的需求訂的；如果沿用純文字列的水平
      flex=2/flex=1 分割，縮圖所在的欄位會被壓縮到約 2/3 列寬，三段字級的
      縮圖尺寸全部視覺上擠成同一個大小，等於白訂了那個尺寸表。按鈕改放
      下方後縮圖拿回整列寬度，字級變大時縮圖才真的跟著變大。
    """
    button_label = t("flex.med.button.taken_one", language)
    button = ft.secondary_button(
        button_label,
        {
            "type": "postback",
            "label": button_label,
            "data": (
                f"action=confirm_medication&log_id={log_id}"
                f"&medication_id={medication_id}"
            ),
            "displayText": t("flex.med.display.taken_one", language).format(
                name=entry.name
            ),
        },
    )
    row_node = _medication_row_node(entry, ft)
    if entry.image_url:
        return {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [row_node, button],
        }
    return {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "alignItems": "center",
        "contents": [
            {**row_node, "flex": 2},
            button,
        ],
    }


def _medication_groups_block(
    groups: list[MedicationGroup],
    log_id: str,
    ft: theme.FlexTheme,
    language: str | None,
) -> Optional[dict[str, Any]]:
    """依服藥時機分區、每列附逐藥確認按鈕的藥品區塊（spec「逐藥確認」「推播
    列出該時段應服藥品」、design 決策 6）。

    `groups` 保證非空清單、且每組 `items` 皆非空——由呼叫端（`medication_ids`
    為空或全空清單時）先行判斷要不要呼叫這個函式，這裡不重複判斷一次。

    只有一組且時機為 `none` 時（規則沒有拆分飯前飯後，等同本功能導入前的
    單一清單）不顯示分區小標，版面比照既有 `_medication_list_block`（含
    `flex.med.medication_list_heading` 標題），只是每列多一顆按鈕——這是
    spec 明講的「只差每列多一顆按鈕」。其餘情況（多分區，或單一分區但時機
    是飯前／飯後）改成每區一行小標，不再有整塊共用的標題文字：小標本身
    （「飯前　07:30」）已經比「本次應服藥品」更精準地說明這批藥是什麼時候吃。

    顯示上限跨組合計（`MEDICATION_LIST_MAX_ITEMS`），超出的品項不逐一列出，
    收斂成既有的單行計數且不帶按鈕——收斂後的計數行不代表任何一張藥證，
    按下去沒有意義。一旦達到上限就不再進入下一組（也不會再多印一個空的
    分區小標），避免出現「小標底下一列都沒有」的殘影。
    """
    single_none = len(groups) == 1 and groups[0].meal_timing == "none"
    total_items = sum(len(group.items) for group in groups)

    contents: list[dict[str, Any]] = []
    if single_none:
        contents.append(
            {
                "type": "text",
                "text": t("flex.med.medication_list_heading", language),
                "weight": "bold",
                "size": ft.body,
                "color": theme.TEXT,
                "wrap": True,
            }
        )

    shown = 0
    for group in groups:
        if shown >= MEDICATION_LIST_MAX_ITEMS:
            break
        if not single_none:
            contents.append(_group_heading_node(group, ft, language))
        for medication_id, entry in group.items:
            if shown >= MEDICATION_LIST_MAX_ITEMS:
                break
            contents.append(
                _medication_row_with_button(entry, medication_id, log_id, ft, language)
            )
            shown += 1

    remaining = total_items - shown
    if remaining > 0:
        # 收斂後的計數行只是一句提示文字，不代表任何一張藥證，故意重用
        # `_medication_row_node` 走純文字分支，不帶按鈕。
        contents.append(
            _medication_row_node(
                MedicationListEntry(
                    name=t("flex.med.medication_list_more", language).format(
                        count=remaining
                    )
                ),
                ft,
            )
        )

    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.SURFACE_ALT,
        "cornerRadius": "md",
        "paddingAll": "lg",
        "spacing": "xs",
        "margin": "md",
        "contents": contents,
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
    medication_groups: Optional[list[MedicationGroup]] = None,
    language: str | None = None,
    font_size: str | None = None,
    tone: str = "control",
) -> FlexMessage:
    """
    建立傳送給用藥者的服藥提醒 Flex Message。

    `tone` 只換說明那一句（見 `_INSTRUCTION_KEYS`），標題、藥品清單與按鈕不變；
    預設的 control 就是上線前的文字。
    - disabled=False: 顯示【我已用藥】可點擊按鈕
    - disabled=True: 顯示已完成的停用狀態 (點擊後動態替換)

    `medication_names` 兩種狀態都會呈現，只換標題的時態（應服／已服用）。
    已完成的卡片同樣列出藥名，是因為它是使用者事後唯一能回頭查的憑據——
    只寫「感謝您的紀錄」而不寫吃了什麼，等於把提醒卡上有的資訊在確認後就
    丟掉，家屬與使用者都無從核對那一次到底服了哪幾種藥。
    規則沒有關聯藥品、或關聯的藥品皆已失效時傳入 None／空清單，版面與本參數
    新增前完全相同。

    `medication_groups` 非空清單且 `disabled=False` 時，改用依飯前／飯後
    分區＋逐藥確認按鈕的版面（spec「逐藥確認」「推播列出該時段應服藥品」、
    design 決策 6），底部按鈕文案改為「全部已服用」；`medication_groups`
    為 `None`／空清單，或 `disabled=True`（已完成卡片，逐藥按鈕在那之後
    沒有意義）時，一律落回既有的 `medication_names` 版面，逐位元組不變。
    """
    ft = theme.resolve_theme(font_size)
    slot_name = get_slot_display_name(slot_type, language)

    if not disabled:
        alt_text = t("flex.med.alt.reminder", language).format(slot=slot_name)
        body_contents = [_slot_block(slot_name, scheduled_time, ft, language)]
        if medication_groups:
            body_contents.append(
                _medication_groups_block(medication_groups, log_id, ft, language)
            )
            taken_label = t("flex.med.button.taken_all", language)
        else:
            med_block = _medication_list_block(medication_names, ft, language)
            if med_block is not None:
                body_contents.append(med_block)
            taken_label = t("flex.med.button.taken", language)
        instruction_key = _INSTRUCTION_KEYS.get(tone, "flex.med.instruction")
        body_contents.append(_paragraph(t(instruction_key, language), ft, margin="md"))
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
    medication_groups: Optional[list[MedicationGroup]] = None,
    language: str | None = None,
    font_size: str | None = None,
    tone: str = "control",
) -> FlexMessage:
    """T+20min 傳送給用藥者的二次催促 Flex Message

    `tone` 與同一頓的 T+0 提醒相同，只換說明那一句（見 `_URGENT_BODY_KEYS`）。

    `medication_names` 為 None／空清單時版面與本參數新增前完全相同，見
    `_medication_list_block`。

    `medication_groups` 非空清單時改用分區＋逐藥確認按鈕的版面，理由同
    `build_patient_medication_flex`；`medication_groups_for_log` 只回傳當日
    仍有效且尚未在 `taken_medication_ids` 裡的藥（見該方法註解），本函式
    直接照單全收，因此「催促只列尚未確認的藥品」不需要在這裡另外過濾一次。
    """
    ft = theme.resolve_theme(font_size)
    slot_name = get_slot_display_name(slot_type, language)

    body_contents = [_slot_block(slot_name, scheduled_time, ft, language)]
    if medication_groups:
        body_contents.append(
            _medication_groups_block(medication_groups, log_id, ft, language)
        )
        taken_label = t("flex.med.button.taken_all", language)
    else:
        med_block = _medication_list_block(medication_names, ft, language)
        if med_block is not None:
            body_contents.append(med_block)
        taken_label = t("flex.med.button.taken", language)
    urgent_body_key = _URGENT_BODY_KEYS.get(tone, "flex.med.urgent_body")
    body_contents.append(_paragraph(t(urgent_body_key, language), ft, margin="md"))

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

    `medication_names` 列出這個時段漏掉的是哪幾種藥。收件人是家庭授權通知政策
    （`medication_missed`）選出的家屬：影子模式下是族譜全員，強制後是 GUARDIAN
    與 CAREGIVER——兩者依授權本來就讀得到用藥設定（GENERAL），藥名對他們不是
    新揭露的資訊；少了它，警報只說得出「某人某個時段沒吃藥」，家屬無從判斷這次
    漏掉的是保養用藥還是不能斷的處方。
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
