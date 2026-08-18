import json

import pytest
from linebot.v3.messaging import FlexMessage

from app.services.line_messaging.flex.verdict_flex import build_verdict_flex
from app.services.rag.claim_verification.service import VerificationResult
from resources.flex_messages import theme


def _result(**overrides) -> VerificationResult:
    base = dict(
        user_question="網傳吃鳳梨心可以溶解血栓，是真的嗎？",
        verdict="錯誤",
        reasoning="查核報告指出這是缺乏醫學根據的說法，血栓需以藥物治療。",
        source_title="鳳梨心溶血栓查核報告",
        source_url="https://tfc-taiwan.org.tw/fact-check-reports/xxx",
        matched=True,
        related_info="",
    )
    base.update(overrides)
    return VerificationResult(**base)


def _uri_actions(node) -> list[dict]:
    """遞迴走訪 Flex 節點樹，收集所有 type=uri 的 action。"""
    actions: list[dict] = []

    def walk(n):
        if isinstance(n, dict):
            action = n.get("action")
            if isinstance(action, dict) and action.get("type") == "uri":
                actions.append(action)
            for value in n.values():
                walk(value)
        elif isinstance(n, list):
            for item in n:
                walk(item)

    walk(node)
    return actions


def _header_color(msg: FlexMessage) -> str:
    return msg.contents.to_dict()["header"]["backgroundColor"]


# ── 1. 五種 verdict 各渲染一次，標頭色正確 ──────────────────────────────


@pytest.mark.parametrize(
    "verdict, matched, expected_color",
    [
        ("錯誤", True, theme.STATUS_CLOSED),
        ("部分錯誤", True, theme.STATUS_PENDING),
        ("正確", True, theme.STATUS_OPEN),
        ("事實釐清", True, theme.STATUS_UNKNOWN),
        ("證據不足", False, theme.STATUS_UNKNOWN),
    ],
)
def test_header_color_matches_verdict_semantics(verdict, matched, expected_color):
    result = _result(
        verdict=verdict,
        matched=matched,
        source_url="https://tfc-taiwan.org.tw/fact-check-reports/xxx" if matched else "",
        related_info="" if matched else "相關衛教資訊內容",
    )
    msg = build_verdict_flex(result)
    assert isinstance(msg, FlexMessage)
    assert _header_color(msg) == expected_color


def test_header_color_uses_verdict_slug_as_primary_key():
    """I4 finding：verdict_slug（穩定機器鍵）是配色表的主要依據，不是 verdict
    的中文顯示字串。刻意讓兩者「打架」以孤立驗證機制：slug 說 correct、
    文字說錯誤時，色碼要跟著 slug 走——這正是修法要保護的方向，因為中文
    字串來自 CARE-data 前綴對照表或 TFC 網站用詞，兩者都出過資料異常事故。
    """
    result = _result(verdict="錯誤", verdict_slug="correct", matched=True)
    msg = build_verdict_flex(result)
    assert _header_color(msg) == theme.STATUS_OPEN


def test_header_color_strips_legacy_prefix_from_slug():
    """CARE-data 舊站遷移文章的 verdict_slug 可能帶 "legacy:" 前綴。"""
    result = _result(verdict="錯誤", verdict_slug="legacy:錯誤", matched=True)
    msg = build_verdict_flex(result)
    assert _header_color(msg) == theme.STATUS_CLOSED


def test_header_color_falls_back_to_verdict_text_when_slug_blank():
    """存量資料或尚未回填 slug 時，仍要能靠中文字串配對出正確顏色——I4 的
    修法不能讓沒有 slug 的既有資料整批退化成中性灰。"""
    result = _result(verdict="部分錯誤", verdict_slug="", matched=True)
    msg = build_verdict_flex(result)
    assert _header_color(msg) == theme.STATUS_PENDING


