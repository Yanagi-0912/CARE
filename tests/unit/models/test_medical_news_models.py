from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.medical_news import (
    DrugNews,
    MedicalNewsDelivery,
    MedicalNewsShare,
    make_news_ref,
)


def test_make_news_ref_is_stable_for_same_key():
    """同一組 (kind, key) 必須永遠得到同一個 ref。

    ref 是 (user_id, news_ref) 唯一索引的一半，不穩定就等於去重失效——
    同一則消息會在隔天以另一個 ref 再推一次。
    """
    first = make_news_ref("kb_article", "https://www.hpa.gov.tw/Pages/Detail.aspx?pid=19023")
    second = make_news_ref("kb_article", "https://www.hpa.gov.tw/Pages/Detail.aspx?pid=19023")
    assert first == second


def test_make_news_ref_differs_across_kinds():
    """同一個 key 在兩種 kind 底下不得相撞。"""
    assert make_news_ref("drug_news", "abc") != make_news_ref("kb_article", "abc")


def test_make_news_ref_length_is_bounded():
    """任意長度的 key 產出的 ref 長度固定。

    kb_article 的 key 是文章 url。Mongo 單一索引鍵上限 1024 bytes，若直接把原字串
    塞進索引，夠長的 url 會讓 insert 拋錯——而那個錯會落在推播路徑上。
    """
    short = make_news_ref("kb_article", "https://a.gov.tw/1")
    long = make_news_ref("kb_article", "https://a.gov.tw/" + "x" * 5000)
    assert len(short) == len(long)
    assert len(short) < 64


def test_make_news_ref_rejects_unknown_kind():
    with pytest.raises(ValueError):
        make_news_ref("something_else", "abc")


def test_drug_news_requires_url():
    """無 url 的來源不得成為消息卡。

    食藥署 DataAction feed 結構上不提供文章網址；消息卡必須帶可點的來源連結，
    分享給家人的卡片尤其——那是收件人唯一能自行查證的東西。
    """
    with pytest.raises(ValidationError):
        DrugNews(
            drug_key="ACETAMINOPHEN",
            key_kind="ingredient",
            title="某藥品回收公告",
            source_name="食藥署",
            summary="食藥署公告某批號回收。",
            concern_kind="recall",
            content_hash="h",
        )


def test_drug_news_rejects_unknown_concern_kind():
    with pytest.raises(ValidationError):
        DrugNews(
            drug_key="ACETAMINOPHEN",
            key_kind="ingredient",
            url="https://www.fda.gov.tw/TC/newsContent.aspx?id=1",
            title="標題",
            source_name="食藥署",
            summary="摘要",
            concern_kind="none",
            content_hash="h",
        )


def test_drug_news_accepts_alias_id():
    news = DrugNews(
        _id="abc123",
        drug_key="ACETAMINOPHEN",
        key_kind="ingredient",
        url="https://www.fda.gov.tw/TC/newsContent.aspx?id=1",
        title="標題",
        source_name="食藥署",
        published_at="2026-08-30",
        summary="摘要",
        concern_kind="recall",
        content_hash="h",
    )
    assert news.id == "abc123"
    assert news.model_dump(by_alias=True)["_id"] == "abc123"


def test_delivery_defaults_are_unshared():
    delivery = MedicalNewsDelivery(
        user_id="U1",
        news_ref=make_news_ref("drug_news", "abc"),
        tier=1,
        pushed_at=datetime.now(timezone.utc),
    )
    assert delivery.shared_at is None
    assert delivery.share_recipient_count == 0


def test_delivery_rejects_tier_outside_one_and_two():
    with pytest.raises(ValidationError):
        MedicalNewsDelivery(
            user_id="U1",
            news_ref=make_news_ref("drug_news", "abc"),
            tier=3,
            pushed_at=datetime.now(timezone.utc),
        )


def test_share_records_sharer_and_recipient():
    share = MedicalNewsShare(
        recipient_id="U2",
        news_ref=make_news_ref("drug_news", "abc"),
        sharer_id="U1",
        sent_at=datetime.now(timezone.utc),
    )
    assert share.recipient_id == "U2"
    assert share.sharer_id == "U1"
