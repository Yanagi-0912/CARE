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


async def test_blank_reason_is_replaced_in_the_recipients_language():
    """
    本地模型判定的緊急不帶白話說明，LLM 也可能回空字串。理由那一格若是空字串，
    LINE 會以 400 拒收整張卡，家人只剩純文字退路。
    """
    from app.i18n.messages import t

    replier = FakeReplier()
    service = _service(
        recipients=("U_SON",),
        profiles={"U_SON": {"settings": {"language": "en"}}},
        replier=replier,
    )

    assert await service.notify(PATIENT, "") is True
    (_, flex), = replier.flex
    card = json.dumps(flex.contents.to_dict(), ensure_ascii=False)
    assert t("emergency_family.default_reason", "en") in card
    assert replier.texts == []


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
    payload = json.dumps(_bubble(words=words), ensure_ascii=False)
    assert words in payload


def test_long_words_are_truncated_not_dropped():
    """卡片放不下整段病史，但急救需要的資訊幾乎一定在開頭。截斷優於不顯示。"""
    from resources.flex_messages.medical_messages.emergency_family_alert_flex_message import (
        MAX_QUOTED_CHARS,
    )

    words = "農" * (MAX_QUOTED_CHARS + 50)
    payload = json.dumps(_bubble(words=words), ensure_ascii=False)
    assert "農" * MAX_QUOTED_CHARS in payload
    assert "…" in payload
    assert words not in payload


def test_quote_block_is_absent_when_there_are_no_words():
    """語音或圖片訊息取不到原話時，不該留一個空引述框。"""
    payload = json.dumps(_bubble(words=""), ensure_ascii=False)
    assert "剛才說的話" not in payload


def test_quote_comes_before_the_system_judgement():
    """原話是事實，判定是推論。家屬掃過卡片時最先看到的應該是他說了什麼。"""
    payload = json.dumps(
        _bubble(words="我剛剛喝了3瓶農藥", language="zh-TW"),
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
        words="我剛剛喝了3瓶農藥",
        language="zh-TW",
    )
    assert "農藥" not in message.alt_text
    assert "王小明" in message.alt_text


@pytest.mark.parametrize("font_size", ["normal", "large", "xlarge"])
def test_card_with_quote_still_passes_sdk_validation(font_size):
    bubble = _bubble(
        words="我剛剛喝了3瓶農藥，現在肚子很痛，頭也很暈", font_size=font_size
    )
    FlexContainer.from_json(json.dumps(bubble, ensure_ascii=False))


# --- 紅卡之後的受影響者解析與稱謂（10.14）--------------------------------------
#
# 解析只決定稱謂：唯一命中家人才叫得出名字，其餘一律「對方」。


import asyncio  # noqa: E402

from app.models.family_tree import FamilyMember  # noqa: E402
from app.services.family.person_resolution import resolve_person  # noqa: E402
from app.services.medical.symptom_classification.urgency import (  # noqa: E402
    AffectedPerson,
)
from app.services.safety.emergency_alert_service import (  # noqa: E402
    followup_texts,
    resolve_affected,
)

OPERATOR = "U_GRANDSON"
GRANDPA = AffectedPerson(kind="family", label="阿公", relationship="grandparent", event="跌倒")
SELF = AffectedPerson(kind="self", event="胸口痛")


def _grandpa(user_id="U_GRANDPA", name="王大明"):
    return FamilyMember(user_id=user_id, display_name=name, relationship_type="grandparent")


class FakeResolver:
    """PatientContextService.resolve_person 的替身：只查名單，照真實規則解析。"""

    def __init__(self, members=(), *, error=None, delay=0.0):
        self.members = list(members)
        self.error = error
        self.delay = delay
        self.calls = []

    async def resolve_person(self, operator_id, *, person, relationship):
        self.calls.append((operator_id, person, relationship))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return resolve_person(self.members, person=person, relationship=relationship)


async def _texts(*people, resolver=None, timeout=5.0, language="zh-TW"):
    resolved = await resolve_affected(
        tuple(people), OPERATOR, resolver, timeout_seconds=timeout
    )
    return followup_texts(resolved, language)


