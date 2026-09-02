import inspect
import json

import pytest

from app.services.line_messaging.flex import medical_news_flex as m
from resources.flex_messages import size_guard, theme

REF = "drug_news:abcdef0123456789abcdef0123456789"
URL = "https://www.fda.gov.tw/TC/newsContent.aspx?id=1"


def _texts(node, out=None):
    out = [] if out is None else out
    if isinstance(node, dict):
        if node.get("type") == "text":
            out.append(node)
        for value in node.values():
            _texts(value, out)
    elif isinstance(node, list):
        for item in node:
            _texts(item, out)
    return out


def _flatten(node):
    return json.dumps(node, ensure_ascii=False)


def _tier1(**kwargs):
    payload = {
        "news_ref": REF,
        "drug_name": "普拿疼",
        "title": "食藥署公告普拿疼某批號回收",
        "summary": "食藥署公告某批號回收，已通知醫療院所下架。",
        "source_name": "食藥署",
        "url": URL,
        "language": "zh-TW",
        "font_size": "large",
    }
    payload.update(kwargs)
    return m.build_tier1_news_bubble(**payload)


def _tier2(**kwargs):
    payload = {
        "news_ref": REF,
        "title": "天氣熱如何正確補水",
        "summary": "國健署提醒夏季應規律補充水分。",
        "source_name": "國民健康署",
        "url": URL,
        "language": "zh-TW",
        "font_size": "large",
    }
    payload.update(kwargs)
    return m.build_tier2_news_bubble(**payload)


def _shared(**kwargs):
    payload = {
        "sharer_name": "小明",
        "title": "食藥署公告某批號回收",
        "summary": "食藥署公告某批號回收，已通知醫療院所下架。",
        "source_name": "食藥署",
        "url": URL,
        "language": "zh-TW",
        "font_size": "large",
    }
    payload.update(kwargs)
    return m.build_shared_news_bubble(**payload)


# ── 字級 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scale", ["normal", "large", "xlarge"])
@pytest.mark.parametrize("builder", [_tier1, _tier2, _shared])
def test_font_size_scales_all_text_nodes(builder, scale):
    """三張卡在三種字級下的每個文字節點都必須取自 FlexTheme。

    寫死任何一個 size，長輩把字級調到特大時那一行就不會跟著變大——本專案的
    目標使用者正是視力需求最高的族群。
    """
    ft = theme.resolve_theme(scale)
    allowed = {ft.title, ft.heading, ft.body, ft.caption, ft.button, ft.thumbnail}

    bubble = builder(font_size=scale)

    sizes = {node["size"] for node in _texts(bubble) if "size" in node}
    assert sizes
    assert sizes <= allowed


# ── 洩漏防線 ────────────────────────────────────────────────────────


def test_tier2_card_contains_no_drug_name():
    """Tier 2 是保底衛教，與使用者的用藥無關，不得出現任何藥名。"""
    assert "普拿疼" not in _flatten(_tier2())


def test_shared_card_contains_no_drug_name():
    """分享卡零洩漏：收件人不該從卡片得知分享者在吃什麼藥。"""
    assert "普拿疼" not in _flatten(_shared(title="某藥品回收", summary="某批號回收。"))


def test_shared_builder_has_no_drug_parameter():
    """介面上根本沒有藥名參數，呼叫端連誤傳的機會都沒有。"""
    params = inspect.signature(m.build_shared_news_bubble).parameters
    assert "drug_name" not in params
    assert "drug_key" not in params


def test_indication_fields_never_rendered():
    """Medication.indication / spc_indication / spc_indication_summary
    在模型檔上有「SHALL NOT 進入任何推播訊息」的明文禁令——適應症直接揭露病情。

    以簽章斷言而非字串比對：三支 builder 的介面上沒有任何適應症參數，
    因此不存在「呼叫端不小心傳進來」的路徑。
    """
    forbidden = {"indication", "spc_indication", "spc_indication_summary"}
    for builder in (
        m.build_tier1_news_bubble,
        m.build_tier2_news_bubble,
        m.build_shared_news_bubble,
    ):
        assert forbidden.isdisjoint(inspect.signature(builder).parameters)


def test_shared_card_has_no_share_button():
    """分享卡不得再帶分享按鈕——否則會變成無限轉傳。"""
    assert "share_medical_news" not in _flatten(_shared())


# ── 版面與行為 ──────────────────────────────────────────────────────


def test_tier1_and_tier2_headers_differ():
    """兩層必須在版面上可分辨，否則 Tier 1 會被 Tier 2 稀釋（design 決策 1）。"""
    t1_header = _tier1()["header"]
    t2_header = _tier2()["header"]
    assert t1_header["backgroundColor"] != t2_header["backgroundColor"]
    assert _texts(t1_header)[0]["text"] != _texts(t2_header)[0]["text"]


def test_tier1_card_shows_drug_name():
    assert "普拿疼" in _flatten(_tier1())


def test_tier1_card_has_consult_professional_line():
    """固定行動呼籲，常數文案，不由模型產生。"""
    assert "請與您的醫師或藥師確認" in _flatten(_tier1())


def test_tier2_card_has_no_consult_professional_line():
    """Tier 2 不涉及使用者的用藥，掛上「不要自行改變用藥」是無關的恐嚇。"""
    assert "不要自行改變用藥" not in _flatten(_tier2())


def test_share_postback_carries_news_ref():
    assert f"action=share_medical_news&news_ref={REF}" in _flatten(_tier1())
    assert f"action=share_medical_news&news_ref={REF}" in _flatten(_tier2())


def test_source_button_uses_uri_action_with_verbatim_url():
    payload = _flatten(_tier1())
    assert '"type": "uri"' in payload
    assert URL in payload


# ── 大小防線 ────────────────────────────────────────────────────────


def test_oversized_summary_is_truncated_then_fits():
    bubble = _tier1(summary="回收公告內容。" * 3000)

    assert size_guard.fits(bubble)


def test_oversized_title_still_fits():
    bubble = _tier2(title="標題" * 3000, summary="摘要" * 3000)

    assert size_guard.fits(bubble)


def test_normal_card_is_well_under_limit():
    assert size_guard.wire_bytes(_tier1()) < size_guard.SAFE_BUBBLE_BYTES


# ── FlexMessage 包裝 ────────────────────────────────────────────────


def test_flex_message_builders_return_flex_message():
    from linebot.v3.messaging import FlexMessage

    assert isinstance(
        m.build_tier1_news_flex(
            news_ref=REF,
            drug_name="普拿疼",
            title="標題",
            summary="摘要",
            source_name="食藥署",
            url=URL,
            language="zh-TW",
            font_size="large",
        ),
        FlexMessage,
    )
