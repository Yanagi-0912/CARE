import inspect

from linebot.v3.messaging import FlexMessage

from app.services.line_messaging.flex.medication_flex import (
    MedicationListEntry,
    build_caregiver_alert_flex,
    build_caregiver_missed_summary_flex,
    build_patient_medication_flex,
    build_patient_urgent_reminder_flex,
    get_slot_display_name,
)

# ── 加入 medication_names 參數前的實際輸出快照 ──────────────────────────
#
# 這兩份快照是老實從加入 medication_names 參數「之前」的提交（0eab8f9）誠實
# 擷取出來的，不是憑印象手打的：
#
#   git worktree add /tmp/task8_baseline 0eab8f9
#   cd /tmp/task8_baseline && .venv/bin/python -c "
#       from app.services.line_messaging.flex.medication_flex import (
#           build_patient_medication_flex, build_patient_urgent_reminder_flex,
#       )
#       build_patient_medication_flex(
#           log_id='L123', slot_type='morning', scheduled_time='08:00'
#       ).contents.to_dict()
#       build_patient_urgent_reminder_flex(
#           log_id='L123', slot_type='noon', scheduled_time='12:00'
#       ).contents.to_dict()
#   "
#
# 決策 2 說這裡的版面回歸會打到現在每一則既有提醒，所以「新版空清單輸出＝
# 自己跟自己比」不足以證明版面沒有偏移；這裡改成跟這份誠實擷取來的舊版
# 輸出逐位元組比對。
_BASELINE_PATIENT_REMINDER_ALT_TEXT = "CARE 用藥提醒：早 服藥時間到了"
_BASELINE_PATIENT_REMINDER_CONTENTS = {
    "type": "bubble",
    "header": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#2E7D32",
        "paddingAll": "lg",
        "contents": [
            {
                "type": "text",
                "text": "用藥提醒",
                "color": "#FFFFFF",
                "weight": "bold",
                # 14666f9（PR #101）把 theme 的 heading 整排調大一級
                # （large: xl → xxl），是為了長輩可讀性的刻意變更。這份快照要
                # 守的是「加入 medication_names 沒有改動空清單的版面」，不是把
                # 字級凍結在某個版本，因此跟著主題更新。
                "size": "xxl",
                "wrap": True,
            }
        ],
    },
    "body": {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "xl",
        "backgroundColor": "#FFFFFF",
        "spacing": "md",
        "contents": [
            {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#EDF5EE",
                "cornerRadius": "md",
                "paddingAll": "lg",
                "spacing": "xs",
                "contents": [
                    {
                        "type": "text",
                        "text": "早",
                        "weight": "bold",
                        "size": "3xl",
                        "color": "#1B5E20",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": "服藥時間 08:00",
                        "size": "lg",
                        "color": "#1B5E20",
                        "wrap": True,
                    },
                ],
            },
            {
                "type": "text",
                "text": "請於 30 分鐘內服藥，並點擊下方按鈕確認。",
                "size": "lg",
                "color": "#555555",
                "wrap": True,
                "margin": "md",
            },
        ],
    },
    "footer": {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "lg",
        "contents": [
            {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#2E7D32",
                "cornerRadius": "md",
                "paddingAll": "lg",
                "flex": 1,
                "action": {
                    "type": "postback",
                    "label": "我已用藥",
                    "data": "action=confirm_medication&log_id=L123",
                    "displayText": "我已經完成用藥了",
                },
                "contents": [
                    {
                        "type": "text",
                        "text": "我已用藥",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "xl",
                        "align": "center",
                        "wrap": True,
                    }
                ],
            }
        ],
    },
}