def test_header_color_falls_back_to_neutral_when_slug_and_text_both_unrecognized():
    result = _result(verdict="未知判定字串", verdict_slug="unknown-slug", matched=True)
    msg = build_verdict_flex(result)
    assert _header_color(msg) == theme.STATUS_UNKNOWN


def test_fact_clarification_and_insufficient_evidence_share_neutral_color():
    """design 決策 6：兩者都「不判真偽」，配色必須完全一致，不能是各自湊巧
    算出同一個色碼——這裡直接比較兩張卡片的標頭色，而不是各自跟常數比較。
    """
    clarification = build_verdict_flex(_result(verdict="事實釐清", matched=True))
    insufficient = build_verdict_flex(
        _result(verdict="證據不足", matched=False, source_url="", related_info="")
    )
    assert _header_color(clarification) == _header_color(insufficient)
    assert _header_color(clarification) == theme.STATUS_UNKNOWN


# ── 2. 命中時含來源標示與 URI action，且 URI 等於 source_url ─────────────


def test_matched_includes_source_label_and_uri_action_equal_to_source_url():
    result = _result(
        matched=True, source_url="https://tfc-taiwan.org.tw/fact-check-reports/xxx"
    )
    msg = build_verdict_flex(result)
    rendered = msg.contents.to_dict()
    assert "台灣事實查核中心" in str(rendered)

    actions = _uri_actions(rendered)
    assert len(actions) == 1
    assert actions[0]["uri"] == "https://tfc-taiwan.org.tw/fact-check-reports/xxx"


# ── 3. source_url 為空時不含任何 action，且不產生空 URI ──────────────────


def test_matched_with_empty_source_url_produces_no_action_and_no_footer():
    """理論上 matched=True 必有 source_url（見 service.py 的欄位註解），但
    卡片本身要防禦這個組合：LINE 對帶空字串 uri 的 action 會拒收整則 Flex
    Message，寧可少一顆按鈕也不能讓整張卡片送不出去。
    """
    result = _result(matched=True, source_url="")
    msg = build_verdict_flex(result)
    rendered = msg.contents.to_dict()

    assert _uri_actions(rendered) == []
    assert "footer" not in rendered
    # 確保不是「有 action、但 uri 是空字串」這種更隱蔽的錯法
    assert '"uri": ""' not in json.dumps(rendered, ensure_ascii=False)


def test_unmatched_produces_no_action_or_footer():
    result = _result(matched=False, verdict="證據不足", source_url="", related_info="衛教資訊內容")
    msg = build_verdict_flex(result)
    rendered = msg.contents.to_dict()
    assert _uri_actions(rendered) == []
    assert "footer" not in rendered


# ── 4 / 5. 未命中時的相關衛教資訊區塊 ─────────────────────────────────────


def test_unmatched_with_related_info_includes_labeled_block():
    result = _result(
        matched=False,
        verdict="證據不足",
        source_url="",
        related_info="檸檬水的營養成分與一般水果類似，並無排毒之特殊功效。",
    )
    msg = build_verdict_flex(result)
    rendered = str(msg.contents.to_dict())
    assert "相關衛教資訊" in rendered
    assert "檸檬水的營養成分與一般水果類似，並無排毒之特殊功效。" in rendered
    # 必須註明這不是判定依據，避免使用者把附帶資訊誤讀成查核結論
    assert "非本次說法的查核依據" in rendered


def test_unmatched_without_related_info_omits_block_and_still_renders():
    result = _result(matched=False, verdict="證據不足", source_url="", related_info="")
    msg = build_verdict_flex(result)
    assert isinstance(msg, FlexMessage)
    rendered = str(msg.contents.to_dict())
    assert "相關衛教資訊" not in rendered


# ── 6. 卡片文字不含 Markdown 符號 ────────────────────────────────────────