@pytest.mark.asyncio
async def test_self_only_needs_no_followup():
    """「我昏倒了」：紅卡就是對發話者說的，不補、也不查名單。"""
    resolver = FakeResolver([_grandpa()])
    assert await _texts(AffectedPerson(kind="self", event="昏倒"), resolver=resolver) == []
    assert resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "people",
    [(), (AffectedPerson(kind="unknown", event="昏倒"),)],
    ids=["identification-failed", "model-said-unknown"],
)
async def test_unrecognized_people_are_treated_as_the_reporter(people):
    """認不出是誰就當發話者本人（2026-09-25 產品決定）：不補稱謂、通知他自己的家人。"""
    resolver = FakeResolver([_grandpa()])
    resolved = await resolve_affected(people, OPERATOR, resolver)

    assert [r.person.kind for r in resolved] == ["self"]
    assert followup_texts(resolved, "zh-TW") == []
    assert resolver.calls == []
    service = EmergencyFamilyAlertService(replier=FakeReplier())
    assert await service.patients_to_notify(OPERATOR, resolved) == [OPERATOR]


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["跌倒", "昏迷"])
async def test_uniquely_resolved_grandpa_is_named(event):
    person = AffectedPerson(kind="family", label="阿公", relationship="grandparent", event=event)
    texts = await _texts(person, resolver=FakeResolver([_grandpa()]))
    assert texts == ["請留在阿公身邊，並依紅卡立即尋求協助。"]


@pytest.mark.asyncio
async def test_two_grandparents_use_a_neutral_address():
    """同稱謂多人：叫錯人比不叫名字更糟。"""
    resolver = FakeResolver([_grandpa("U_G1", "王大明"), _grandpa("U_G2", "李阿土")])
    texts = await _texts(GRANDPA, resolver=resolver)
    assert texts == ["請留在對方身邊，並依紅卡立即尋求協助。"]
    assert "阿公" not in texts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "person",
    [
        AffectedPerson(kind="third_party", label="路人", event="跌倒"),
        AffectedPerson(kind="third_party", label="朋友", event="想自殺"),
    ],
)
async def test_unlinked_people_are_the_other_person_and_skip_the_family_list(person):
    resolver = FakeResolver([_grandpa()])
    texts = await _texts(person, resolver=resolver)
    assert texts == ["請留在對方身邊，並依紅卡立即尋求協助。"]
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_relative_not_in_the_family_list_is_the_other_person():
    texts = await _texts(GRANDPA, resolver=FakeResolver([]))
    assert texts == ["請留在對方身邊，並依紅卡立即尋求協助。"]


@pytest.mark.asyncio
async def test_grandpa_and_self_in_one_message_stay_separate():
    urgent_self = AffectedPerson(kind="self", event="胸口痛到喘不過氣")
    texts = await _texts(GRANDPA, urgent_self, resolver=FakeResolver([_grandpa()]))
    assert texts == [
        "請留在阿公身邊，並依紅卡立即尋求協助。",
        "你自己的狀況也可能需要立即處置，打 119 時請一併說明。",
    ]


@pytest.mark.asyncio
async def test_self_mentioned_but_not_urgent_is_not_told_to_call_119():
    mild_self = AffectedPerson(kind="self", event="有點頭痛", urgent=False)
    texts = await _texts(GRANDPA, mild_self, resolver=FakeResolver([_grandpa()]))
    assert texts == ["請留在阿公身邊，並依紅卡立即尋求協助。"]


@pytest.mark.asyncio
async def test_several_other_people_are_never_named():
    texts = await _texts(
        GRANDPA,
        AffectedPerson(kind="third_party", label="路人", event="被撞"),
        resolver=FakeResolver([_grandpa()]),
    )
    assert texts == ["請留在對方身邊，並依紅卡立即尋求協助。"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolver",
    [
        FakeResolver([_grandpa()], error=RuntimeError("mongo down")),
        FakeResolver([_grandpa()], delay=0.2),
        None,
    ],
    ids=["lookup-fails", "lookup-times-out", "no-service"],
)
async def test_family_lookup_problems_fall_back_to_a_neutral_address(resolver):
    texts = await _texts(GRANDPA, resolver=resolver, timeout=0.01)
    assert texts == ["請留在對方身邊，並依紅卡立即尋求協助。"]


@pytest.mark.asyncio
async def test_resolution_uses_the_operators_own_family_list():
    resolver = FakeResolver([_grandpa()])
    await _texts(GRANDPA, resolver=resolver)
    assert resolver.calls == [(OPERATOR, "阿公", "grandparent")]


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_followup_texts_exist_in_every_language(language):
    from app.i18n.messages import t

    for key in (
        "text.emergency.stay_with_named",
        "text.emergency.stay_with_other",
        "text.emergency.self_also_urgent",
    ):
        assert t(key, language) != key
    assert "{name}" in t("text.emergency.stay_with_named", language)