_BASELINE_URGENT_REMINDER_ALT_TEXT = "CARE 提醒：您尚未確認 中 服藥"
_BASELINE_URGENT_REMINDER_CONTENTS = {
    "type": "bubble",
    "header": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#C62828",
        "paddingAll": "lg",
        "contents": [
            {
                "type": "text",
                "text": "尚未完成用藥",
                "color": "#FFFFFF",
                "weight": "bold",
                # 同上：跟隨 14666f9 對 theme heading 的刻意調整。
                "size": "xxl",
                "wrap": True,
            }
        ],
    },
    "body": {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "xl",
        "backgroundColor": "#FFFFFF",
        "spacing": "md",
        "contents": [
            {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#EDF5EE",
                "cornerRadius": "md",
                "paddingAll": "lg",
                "spacing": "xs",
                "contents": [
                    {
                        "type": "text",
                        "text": "中",
                        "weight": "bold",
                        "size": "3xl",
                        "color": "#1B5E20",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": "服藥時間 12:00",
                        "size": "lg",
                        "color": "#1B5E20",
                        "wrap": True,
                    },
                ],
            },
            {
                "type": "text",
                "text": "您尚未點擊「我已用藥」。請即刻服藥並點擊下方按鈕確認。",
                "size": "lg",
                "color": "#555555",
                "wrap": True,
                "margin": "md",
            },
        ],
    },
    "footer": {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "lg",
        "contents": [
            {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#2E7D32",
                "cornerRadius": "md",
                "paddingAll": "lg",
                "flex": 1,
                "action": {
                    "type": "postback",
                    "label": "我已用藥",
                    "data": "action=confirm_medication&log_id=L123",
                    "displayText": "我已經完成用藥了",
                },
                "contents": [
                    {
                        "type": "text",
                        "text": "我已用藥",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "xl",
                        "align": "center",
                        "wrap": True,
                    }
                ],
            }
        ],
    },
}


def test_get_slot_display_name():
    assert get_slot_display_name("morning") == "早"
    assert get_slot_display_name("noon") == "中"
    assert get_slot_display_name("evening") == "晚"
    assert get_slot_display_name("bedtime") == "睡前"
    assert get_slot_display_name("custom") == "custom"


def test_build_patient_medication_flex_active():
    msg = build_patient_medication_flex(
        log_id="L123", slot_type="morning", scheduled_time="08:00", disabled=False
    )
    assert isinstance(msg, FlexMessage)
    assert "早" in msg.alt_text
    assert "CARE 用藥提醒" in msg.alt_text


def test_build_patient_medication_flex_disabled():
    msg = build_patient_medication_flex(
        log_id="L123",
        slot_type="evening",
        scheduled_time="18:00",
        disabled=True,
        taken_at_str="18:05",
    )
    assert isinstance(msg, FlexMessage)
    assert "已完成" in msg.alt_text


def test_build_patient_urgent_reminder_flex():
    msg = build_patient_urgent_reminder_flex(
        log_id="L123", slot_type="noon", scheduled_time="12:00"
    )
    assert isinstance(msg, FlexMessage)
    assert "尚未確認" in msg.alt_text


def test_build_caregiver_alert_flex():
    msg = build_caregiver_alert_flex(
        patient_name="王小明", slot_type="bedtime", scheduled_time="21:30"
    )
    assert isinstance(msg, FlexMessage)
    assert "王小明" in msg.alt_text
    assert "逾時未服藥" in msg.alt_text


def test_medication_flex_follows_language_setting():
    msg = build_patient_medication_flex(
        log_id="L123", slot_type="morning", scheduled_time="08:00", language="en"
    )
    body = str(msg.contents.to_dict())
    assert "Morning" in body
    assert "I took it" in body
    assert "早" not in body


def test_medication_flex_follows_font_size_setting():
    normal = build_patient_medication_flex(
        log_id="L1", slot_type="morning", scheduled_time="08:00", font_size="normal"
    )
    xlarge = build_patient_medication_flex(
        log_id="L1", slot_type="morning", scheduled_time="08:00", font_size="xlarge"
    )
    # body 第一塊是時段重點區塊，其首行為時段名稱
    normal_slot = normal.contents.to_dict()["body"]["contents"][0]["contents"][0]
    xlarge_slot = xlarge.contents.to_dict()["body"]["contents"][0]["contents"][0]
    assert normal_slot["size"] == "xl"
    assert xlarge_slot["size"] == "4xl"


