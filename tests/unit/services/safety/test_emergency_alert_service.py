"""
緊急狀況家人通報：誰收得到、收到什麼、以及什麼情況下不送。

這是本專案唯一一種「系統判斷錯誤會驚動第三人」的通知，而誤報收不回來。
因此這裡的測試多半在驗「不送」的條件，而不是「送得出去」。
"""

import json

import pytest
from linebot.v3.messaging import FlexContainer, FlexMessage

from app.core.user_language import SUPPORTED_LANGUAGES
from app.services.safety.emergency_alert_service import (
    NOTIFICATION_KIND,
    EmergencyFamilyAlertService,
    notify_patient_family_was_told,
)
from resources.flex_messages.medical_messages.emergency_family_alert_flex_message import (
    build_emergency_family_bubble,
    build_emergency_family_flex,
)

PATIENT = "U_PATIENT"
REASON = "提到有人失去意識、叫不醒"


class FakeReplier:
    """刻意貼齊 LineReplier 的真實契約，不比它寬鬆。

    初版的替身收 dict、回 None，於是兩個真實 bug 都測不出來：
      1. push_flex 收的是 SDK 的 FlexMessage，傳 dict 進去會靜默失敗。
      2. push_flex 把例外吞掉只回傳 bool，所以「有沒有送出去」要看回傳值。
    線上的表現是家人一則都沒收到，當事人卻收到了「我已經讓你的家人知道」。
    替身比真品寬鬆時，測試綠燈只證明替身自己會動。
    """

    def __init__(self, flex_result=True):
        self.flex = []
        self.texts = []
        self._flex_result = flex_result

    async def push_flex(self, user_id, flex):
        assert isinstance(flex, FlexMessage), (
            "push_flex 收的是 SDK FlexMessage；傳 dict 進去線上會靜默失敗"
        )
        if not self._flex_result:
            return False
        self.flex.append((user_id, flex))
        return True

    async def push_text(self, user_id, text):
        self.texts.append((user_id, text))
        return True


class FakeAuthorization:
    def __init__(self, recipients=(), error=None):
        self._recipients = list(recipients)
        self._error = error
        self.calls = []

    async def notification_recipients(self, owner_id, kind):
        self.calls.append((owner_id, kind))
        if self._error is not None:
            raise self._error
        return list(self._recipients)


class FakeProfiles:
    def __init__(self, profiles=None):
        self._profiles = profiles or {}

    async def get_user_profile(self, user_id):
        return self._profiles.get(user_id)


def _bubble(**kwargs):
    kwargs.setdefault("patient_name", "王小明")
    kwargs.setdefault("reason", REASON)
    return build_emergency_family_bubble(**kwargs)


def _service(recipients=("U_SON",), profiles=None, replier=None, auth_error=None):
    return (
        EmergencyFamilyAlertService(
            replier=replier or FakeReplier(),
            authorization_service=FakeAuthorization(recipients, auth_error),
            user_profile_service=FakeProfiles(profiles),
        ),
    )[0]


# --- 收件人判定 --------------------------------------------------------------


async def test_notifies_eligible_recipients():
    replier = FakeReplier()
    service = _service(recipients=("U_SON", "U_DAUGHTER"), replier=replier)

    assert await service.notify(PATIENT, REASON) is True
    assert [uid for uid, _ in replier.flex] == ["U_SON", "U_DAUGHTER"]


async def test_asks_the_authorization_service_for_the_right_kind():
    auth = FakeAuthorization(("U_SON",))
    service = EmergencyFamilyAlertService(
        replier=FakeReplier(),
        authorization_service=auth,
        user_profile_service=FakeProfiles(),
    )

    await service.notify(PATIENT, REASON)

    assert auth.calls == [(PATIENT, NOTIFICATION_KIND)]


async def test_patient_never_receives_their_own_alert():
    """當事人收到的是自己那張紅卡。他若也在自己的族譜裡，不該收到兩則。"""
    replier = FakeReplier()
    service = _service(recipients=(PATIENT, "U_SON"), replier=replier)

    await service.notify(PATIENT, REASON)

    assert [uid for uid, _ in replier.flex] == ["U_SON"]


async def test_no_recipients_sends_nothing():
    replier = FakeReplier()
    service = _service(recipients=(), replier=replier)

    assert await service.notify(PATIENT, REASON) is False
    assert replier.flex == [] and replier.texts == []


async def test_authorization_failure_notifies_nobody():
    """
    對主流程 fail-open、對通報 fail-closed。「查不到就全族譜廣播」會把最私密
    的一件事送給不該收到的人，而那收不回來。
    """
    replier = FakeReplier()
    service = _service(auth_error=RuntimeError("db down"), replier=replier)

    assert await service.notify(PATIENT, REASON) is False
    assert replier.flex == []