# --- 通知正確病人的照顧者（10.15）----------------------------------------------
#
# 通知主體是解析後的病人，不是發話者；收件人照病人的 emergency_detected 政策選；
# 回報只驗證家庭連結，不做 SENSITIVE READ，也不讀病人健康資料給回報者。

from app.services.family.person_resolution import PersonResolution  # noqa: E402
from app.services.safety.emergency_alert_service import ResolvedAffected  # noqa: E402

GRANDPA_ID = "U_GRANDPA"
GUARDIAN = "U_AUNT"


class PolicyAuthorization:
    """貼齊 FamilyAuthorizationService 在這條路徑用到的兩支：連結與收件人。"""

    def __init__(self, *, links=(), recipients=None, link_error=None):
        self.links = set(links)
        self.recipients = recipients or {}
        self.link_error = link_error
        self.role_calls = []
        self.recipient_calls = []

    async def resolve_role(self, operator_id, target_owner_id, now=None):
        self.role_calls.append((operator_id, target_owner_id))
        if self.link_error:
            raise self.link_error
        return "MEMBER" if (operator_id, target_owner_id) in self.links else None

    async def notification_recipients(self, owner_id, kind):
        self.recipient_calls.append((owner_id, kind))
        return list(self.recipients.get(owner_id, ()))

    async def authorize(self, *args, **kwargs):
        raise AssertionError("緊急回報不得走 SENSITIVE READ 授權")


class RecordingProfiles(FakeProfiles):
    def __init__(self, profiles=None):
        super().__init__(profiles)
        self.calls = []

    async def get_user_profile(self, user_id):
        self.calls.append(user_id)
        return await super().get_user_profile(user_id)


def _member_resolution(user_id=GRANDPA_ID):
    member = FamilyMember(
        user_id=user_id, display_name="王大明", relationship_type="grandparent"
    )
    return PersonResolution(
        kind="member",
        member=member,
        display_label="王大明",
        relationship="grandparent",
        matched_by="relationship",
    )


def _resolved_grandpa():
    return ResolvedAffected(GRANDPA, _member_resolution())


def _policy_service(auth, replier=None, profiles=None):
    return EmergencyFamilyAlertService(
        replier=replier or FakeReplier(),
        authorization_service=auth,
        user_profile_service=profiles or RecordingProfiles(),
    )


@pytest.mark.asyncio
async def test_self_report_notifies_the_reporters_own_family():
    service = _policy_service(PolicyAuthorization())
    patients = await service.patients_to_notify(OPERATOR, (ResolvedAffected(SELF),))
    assert patients == [OPERATOR]


@pytest.mark.asyncio
async def test_linked_grandpa_is_the_patient_not_the_reporter():
    auth = PolicyAuthorization(links={(OPERATOR, GRANDPA_ID)})
    patients = await _policy_service(auth).patients_to_notify(
        OPERATOR, (_resolved_grandpa(),)
    )
    assert patients == [GRANDPA_ID]
    assert auth.role_calls == [(OPERATOR, GRANDPA_ID)]


@pytest.mark.asyncio
async def test_grandpa_only_in_the_reporters_list_is_not_notified():
    """我在自己的名單裡列了對方，但對方的族譜裡沒有我：那是單方面的連結。"""
    patients = await _policy_service(PolicyAuthorization()).patients_to_notify(
        OPERATOR, (_resolved_grandpa(),)
    )
    assert patients == []


@pytest.mark.asyncio
async def test_link_check_failure_notifies_nobody():
    auth = PolicyAuthorization(
        links={(OPERATOR, GRANDPA_ID)}, link_error=RuntimeError("down")
    )
    patients = await _policy_service(auth).patients_to_notify(
        OPERATOR, (_resolved_grandpa(),)
    )
    assert patients == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    [
        ResolvedAffected(GRANDPA, PersonResolution(kind="ambiguous", display_label="阿公")),
        ResolvedAffected(GRANDPA, PersonResolution(kind="not_found", display_label="阿公")),
        ResolvedAffected(GRANDPA),
        ResolvedAffected(AffectedPerson(kind="third_party", label="路人", event="跌倒")),
        ResolvedAffected(AffectedPerson(kind="third_party", label="朋友", event="想自殺")),
    ],
    ids=["two-grandparents", "not-in-list", "lookup-failed", "passer-by", "friend"],
)
async def test_unresolved_people_never_notify_any_family(item):
    auth = PolicyAuthorization(links={(OPERATOR, GRANDPA_ID)})
    assert await _policy_service(auth).patients_to_notify(OPERATOR, (item,)) == []
    assert auth.role_calls == []