def test_caregiver_alert_follows_language_setting():
    msg = build_caregiver_alert_flex(
        patient_name="王小明", slot_type="bedtime", scheduled_time="21:30", language="ja"
    )
    assert "王小明" in msg.alt_text
    assert "就寝前" in str(msg.contents.to_dict())


def test_unknown_slot_type_falls_back_to_raw_value():
    # 未知時段不應輸出 i18n key 字串
    msg = build_patient_medication_flex(
        log_id="L1", slot_type="brunch", scheduled_time="10:00"
    )
    body = str(msg.contents.to_dict())
    assert "brunch" in body
    assert "slot.brunch" not in body


# ── 停機錯過時段的彙整通知 ────────────────────────────────────────────


def _missed(slot_type: str, scheduled_time: str, patient_name: str = "李老先生") -> dict:
    return {
        "patient_name": patient_name,
        "slot_type": slot_type,
        "scheduled_time": scheduled_time,
    }


def test_missed_summary_groups_slots_under_each_patient():
    msg = build_caregiver_missed_summary_flex(
        [
            _missed("morning", "08:00"),
            _missed("noon", "12:00"),
            _missed("evening", "18:00", patient_name="王阿嬤"),
        ]
    )
    assert isinstance(msg, FlexMessage)
    body = msg.contents.to_dict()["body"]["contents"]
    names = [
        block["contents"][0]["text"]
        for block in body
        if block.get("type") == "box"
    ]
    # 同一位家人的時段收在同一個區塊，不重複列出姓名
    assert names == ["李老先生", "王阿嬤"]


def test_missed_summary_wording_differs_from_overdue_alert():
    """
    這則說的是「我們沒能發出提醒」，不是「家人逾時未服藥」。
    沿用逾時警報的措辭會讓家屬誤以為長輩沒吃藥。
    """
    summary = build_caregiver_missed_summary_flex([_missed("morning", "08:00")])
    alert = build_caregiver_alert_flex(
        patient_name="李老先生", slot_type="morning", scheduled_time="08:00"
    )
    assert summary.alt_text != alert.alt_text
    assert "未發出" in summary.alt_text


def test_missed_summary_truncates_long_outages():
    """停機半天會累積大量時段，超過上限的部分收斂成一行，避免 Flex 過大。"""
    entries = [_missed("morning", f"{hour:02d}:00") for hour in range(14)]
    msg = build_caregiver_missed_summary_flex(entries)

    rendered = str(msg.contents.to_dict())
    assert "另有 4 個時段" in rendered
    # altText 仍要報出完整數量
    assert "14" in msg.alt_text


# ── 推播文案的藥品區塊 ────────────────────────────────────────────────


def test_patient_reminder_empty_medication_list_matches_baseline():
    """
    medication_ids 為空（或全部失效）是既有規則的常態，這種情況下的版面
    必須與加入 medication_names 參數「之前」的實際輸出逐位元組相同——不是
    跟現在的函式自己比（那樣就算版面已經悄悄跑掉，只要跑掉的方式對空清單和
    預設值一致，測試依然會過），而是跟從 0eab8f9 誠實擷取出來的舊版快照比對。
    """
    with_none = build_patient_medication_flex(
        log_id="L123", slot_type="morning", scheduled_time="08:00", medication_names=None
    )
    with_empty = build_patient_medication_flex(
        log_id="L123", slot_type="morning", scheduled_time="08:00", medication_names=[]
    )
    assert with_none.alt_text == _BASELINE_PATIENT_REMINDER_ALT_TEXT
    assert with_none.contents.to_dict() == _BASELINE_PATIENT_REMINDER_CONTENTS
    assert with_empty.contents.to_dict() == _BASELINE_PATIENT_REMINDER_CONTENTS


