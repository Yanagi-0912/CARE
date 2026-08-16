import json

from app.services.line_messaging.flex.safety_flex import build_family_alert_flex

PATIENT_QUESTION = "我最近睡不好，朋友說吃這個很有效"


def _flatten(flex) -> str:
    return json.dumps(flex.contents.to_dict(), ensure_ascii=False)


def test_family_alert_carries_patient_name_and_drug_name():
    flex = build_family_alert_flex(
        patient_name="王大明",
        drug_name="合利他命強効錠 EX PLUS",
        risk_reason="這不是台灣核准的版本",
        language="zh-TW",
        font_size="large",
    )

    payload = _flatten(flex)
    assert "王大明" in payload
    assert "合利他命強効錠 EX PLUS" in payload
    assert "這不是台灣核准的版本" in payload


def test_family_alert_omits_the_original_question():
    """推播會出現在通知列與鎖定畫面。原話常帶病情，藥袋 OCR 全文更帶姓名與院所。"""
    flex = build_family_alert_flex(
        patient_name="王大明",
        drug_name="某藥",
        risk_reason="來源不明",
        language="zh-TW",
        font_size="large",
    )

    payload = _flatten(flex)
    assert PATIENT_QUESTION not in payload
    assert "睡不好" not in payload


def test_family_alert_alt_text_names_the_patient():
    flex = build_family_alert_flex(
        patient_name="王大明",
        drug_name="某藥",
        risk_reason="來源不明",
        language="zh-TW",
        font_size="large",
    )

    assert "王大明" in flex.alt_text


def test_family_alert_respects_language():
    zh = _flatten(
        build_family_alert_flex(
            patient_name="Ming",
            drug_name="某藥",
            risk_reason="reason",
            language="zh-TW",
            font_size="large",
        )
    )
    en = _flatten(
        build_family_alert_flex(
            patient_name="Ming",
            drug_name="某藥",
            risk_reason="reason",
            language="en",
            font_size="large",
        )
    )

    assert zh != en


def test_family_alert_respects_font_size():
    normal = _flatten(
        build_family_alert_flex(
            patient_name="Ming",
            drug_name="某藥",
            risk_reason="reason",
            language="zh-TW",
            font_size="normal",
        )
    )
    xlarge = _flatten(
        build_family_alert_flex(
            patient_name="Ming",
            drug_name="某藥",
            risk_reason="reason",
            language="zh-TW",
            font_size="xlarge",
        )
    )

    assert normal != xlarge