@pytest.mark.asyncio
async def test_self_and_grandpa_in_one_message_are_two_separate_patients():
    auth = PolicyAuthorization(links={(OPERATOR, GRANDPA_ID)})
    patients = await _policy_service(auth).patients_to_notify(
        OPERATOR, (_resolved_grandpa(), ResolvedAffected(SELF))
    )
    assert patients == [GRANDPA_ID, OPERATOR]


@pytest.mark.asyncio
async def test_people_without_an_urgent_condition_are_not_notified():
    mild_self = AffectedPerson(kind="self", event="有點頭痛", urgent=False)
    auth = PolicyAuthorization(links={(OPERATOR, GRANDPA_ID)})
    patients = await _policy_service(auth).patients_to_notify(
        OPERATOR, (_resolved_grandpa(), ResolvedAffected(mild_self))
    )
    assert patients == [GRANDPA_ID]


@pytest.mark.asyncio
async def test_recipients_follow_the_patients_policy_and_skip_the_reporter():
    """孫子若本身就是阿公的照顧者，他已經知道了，不必再收一張。"""
    replier = FakeReplier()
    auth = PolicyAuthorization(recipients={GRANDPA_ID: [GUARDIAN, OPERATOR]})
    service = _policy_service(auth, replier)

    assert await service.notify(GRANDPA_ID, REASON, reporter_id=OPERATOR) is True

    assert auth.recipient_calls == [(GRANDPA_ID, NOTIFICATION_KIND)]
    assert [uid for uid, _ in replier.flex] == [GUARDIAN]


@pytest.mark.asyncio
async def test_reporting_never_reads_the_patients_profile_for_the_reporter():
    """回報緊急事件不等於取得病人健康資料：不授權，回報者也收不到任何東西。

    收件人卡片上需要病人姓名，所以會為收件人讀病人的名字；回報者一則都不收。
    """
    replier = FakeReplier()
    profiles = RecordingProfiles({GRANDPA_ID: {"name": "王大明", "age": 82}})
    auth = PolicyAuthorization(
        links={(OPERATOR, GRANDPA_ID)}, recipients={GRANDPA_ID: [GUARDIAN]}
    )
    service = _policy_service(auth, replier, profiles)

    patients = await service.patients_to_notify(OPERATOR, (_resolved_grandpa(),))
    await service.notify(patients[0], REASON, reporter_id=OPERATOR)

    assert OPERATOR not in [uid for uid, _ in replier.flex + replier.texts]
    # 回報者的 profile 只讀名字（卡片要揭示回報者），不因回報取得任何病人資料。


@pytest.mark.asyncio
async def test_patient_without_recipients_is_not_reported_as_sent():
    replier = FakeReplier()
    service = _policy_service(PolicyAuthorization(recipients={GRANDPA_ID: []}), replier)
    assert await service.notify(GRANDPA_ID, REASON, reporter_id=OPERATOR) is False
    assert replier.flex == [] and replier.texts == []


@pytest.mark.asyncio
async def test_recipient_who_turned_off_family_alerts_is_skipped():
    replier = FakeReplier()
    profiles = RecordingProfiles({GUARDIAN: {"settings": {"notify_family": False}}})
    service = _policy_service(
        PolicyAuthorization(recipients={GRANDPA_ID: [GUARDIAN]}), replier, profiles
    )
    assert await service.notify(GRANDPA_ID, REASON, reporter_id=OPERATOR) is False
    assert replier.flex == [] and replier.texts == []


@pytest.mark.asyncio
async def test_push_failure_is_reported_as_not_sent():
    class _Down(FakeReplier):
        async def push_text(self, user_id, text):
            return False

    service = _policy_service(
        PolicyAuthorization(recipients={GRANDPA_ID: [GUARDIAN]}), _Down(flex_result=False)
    )
    assert await service.notify(GRANDPA_ID, REASON, reporter_id=OPERATOR) is False


# --- 正確標示回報者（10.16）----------------------------------------------------
#
# 病人本人發話才可以寫「剛才說」；別人代為回報時標成「{回報者} 回報」，原話保留
# 但不冒充病人發言。