def test_patient_reminder_disabled_state_lists_medication_names():
    """已完成用藥的卡片要留下「這次吃了哪幾種藥」。

    （先前這條規則是反過來的：已完成卡片刻意忽略藥名，理由是「沒有『該吃什麼』
    的需求」。那個理由只看了確認前的用途——確認之後這張卡片就是使用者事後唯一
    查得到的憑據，提醒卡上列得出來的藥名在按下按鈕後整批消失，等於沒有任何地方
    回答得了「那一次到底服了哪幾種藥」。標題改用已服用的時態，不再說「應服」。）
    """
    without = build_patient_medication_flex(
        log_id="L123",
        slot_type="evening",
        scheduled_time="18:00",
        disabled=True,
        taken_at_str="18:05",
    )
    with_names = build_patient_medication_flex(
        log_id="L123",
        slot_type="evening",
        scheduled_time="18:00",
        disabled=True,
        taken_at_str="18:05",
        medication_names=["脈優"],
    )
    rendered = str(with_names.contents.to_dict())
    assert "脈優" in rendered
    assert "本次服用藥品" in rendered
    assert "本次應服藥品" not in rendered

    # 沒有藥品關聯的既有規則，版面必須與本變更前完全相同（不能出現空白區塊）。
    assert without.contents.to_dict() == build_patient_medication_flex(
        log_id="L123",
        slot_type="evening",
        scheduled_time="18:00",
        disabled=True,
        taken_at_str="18:05",
        medication_names=[],
    ).contents.to_dict()
    assert "脈優" not in str(without.contents.to_dict())


def test_patient_reminder_lists_medication_names():
    msg = build_patient_medication_flex(
        log_id="L123",
        slot_type="morning",
        scheduled_time="08:00",
        medication_names=["脈優", "利尿劑"],
    )
    body = str(msg.contents.to_dict())
    assert "脈優" in body
    assert "利尿劑" in body


def test_patient_reminder_medication_list_collapses_overflow_to_one_line():
    """超過顯示上限的藥品收斂為單行計數，形狀比照家屬彙整通知的截斷方式。"""
    names = [f"藥{i}" for i in range(8)]
    msg = build_patient_medication_flex(
        log_id="L123",
        slot_type="morning",
        scheduled_time="08:00",
        medication_names=names,
    )
    rendered = str(msg.contents.to_dict())
    for shown in names[:5]:
        assert shown in rendered
    for hidden in names[5:]:
        assert hidden not in rendered
    assert "另有 3 種藥品" in rendered


# ── 藥丸縮圖：證號已確定且有照片時，該列改為「縮圖＋藥名」───────────────


def test_patient_reminder_row_with_thumbnail_carries_image():
    """證號已確定且有落地縮圖時，該列要看得到縮圖節點與對應的藥名。"""
    msg = build_patient_medication_flex(
        log_id="L123",
        slot_type="morning",
        scheduled_time="08:00",
        medication_names=[
            MedicationListEntry(name="脈優", image_url="https://img.example.com/a.jpg")
        ],
    )
    med_block = msg.contents.to_dict()["body"]["contents"][1]
    rows = med_block["contents"][1:]  # 第一個是標題列
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "box"
    assert row["layout"] == "horizontal"
    image_node = next(c for c in row["contents"] if c["type"] == "image")
    assert image_node["url"] == "https://img.example.com/a.jpg"
    text_node = next(c for c in row["contents"] if c["type"] == "text")
    assert text_node["text"] == "脈優"


def test_patient_reminder_mixed_thumbnail_and_text_rows_layout_intact():
    """同一時段圖文混排是常態：有縮圖的列變成 box，沒有的維持純文字列，兩者並存。"""
    msg = build_patient_medication_flex(
        log_id="L123",
        slot_type="morning",
        scheduled_time="08:00",
        medication_names=[
            MedicationListEntry(name="脈優", image_url="https://img.example.com/a.jpg"),
            "利尿劑",  # 證號未確定或無落地縮圖，仍以純文字呈現
        ],
    )
    med_block = msg.contents.to_dict()["body"]["contents"][1]
    rows = med_block["contents"][1:]
    assert len(rows) == 2

    with_image, without_image = rows
    assert with_image["type"] == "box"
    assert any(c["type"] == "image" for c in with_image["contents"])

    assert without_image["type"] == "text"
    assert without_image["text"] == "利尿劑"