async def test_missing_authorization_service_notifies_nobody():
    service = EmergencyFamilyAlertService(
        replier=FakeReplier(), authorization_service=None
    )
    assert await service.notify(PATIENT, REASON) is False


# --- notify_family 開關（不豁免）---------------------------------------------


async def test_recipient_who_opted_out_is_skipped():
    """
    開關的語意是「我要不要收到家人的健康通知」，屬於收件人。危機通報刻意
    不豁免它——使用者關掉是明示的意願，緊急時無視等於系統自行決定
    「我比你更知道什麼對你好」。
    """
    replier = FakeReplier()
    service = _service(
        recipients=("U_SON", "U_DAUGHTER"),
        profiles={"U_SON": {"settings": {"notify_family": False}}},
        replier=replier,
    )

    assert await service.notify(PATIENT, REASON) is True
    assert [uid for uid, _ in replier.flex] == ["U_DAUGHTER"]


async def test_all_recipients_opted_out_means_nothing_sent():
    replier = FakeReplier()
    service = _service(
        recipients=("U_SON",),
        profiles={"U_SON": {"settings": {"notify_family": False}}},
        replier=replier,
    )

    assert await service.notify(PATIENT, REASON) is False


async def test_missing_setting_defaults_to_notifying():
    """既有使用者的文件沒有這一欄，不該因此收不到。"""
    replier = FakeReplier()
    service = _service(
        recipients=("U_SON",), profiles={"U_SON": {"settings": {}}}, replier=replier
    )

    assert await service.notify(PATIENT, REASON) is True


# --- 內容 --------------------------------------------------------------------


async def test_card_carries_the_patient_name_not_their_words():
    """
    卡片 SHALL NOT 含當事人的原話。原話對「該不該趕過去」沒有增加資訊，
    卻會把最私密的一句話轉發給第三人。
    """
    replier = FakeReplier()
    service = _service(
        recipients=("U_SON",),
        profiles={PATIENT: {"name": "王小明"}},
        replier=replier,
    )

    await service.notify(PATIENT, REASON)

    message = replier.flex[0][1]
    payload = json.dumps(message.contents.to_dict(), ensure_ascii=False)
    assert "王小明" in payload
    assert REASON in payload


async def test_recipient_display_prefs_are_used_not_the_patients():
    """背景推播沒有 request context，每位收件人的語言與字級都要各自查。"""
    replier = FakeReplier()
    service = _service(
        recipients=("U_SON",),
        profiles={
            PATIENT: {"name": "王小明", "settings": {"language": "zh-TW"}},
            "U_SON": {"settings": {"language": "en", "font_size": "xlarge"}},
        },
        replier=replier,
    )

    await service.notify(PATIENT, REASON)

    payload = json.dumps(replier.flex[0][1].contents.to_dict(), ensure_ascii=False)
    assert "may need" in payload


async def test_push_failure_falls_back_to_text():
    """
    寧可少了版面，也不要讓家人什麼都收不到。push_flex 回傳 False（它吞掉例外，
    不會拋），因此判斷送出與否必須看回傳值。
    """
    replier = FakeReplier(flex_result=False)
    service = _service(
        recipients=("U_SON",), profiles={PATIENT: {"name": "王小明"}}, replier=replier
    )

    await service.notify(PATIENT, REASON)

    assert [uid for uid, _ in replier.texts] == ["U_SON"]
    assert "王小明" in replier.texts[0][1]


# --- 告知當事人 --------------------------------------------------------------


async def test_patient_is_told_in_supportive_wording():
    """
    這則訊息的收件人正處於危機中，讀起來必須像有人來陪，不是像被舉報——
    否則下一次他就不說了。
    """
    replier = FakeReplier()
    await notify_patient_family_was_told(replier, PATIENT, "zh-TW")

    assert replier.texts[0][0] == PATIENT
    text = replier.texts[0][1]
    assert "不用一個人" in text
    for accusatory in ("通報", "警告", "舉報", "違規"):
        assert accusatory not in text


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
async def test_patient_notice_exists_in_every_language(language):
    replier = FakeReplier()
    await notify_patient_family_was_told(replier, PATIENT, language)
    assert not replier.texts[0][1].startswith("text.emergency.")


# --- 卡片本身 ----------------------------------------------------------------


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("font_size", ["normal", "large", "xlarge"])
def test_card_passes_line_sdk_validation(language, font_size):
    bubble = _bubble(language=language, font_size=font_size)
    FlexContainer.from_json(json.dumps(bubble, ensure_ascii=False))


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_no_untranslated_keys_leak_into_the_card(language):
    message = build_emergency_family_flex(
        patient_name="王小明", reason=REASON, language=language
    )
    payload = json.dumps(message.contents.to_dict(), ensure_ascii=False)
    assert "emergency_family." not in payload
    assert "emergency_family." not in message.alt_text