REPORTED_WORDS = "我阿公跌倒了叫不醒"


def _card_text(**kwargs):
    kwargs.setdefault("patient_name", "王大明")
    kwargs.setdefault("reason", REASON)
    return json.dumps(build_emergency_family_bubble(**kwargs), ensure_ascii=False)


def test_self_report_keeps_just_said_wording():
    text = _card_text(words="我胸口好痛喘不過氣")
    assert "王大明 剛才說的話" in text
    assert "王大明 剛才在 CARE 描述的狀況" in text
    assert "回報" not in text


def test_third_party_report_is_labelled_as_the_reporters_words():
    """「阿公剛才說：我阿公跌倒」會讓家屬以為阿公還能自己打字。"""
    text = _card_text(words=REPORTED_WORDS, reporter_name="王小明")

    assert "王小明 剛才在 CARE 回報 王大明 的狀況" in text
    assert "王小明 回報的內容" in text
    assert REPORTED_WORDS in text
    assert "剛才說" not in text
    assert "王大明 剛才在 CARE 描述" not in text


def test_third_party_report_first_step_includes_the_reporter():
    """病人可能正叫不醒、接不了電話；回報者此刻就在旁邊。"""
    text = _card_text(words=REPORTED_WORDS, reporter_name="王小明")
    assert "先打電話給 王小明 或 王大明" in text


def test_reporter_without_a_name_gets_a_neutral_label():
    text = _card_text(words=REPORTED_WORDS, reporter_name="")
    assert "一位家人 回報的內容" in text
    assert "剛才說" not in text


def test_reported_card_without_words_has_no_quote_box():
    text = _card_text(words="", reporter_name="王小明")
    assert "回報的內容" not in text
    assert "王小明 剛才在 CARE 回報 王大明 的狀況" in text


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_reported_card_is_translated_and_passes_line_validation(language):
    from app.i18n.messages import t

    for key in (
        "emergency_family.lead_reported",
        "emergency_family.words_label_reported",
        "emergency_family.step.1_reported",
        "emergency_family.fallback_reporter",
    ):
        assert t(key, language) != key
    message = build_emergency_family_flex(
        patient_name="王大明",
        reason=REASON,
        words=REPORTED_WORDS,
        reporter_name="王小明",
        language=language,
    )
    FlexContainer.from_json(json.dumps(message.contents.to_dict(), ensure_ascii=False))
    text = json.dumps(message.contents.to_dict(), ensure_ascii=False)
    assert "王小明" in text and "王大明" in text


def _flex_text(replier):
    return [json.dumps(flex.contents.to_dict(), ensure_ascii=False) for _, flex in replier.flex]


@pytest.mark.asyncio
async def test_service_labels_a_report_about_someone_else():
    replier = FakeReplier()
    profiles = RecordingProfiles({GRANDPA_ID: {"name": "王大明"}, OPERATOR: {"name": "王小明"}})
    service = _policy_service(
        PolicyAuthorization(recipients={GRANDPA_ID: [GUARDIAN]}), replier, profiles
    )

    assert await service.notify(GRANDPA_ID, REASON, REPORTED_WORDS, reporter_id=OPERATOR)

    (card,) = _flex_text(replier)
    assert "王小明 回報的內容" in card
    assert REPORTED_WORDS in card
    assert "剛才說" not in card


@pytest.mark.asyncio
async def test_service_keeps_just_said_when_the_patient_is_the_reporter():
    replier = FakeReplier()
    profiles = RecordingProfiles({PATIENT: {"name": "王小明"}})
    service = _policy_service(
        PolicyAuthorization(recipients={PATIENT: ["U_SON"]}), replier, profiles
    )

    assert await service.notify(PATIENT, REASON, "我昏倒了", reporter_id=PATIENT)

    (card,) = _flex_text(replier)
    assert "王小明 剛才說的話" in card
    assert "回報" not in card


@pytest.mark.asyncio
async def test_service_without_a_reporter_is_a_self_report():
    """舊呼叫端（走失流程等）沒帶 reporter_id：維持本人發話的寫法。"""
    replier = FakeReplier()
    service = _policy_service(
        PolicyAuthorization(recipients={PATIENT: ["U_SON"]}),
        replier,
        RecordingProfiles({PATIENT: {"name": "王小明"}}),
    )

    assert await service.notify(PATIENT, REASON, "我昏倒了")

    (card,) = _flex_text(replier)
    assert "王小明 剛才說的話" in card