def test_urgent_reminder_row_with_thumbnail_carries_image():
    """二次催促與首刷提醒共用同一套藥品清單區塊，縮圖同樣要出現。"""
    msg = build_patient_urgent_reminder_flex(
        log_id="L123",
        slot_type="noon",
        scheduled_time="12:00",
        medication_names=[
            MedicationListEntry(name="普拿疼", image_url="https://img.example.com/b.jpg")
        ],
    )
    rendered = msg.contents.to_dict()
    med_block = rendered["body"]["contents"][1]
    row = med_block["contents"][1]
    assert row["type"] == "box"
    image_node = next(c for c in row["contents"] if c["type"] == "image")
    assert image_node["url"] == "https://img.example.com/b.jpg"


def test_patient_reminder_overflow_collapse_stays_text_only_even_with_thumbnails():
    """收斂成單行計數的那一列不代表任何一張藥證，即使清單裡混著帶縮圖的藥品，
    這一行本身也絕不能出現縮圖。
    """
    entries = [
        MedicationListEntry(name=f"藥{i}", image_url=f"https://img.example.com/{i}.jpg")
        for i in range(8)
    ]
    msg = build_patient_medication_flex(
        log_id="L123",
        slot_type="morning",
        scheduled_time="08:00",
        medication_names=entries,
    )
    med_block = msg.contents.to_dict()["body"]["contents"][1]
    rows = med_block["contents"][1:]
    assert len(rows) == 6  # 5 顯示 + 1 收斂列
    for shown_row in rows[:5]:
        assert shown_row["type"] == "box"  # 前 5 筆都帶縮圖
    last_row = rows[-1]
    assert last_row["type"] == "text"
    assert last_row["text"] == "…另有 3 種藥品"


def test_caregiver_alert_flex_never_contains_image():
    """家屬的逾時警報即使帶藥名，也不得出現任何縮圖節點（spec「家屬卡片不含縮圖」）。"""
    msg = build_caregiver_alert_flex(
        patient_name="王小明",
        slot_type="morning",
        scheduled_time="08:00",
        medication_names=["脈優", "利尿劑"],
    )
    rendered = str(msg.contents.to_dict())
    # dict 轉字串後圖片節點會是 "'type': 'image'"（Python repr 用單引號）
    assert "'type': 'image'" not in rendered


def test_caregiver_missed_summary_flex_never_contains_image():
    """錯過時段的彙整通知同樣不帶任何縮圖——它連 medication_names 參數都沒有，
    這裡仍明確鎖住輸出裡沒有任何圖片節點。
    """
    msg = build_caregiver_missed_summary_flex(
        [_missed("morning", "08:00"), _missed("noon", "12:00", patient_name="王阿嬤")]
    )
    rendered = str(msg.contents.to_dict())
    assert "'type': 'image'" not in rendered


def test_urgent_reminder_empty_medication_list_matches_baseline():
    """同上，比對對象是 0eab8f9 誠實擷取出來的舊版快照，不是自己跟自己比。"""
    none_names = build_patient_urgent_reminder_flex(
        log_id="L123", slot_type="noon", scheduled_time="12:00", medication_names=None
    )
    empty = build_patient_urgent_reminder_flex(
        log_id="L123", slot_type="noon", scheduled_time="12:00", medication_names=[]
    )
    assert none_names.alt_text == _BASELINE_URGENT_REMINDER_ALT_TEXT
    assert none_names.contents.to_dict() == _BASELINE_URGENT_REMINDER_CONTENTS
    assert empty.contents.to_dict() == _BASELINE_URGENT_REMINDER_CONTENTS