def test_alt_text_names_the_person():
    """通知列上唯一看得到的字。沒有名字，家屬不知道是誰、也不會馬上點開。"""
    message = build_emergency_family_flex(
        patient_name="王小明", reason=REASON, language="zh-TW"
    )
    assert "王小明" in message.alt_text


def test_call_button_only_appears_when_a_number_is_known():
    """CARE 沒有存電話，多數情況會是 None。缺按鈕不得讓卡片少掉行動指示。"""
    payload = json.dumps(_bubble(language="zh-TW"), ensure_ascii=False)
    assert "先打電話給" in payload
    assert "tel:" not in payload

    with_tel = _bubble(patient_tel_uri="tel:0912345678", language="zh-TW")
    assert "tel:0912345678" in json.dumps(with_tel, ensure_ascii=False)


def test_card_says_it_may_be_wrong():
    """
    唯一一種誤判會驚動第三人的通知。免責必須明說它可能是錯的，並把
    「以你實際聯繫到的情況為準」放在最後。
    """
    payload = json.dumps(_bubble(language="zh-TW"), ensure_ascii=False)
    assert "可能判斷錯誤" in payload
    assert "不是醫療診斷" in payload


def test_card_does_not_tell_family_to_call_119_first():
    """
    家屬多半不在現場，救護車不知道要去哪裡。第一個動作是先打給當事人確認，
    119 是聯絡不上或情況危急時的第二步。
    """
    steps = json.dumps(_bubble(language="zh-TW")["body"], ensure_ascii=False)
    assert steps.index("先打電話給") < steps.index("119")


def test_missing_name_falls_back_rather_than_showing_blank():
    payload = _bubble(patient_name="", language="zh-TW")
    assert "你的家人" in json.dumps(payload, ensure_ascii=False)


# --- 原話逐字轉發 ------------------------------------------------------------
#
# 初版只送系統的白話摘要，理由是「原話對『該不該趕過去』沒有增加資訊」。
# 那個理由是錯的：「可能需要協助」與「喝了 3 瓶農藥」對家屬是完全不同的兩件事。


async def test_verbatim_words_reach_the_card():
    replier = FakeReplier()
    service = _service(
        recipients=("U_SON",), profiles={PATIENT: {"name": "王小明"}}, replier=replier
    )

    await service.notify(PATIENT, "提到喝下大量農藥", "我剛剛喝了3瓶農藥")

    payload = json.dumps(replier.flex[0][1].contents.to_dict(), ensure_ascii=False)
    assert "我剛剛喝了3瓶農藥" in payload


def test_words_are_not_paraphrased_or_reordered():
    """改寫會把「3 瓶」變成「一些」，而劑量正是急救要問的第一個問題。"""
    words = "我剛剛喝了3瓶農藥，現在肚子很痛"
    payload = json.dumps(_bubble(patient_words=words), ensure_ascii=False)
    assert words in payload


def test_long_words_are_truncated_not_dropped():
    """卡片放不下整段病史，但急救需要的資訊幾乎一定在開頭。截斷優於不顯示。"""
    from resources.flex_messages.medical_messages.emergency_family_alert_flex_message import (
        MAX_QUOTED_CHARS,
    )

    words = "農" * (MAX_QUOTED_CHARS + 50)
    payload = json.dumps(_bubble(patient_words=words), ensure_ascii=False)
    assert "農" * MAX_QUOTED_CHARS in payload
    assert "…" in payload
    assert words not in payload


def test_quote_block_is_absent_when_there_are_no_words():
    """語音或圖片訊息取不到原話時，不該留一個空引述框。"""
    payload = json.dumps(_bubble(patient_words=""), ensure_ascii=False)
    assert "剛才說的話" not in payload


def test_quote_comes_before_the_system_judgement():
    """原話是事實，判定是推論。家屬掃過卡片時最先看到的應該是他說了什麼。"""
    payload = json.dumps(
        _bubble(patient_words="我剛剛喝了3瓶農藥", language="zh-TW"),
        ensure_ascii=False,
    )
    assert payload.index("剛才說的話") < payload.index("系統為什麼判定為緊急")


def test_alt_text_never_leaks_the_words():
    """
    通知列會出現在鎖定畫面上，旁邊的人也看得到。要看內容得先點開——這是
    「逐字轉發」唯一保留的界線。
    """
    message = build_emergency_family_flex(
        patient_name="王小明",
        reason="提到喝下大量農藥",
        patient_words="我剛剛喝了3瓶農藥",
        language="zh-TW",
    )
    assert "農藥" not in message.alt_text
    assert "王小明" in message.alt_text


@pytest.mark.parametrize("font_size", ["normal", "large", "xlarge"])
def test_card_with_quote_still_passes_sdk_validation(font_size):
    bubble = _bubble(
        patient_words="我剛剛喝了3瓶農藥，現在肚子很痛，頭也很暈", font_size=font_size
    )
    FlexContainer.from_json(json.dumps(bubble, ensure_ascii=False))