def test_card_text_contains_no_markdown_markers():
    matched_rendered = str(build_verdict_flex(_result(matched=True)).contents.to_dict())
    unmatched_rendered = str(
        build_verdict_flex(
            _result(matched=False, verdict="證據不足", source_url="", related_info="重點一。")
        ).contents.to_dict()
    )
    for rendered in (matched_rendered, unmatched_rendered):
        assert "**" not in rendered
        assert "##" not in rendered
        assert not any(
            line.strip().startswith("- ")
            for line in rendered.replace("\\n", "\n").splitlines()
        )


# ── 7. alt_text 含判定字樣 ────────────────────────────────────────────


@pytest.mark.parametrize("verdict", ["錯誤", "部分錯誤", "正確", "事實釐清", "證據不足"])
def test_alt_text_contains_verdict(verdict):
    msg = build_verdict_flex(_result(verdict=verdict))
    assert verdict in msg.alt_text


def test_alt_text_contains_question_summary():
    msg = build_verdict_flex(_result(user_question="網傳喝薑茶可以退燒？"))
    assert "網傳喝薑茶可以退燒" in msg.alt_text


# ── 8. 極長文字不會讓組裝失敗 ────────────────────────────────────────────


def test_extremely_long_reasoning_and_question_do_not_break_assembly():
    result = _result(user_question="問" * 5000, reasoning="理" * 5000)
    msg = build_verdict_flex(result)
    assert isinstance(msg, FlexMessage)
    # altText 仍要落在 LINE 官方上限內，不能把 5000 字整段塞進去
    assert len(msg.alt_text) <= 400


def test_extremely_long_related_info_does_not_break_assembly_when_unmatched():
    result = _result(
        matched=False,
        verdict="證據不足",
        source_url="",
        user_question="問" * 3000,
        related_info="資" * 5000,
    )
    msg = build_verdict_flex(result)
    assert isinstance(msg, FlexMessage)


# ── 9. 空字串防護（C2 finding）：LINE Flex 的 text 元件與 altText 都要求
# 非空字串，空字串會讓整則訊息在 API 呼叫時被拒收（400），使用者收到完全
# 的沉默——比顯示一句不完美的預設文字更糟 ──────────────────────────────


def test_blank_user_question_is_replaced_with_non_empty_fallback():
    result = _result(user_question="")
    msg = build_verdict_flex(result)
    rendered = msg.contents.to_dict()

    question_text = rendered["body"]["contents"][0]["contents"][1]["text"]
    assert question_text != ""
    assert question_text.strip() != ""


def test_whitespace_only_user_question_is_replaced_with_non_empty_fallback():
    """只有空白字元也要視為「沒有內容」，不能讓 LINE 收到全是空白的 text。"""
    result = _result(user_question="   ")
    msg = build_verdict_flex(result)
    rendered = msg.contents.to_dict()

    question_text = rendered["body"]["contents"][0]["contents"][1]["text"]
    assert question_text.strip() != ""


def test_blank_reasoning_is_replaced_with_non_empty_fallback():
    result = _result(reasoning="")
    msg = build_verdict_flex(result)
    rendered = msg.contents.to_dict()

    reasoning_text = rendered["body"]["contents"][1]["text"]
    assert reasoning_text != ""
    assert reasoning_text.strip() != ""


def test_blank_user_question_does_not_produce_empty_alt_text():
    result = _result(user_question="", verdict="錯誤")
    msg = build_verdict_flex(result)

    assert msg.alt_text.strip() != ""
    assert not msg.alt_text.endswith("｜")


# ── 必須遵守：用 FlexTheme 的尺寸，不是寫死字級 ───────────────────────────


def test_verdict_flex_follows_font_size_setting():
    normal = build_verdict_flex(_result(), font_size="normal")
    xlarge = build_verdict_flex(_result(), font_size="xlarge")
    normal_size = normal.contents.to_dict()["header"]["contents"][0]["size"]
    xlarge_size = xlarge.contents.to_dict()["header"]["contents"][0]["size"]
    assert normal_size != xlarge_size