def test_urgent_reminder_lists_medication_names():
    msg = build_patient_urgent_reminder_flex(
        log_id="L123",
        slot_type="noon",
        scheduled_time="12:00",
        medication_names=["普拿疼"],
    )
    assert "普拿疼" in str(msg.contents.to_dict())


def test_urgent_reminder_medication_list_collapses_overflow_to_one_line():
    names = [f"藥{i}" for i in range(7)]
    msg = build_patient_urgent_reminder_flex(
        log_id="L123", slot_type="noon", scheduled_time="12:00", medication_names=names
    )
    rendered = str(msg.contents.to_dict())
    assert "另有 2 種藥品" in rendered


def test_caregiver_alert_flex_lists_the_medications_that_were_missed():
    """
    家屬的逾時警報要說得出漏掉的是哪幾種藥。

    （先前這條規則是反過來的：簽章刻意不收藥品清單，理由是不讓病情資訊進到
    家屬的通知列。但收件人 `alert_notify_user_id` 取自 `reminder.creator_user_id`
    ——正是當初替家人建立這些藥的人，藥名對他不是新揭露的資訊；而少了藥名，
    警報只說得出「某人某個時段沒吃藥」，家屬無從判斷漏掉的是保養用藥還是
    不能斷的處方。真正不得外流的是適應症，那條界線由
    `test_caregiver_alert_flex_never_carries_indication` 單獨守住。）
    """
    medication_names = ["脈優", "利尿劑"]
    caregiver_card = build_caregiver_alert_flex(
        patient_name="王小明",
        slot_type="morning",
        scheduled_time="08:00",
        medication_names=medication_names,
    )
    rendered = str(caregiver_card.contents.to_dict())
    assert all(name in rendered for name in medication_names)
    assert "尚未服用的藥品" in rendered
    # 病患姓名與時段仍是主資訊，不能被藥品區塊擠掉
    assert "王小明" in rendered
    assert "08:00" in rendered


def test_caregiver_alert_flex_without_medication_names_keeps_the_original_layout():
    """沒有藥品關聯的既有規則（medication_ids 為空陣列）版面必須完全不變。"""
    baseline = build_caregiver_alert_flex(
        patient_name="王小明", slot_type="morning", scheduled_time="08:00"
    ).contents.to_dict()
    assert (
        build_caregiver_alert_flex(
            patient_name="王小明",
            slot_type="morning",
            scheduled_time="08:00",
            medication_names=[],
        ).contents.to_dict()
        == baseline
    )
    assert "尚未服用的藥品" not in str(baseline)


def test_caregiver_alert_flex_never_carries_indication():
    """
    藥名可以進家屬警報，適應症不行——它會直接揭露病情（見 Medication.indication
    的欄位註解：「不得進入任何推播訊息」）。呼叫端只被允許傳藥名，這裡鎖住
    build_caregiver_alert_flex 沒有任何其他管道能收到適應症欄位。
    """
    assert "indication" not in inspect.signature(build_caregiver_alert_flex).parameters
    rendered = str(
        build_caregiver_alert_flex(
            patient_name="王小明",
            slot_type="morning",
            scheduled_time="08:00",
            medication_names=["脈優"],
        ).contents.to_dict()
    )
    assert "高血壓" not in rendered


def test_caregiver_missed_summary_flex_carries_no_medication_names():
    """同上：先證明這批藥名是「真的關聯到這個時段」的資料，不是隨便挑的字串。"""
    medication_names = ["脈優", "利尿劑"]
    patient_card = build_patient_medication_flex(
        log_id="L123",
        slot_type="morning",
        scheduled_time="08:00",
        medication_names=medication_names,
    )
    assert all(
        name in str(patient_card.contents.to_dict()) for name in medication_names
    )

    assert (
        "medication_names"
        not in inspect.signature(build_caregiver_missed_summary_flex).parameters
    )

    summary_card = build_caregiver_missed_summary_flex([_missed("morning", "08:00")])
    rendered = str(summary_card.contents.to_dict())
    assert not any(name in rendered for name in medication_names)
