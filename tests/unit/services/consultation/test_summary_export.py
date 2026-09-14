from __future__ import annotations

from datetime import date, datetime

from app.models.consultation import ConsultationSummary
from app.services.consultation.summary_export import render_summaries_txt

EXPORTED_AT = datetime(2026, 9, 14, 13, 45)


def _summary(text: str, day: date = date(2026, 5, 27)) -> ConsultationSummary:
    return ConsultationSummary(
        line_id="U123",
        summary_date=day,
        summary=text,
        language="zh-TW",
        created_at=datetime(2026, 5, 27, 10, 0),
    )


def test_json_summary_renders_sections_in_key_order():
    text = render_summaries_txt(
        [_summary('{"主訴": " 頭痛 ", "症狀": "頭痛、噁心", "檢查": "無"}')],
        "zh-TW",
        EXPORTED_AT,
    )

    assert text == (
        "醫療諮詢紀錄摘要\n"
        "匯出時間：2026-09-14 13:45\n"
        "\n"
        "==== 2026-05-27 ====\n"
        "\n"
        "■ 主訴\n頭痛\n\n"
        "■ 症狀\n頭痛、噁心\n\n"
        # 「無」照樣保留，不過濾
        "■ 檢查\n無\n"
    )


def test_unparseable_summary_is_printed_verbatim():
    text = render_summaries_txt([_summary(" 該日期尚無諮詢記錄。 ")], "zh-TW", EXPORTED_AT)

    assert text.endswith("==== 2026-05-27 ====\n\n該日期尚無諮詢記錄。\n")


def test_non_object_json_is_printed_verbatim():
    text = render_summaries_txt([_summary('["頭痛"]')], "zh-TW", EXPORTED_AT)

    assert text.endswith('==== 2026-05-27 ====\n\n["頭痛"]\n')


def test_list_values_become_bullets_and_empty_values_are_skipped():
    text = render_summaries_txt(
        [_summary('{"建議": ["多喝水", " ", "量血壓"], "檢查": null, "其他": "  "}')],
        "zh-TW",
        EXPORTED_AT,
    )

    assert "■ 建議\n- 多喝水\n- 量血壓\n" in text
    assert "■ 檢查" not in text
    assert "■ 其他" not in text


def test_object_values_are_pretty_printed():
    text = render_summaries_txt(
        [_summary('{"檢查": {"血壓": "140/90"}}')], "zh-TW", EXPORTED_AT
    )

    assert '■ 檢查\n{\n  "血壓": "140/90"\n}\n' in text


def test_code_fenced_summary_is_parsed():
    text = render_summaries_txt(
        [_summary('```json\n{"主訴": "頭痛"}\n```')], "zh-TW", EXPORTED_AT
    )

    assert "■ 主訴\n頭痛\n" in text
    assert "```" not in text


def test_summaries_keep_given_order():
    text = render_summaries_txt(
        [
            _summary('{"主訴": "新"}', date(2026, 5, 27)),
            _summary('{"主訴": "舊"}', date(2026, 5, 26)),
        ],
        "zh-TW",
        EXPORTED_AT,
    )

    assert text.index("2026-05-27") < text.index("2026-05-26")


def test_empty_list_prints_no_data_line():
    text = render_summaries_txt([], "zh-TW", EXPORTED_AT)

    assert text == "醫療諮詢紀錄摘要\n匯出時間：2026-09-14 13:45\n\n目前沒有摘要資料\n"


def test_header_follows_language():
    text = render_summaries_txt([], "en", EXPORTED_AT)

    assert text == (
        "Medical Consultation Summary\n"
        "Exported at: 2026-09-14 13:45\n"
        "\n"
        "No summary data available.\n"
    )


def test_unsupported_language_falls_back_to_zh_tw():
    text = render_summaries_txt([], "fr", EXPORTED_AT)

    assert text.startswith("醫療諮詢紀錄摘要\n")
