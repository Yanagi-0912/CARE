import inspect

from linebot.v3.messaging import FlexMessage

from app.services.line_messaging.flex.medication_flex import (
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
                "size": "xl",
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
                "size": "xl",
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


def test_patient_reminder_disabled_state_ignores_medication_names():
    """已完成用藥的卡片沒有『該吃什麼』的需求，傳入藥名也不該改變版面。"""
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
    assert without.contents.to_dict() == with_names.contents.to_dict()


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


def test_caregiver_alert_flex_carries_no_medication_names():
    """
    家屬的逾時警報要回答的是「有沒有吃」，不是「該吃什麼」——函式簽章本身
    就不接受藥品清單參數，用意識地不讓病情資訊進到家屬的通知列。

    只斷言簽章沒有這個參數還不夠有說服力：如果 build_caregiver_alert_flex
    根本沒有任何管道能收到藥名，那「輸出裡沒有藥名」這件事就是恆真命題，
    不管實作對不對都會過。這裡先證明這個時段「確實」關聯了藥品——把同一批
    藥名餵給用藥者卡片，確認它們真的會出現——再證明同一時段的家屬警報完全
    不含這些藥名，這樣才是真的在測「家屬卡片排除藥名」這條規則。
    """
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

    assert "medication_names" not in inspect.signature(build_caregiver_alert_flex).parameters

    caregiver_card = build_caregiver_alert_flex(
        patient_name="王小明", slot_type="morning", scheduled_time="08:00"
    )
    rendered = str(caregiver_card.contents.to_dict())
    assert not any(name in rendered for name in medication_names)


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
