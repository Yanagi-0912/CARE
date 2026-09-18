"""``HealthAlertService``：誰收得到、收到什麼、什麼時候不送
（health-alerts spec「只有超出範圍才推播」「超出範圍的推播對象」「重複
推播的節流」「過時的補記不推播」「推播失敗不影響紀錄」「推播總開關」
「經期異常只通知本人」）。

沿用 ``test_emergency_alert_service.py`` 的替身風格：不使用
``unittest.mock.patch``，收件人判定／使用者設定／推播全部是注入的假物件；
claim repository 額外模擬「有沒有過期」，讓節流的兩個方向（視窗內只推一次、
視窗過後可以再推）都測得到。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from linebot.v3.messaging import FlexMessage

from app.models.family_tree import FamilyMember, FamilyTree
from app.models.health import HealthAlertThreshold, HealthMeasurement
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.services.health.health_alert_service import (
    NOTIFICATION_KIND,
    HealthAlertService,
)

OWNER = "U_ELDER"
GUARDIAN = "U_GUARDIAN"
CAREGIVER = "U_CAREGIVER"


class _FakeReplier:
    def __init__(self, flex_ok: bool = True, raise_on_push: bool = False) -> None:
        self.flex: List[tuple] = []
        self.texts: List[tuple] = []
        self._flex_ok = flex_ok
        self._raise = raise_on_push

    async def push_flex(self, user_id: str, flex: Any) -> bool:
        if self._raise:
            raise RuntimeError("LINE 掛了")
        assert isinstance(flex, FlexMessage)
        if not self._flex_ok:
            return False
        self.flex.append((user_id, flex))
        return True

    async def push_text(self, user_id: str, text: str) -> bool:
        if self._raise:
            raise RuntimeError("LINE 掛了")
        self.texts.append((user_id, text))
        return True


class _FakeClaimRepository:
    """模擬 ``(user_id, alert_key)`` 唯一 + TTL 的節流語意，時鐘由測試控制。"""

    def __init__(self, clock) -> None:
        self._clock = clock
        self._expires: Dict[tuple, datetime] = {}
        self.calls: List[tuple] = []

    async def try_claim(self, user_id: str, alert_key: str, ttl_minutes: int) -> bool:
        self.calls.append((user_id, alert_key, ttl_minutes))
        key = (user_id, alert_key)
        expires_at = self._expires.get(key)
        now = self._clock()
        if expires_at is not None and expires_at > now:
            return False
        self._expires[key] = now + timedelta(minutes=ttl_minutes)
        return True


class _FakeAuthorization:
    def __init__(self, recipients=(), error: Optional[Exception] = None) -> None:
        self._recipients = list(recipients)
        self._error = error
        self.calls: List[tuple] = []

    async def notification_recipients(
        self, owner_id: str, kind: str, has_legacy_equivalent: bool = True
    ) -> List[str]:
        self.calls.append((owner_id, kind, has_legacy_equivalent))
        if self._error is not None:
            raise self._error
        return list(self._recipients)


class _FakeProfiles:
    def __init__(
        self,
        profiles: Optional[Dict[str, dict]] = None,
        *,
        error_for: Optional[set] = None,
    ) -> None:
        self._profiles = profiles or {}
        # Task 7：模擬個人資料儲存層對特定使用者短暫故障（不是「查無此人」
        # ——查無此人回 None，故障是拋例外）。
        self._error_for = error_for or set()

    async def get_user_profile(self, user_id: str):
        if user_id in self._error_for:
            raise RuntimeError("個人資料服務掛了")
        return self._profiles.get(user_id)


def _collect_text_nodes(node) -> list:
    """遞迴取出 Flex 節點樹裡所有可見的 ``text`` 內容（同
    ``tests/unit/services/line_messaging/flex/test_health_alert_flex.py`` 的
    ``_collect_text_nodes``）：只有 ``type: text`` 節點的 ``text`` 才是使用者
    會讀到的推播文字，版面本身（顏色碼、layout 關鍵字、按鈕 uri）不是。"""
    texts: list = []
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            texts.append(node["text"])
        for value in node.values():
            texts.extend(_collect_text_nodes(value))
    elif isinstance(node, list):
        for item in node:
            texts.extend(_collect_text_nodes(item))
    return texts


def _clock_box(start: datetime):
    box = {"now": start}

    def clock() -> datetime:
        return box["now"]

    return box, clock


def _measurement(
    *,
    kind: str = "blood_pressure",
    level: str = "above_range",
    measured_at: Optional[datetime] = None,
    recorded_by: str = OWNER,
    systolic: int = 152,
    diastolic: int = 90,
    glucose_mg_dl: int = 65,
    meal_context: str = "fasting",
) -> HealthMeasurement:
    now = measured_at or datetime.now(timezone.utc)
    if kind == "blood_pressure":
        return HealthMeasurement(
            id="M1",
            user_id=OWNER,
            kind=kind,
            measured_at=now,
            recorded_by=recorded_by,
            systolic=systolic,
            diastolic=diastolic,
            level=level,
        )
    return HealthMeasurement(
        id="M1",
        user_id=OWNER,
        kind=kind,
        measured_at=now,
        recorded_by=recorded_by,
        glucose_mg_dl=glucose_mg_dl,
        meal_context=meal_context,
        level=level,
    )


def _threshold(**kwargs) -> HealthAlertThreshold:
    return HealthAlertThreshold(user_id=OWNER, updated_by=OWNER, **kwargs)


def _service(
    *,
    enabled: bool = True,
    recipients=(),
    profiles=None,
    profile_error_for=None,
    replier=None,
    clock=None,
    auth_error=None,
):
    clock = clock or (lambda: datetime.now(timezone.utc))
    claims = _FakeClaimRepository(clock)
    auth = _FakeAuthorization(recipients, auth_error)
    service = HealthAlertService(
        replier=replier or _FakeReplier(),
        claim_repository=claims,
        authorization_service=auth,
        user_profile_service=_FakeProfiles(profiles, error_for=profile_error_for),
        enabled=enabled,
        liff_url="https://liff.line.me/1234",
        clock=clock,
    )
    return service, claims, auth


# ── 只有超出範圍才推播 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_within_range_does_not_push():
    service, claims, _ = _service(recipients=(GUARDIAN,))
    replier = service._replier

    await service.notify_out_of_range(_measurement(level="within_range"), _threshold())

    assert claims.calls == []
    assert replier.flex == []


@pytest.mark.asyncio
async def test_no_threshold_does_not_push():
    service, claims, _ = _service(recipients=(GUARDIAN,))

    await service.notify_out_of_range(_measurement(level="no_threshold"), None)

    assert claims.calls == []


@pytest.mark.asyncio
async def test_above_range_pushes():
    replier = _FakeReplier()
    service, claims, _ = _service(recipients=(), replier=replier)

    await service.notify_out_of_range(
        _measurement(level="above_range"), _threshold(systolic_high=140)
    )

    assert [uid for uid, _ in replier.flex] == [OWNER]


# ── 推播總開關 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_switch_sends_nothing_and_claims_nothing():
    service, claims, _ = _service(enabled=False, recipients=(GUARDIAN,))
    replier = service._replier

    await service.notify_out_of_range(
        _measurement(level="above_range"), _threshold(systolic_high=140)
    )

    assert claims.calls == []
    assert replier.flex == [] and replier.texts == []


# ── 超出範圍的推播對象 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_family_recipients_from_authorization_are_notified():
    replier = _FakeReplier()
    service, claims, auth = _service(recipients=(GUARDIAN, CAREGIVER), replier=replier)

    await service.notify_out_of_range(
        _measurement(level="above_range"), _threshold(systolic_high=140)
    )

    assert set(uid for uid, _ in replier.flex) == {OWNER, GUARDIAN, CAREGIVER}


@pytest.mark.asyncio
async def test_asks_authorization_service_for_the_right_kind_without_legacy_equivalent():
    service, _, auth = _service(recipients=(GUARDIAN,))

    await service.notify_out_of_range(
        _measurement(level="above_range"), _threshold(systolic_high=140)
    )

    assert auth.calls == [(OWNER, NOTIFICATION_KIND, False)]


class _FakeTrees:
    """同 ``tests/unit/routers/test_health_router_authorization.py`` 的假
    ``FamilyTreeRepository``：只回傳整份 ``FamilyTree``，角色解析交給真正的
    ``FamilyAuthorizationService`` 完成。"""

    def __init__(self, trees: Dict[str, FamilyTree]) -> None:
        self._trees = trees

    async def get_by_user_id(self, user_id: str) -> Optional[FamilyTree]:
        return self._trees.get(user_id)


class _NoDelegations:
    async def has_active_delegation(self, owner_id, delegate_user_id, now=None) -> bool:
        return False


@pytest.mark.asyncio
async def test_real_authorization_service_filters_to_guardian_and_caregiver_even_in_shadow_state():
    """規則本身有沒有被正確套用的回歸守門：上面的測試都用一個直接回傳固定
    清單的假 authorization service，只驗證了「接線」（呼叫了哪個 kind、傳了
    ``has_legacy_equivalent=False``），沒有任何一個測試真正跑過角色解析或
    遷移狀態判斷。這裡改用真正的 ``FamilyAuthorizationService``（repository
    換成假的，同 router 授權測試的方法論），族譜裡有 GUARDIAN、CAREGIVER、
    MEMBER 與一位尚未指派角色的成員，且家庭**仍在 shadow 遷移狀態**——
    ``health_out_of_range`` 是 ``has_legacy_equivalent=False`` 呼叫的種類，
    SHALL NOT 受影子模式放寬，收件人 SHALL 只有本人＋GUARDIAN＋CAREGIVER
    （health-alerts spec「超出範圍的推播對象」：「MEMBER 與尚未被指派角色的
    家人 SHALL NOT 收到」「收件人判定 SHALL NOT 受遷移狀態影響」）。
    """
    guardian, caregiver, member, unassigned = (
        "U_GUARDIAN2",
        "U_CAREGIVER2",
        "U_MEMBER2",
        "U_UNASSIGNED2",
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    tree = FamilyTree(
        user_id=OWNER,
        family_members=[
            FamilyMember(user_id=guardian, family_role="GUARDIAN"),
            FamilyMember(user_id=caregiver, family_role="CAREGIVER"),
            FamilyMember(user_id=member, family_role="MEMBER"),
            FamilyMember(user_id=unassigned, family_role=None),
        ],
        rbac_migration_state="shadow",
        created_at=now,
        updated_at=now,
    )
    real_authorization = FamilyAuthorizationService(
        family_tree_repository=_FakeTrees({OWNER: tree}),
        delegation_repository=_NoDelegations(),
        enforcement_enabled=True,
    )
    replier = _FakeReplier()
    claims = _FakeClaimRepository(lambda: now)
    service = HealthAlertService(
        replier=replier,
        claim_repository=claims,
        authorization_service=real_authorization,
        user_profile_service=_FakeProfiles(),
        enabled=True,
        liff_url="https://liff.line.me/1234",
        clock=lambda: now,
    )

    await service.notify_out_of_range(
        _measurement(level="above_range", measured_at=now), _threshold(systolic_high=140)
    )

    assert set(uid for uid, _ in replier.flex) == {OWNER, guardian, caregiver}


@pytest.mark.asyncio
async def test_family_recipient_who_opted_out_is_skipped_but_others_are_not():
    replier = _FakeReplier()
    service, _, _ = _service(
        recipients=(GUARDIAN, CAREGIVER),
        profiles={GUARDIAN: {"settings": {"notify_family": False}}},
        replier=replier,
    )

    await service.notify_out_of_range(
        _measurement(level="above_range"), _threshold(systolic_high=140)
    )

    assert set(uid for uid, _ in replier.flex) == {OWNER, CAREGIVER}


@pytest.mark.asyncio
async def test_family_recipient_is_skipped_when_profile_read_fails():
    """Task 7：個人資料儲存層讀取失敗時，家人收件人 SHALL fail 關（視同
    ``notify_family=False``），不是 fail 開。一位家人已經明確關閉推播
    （見 ``GUARDIAN`` 的設定）而另一位從未讀過設定就故障（``CAREGIVER``）
    ——兩者都不該收到，基礎設施錯誤 SHALL NOT 覆寫成「照樣推播」。本人不
    受影響，仍然收得到（本人不經這裡的 notify_family 判定）。"""
    replier = _FakeReplier()
    service, _, _ = _service(
        recipients=(GUARDIAN, CAREGIVER),
        profiles={GUARDIAN: {"settings": {"notify_family": False}}},
        profile_error_for={CAREGIVER},
        replier=replier,
    )

    await service.notify_out_of_range(
        _measurement(level="above_range"), _threshold(systolic_high=140)
    )

    assert set(uid for uid, _ in replier.flex) == {OWNER}


@pytest.mark.asyncio
async def test_owner_is_notified_even_when_owner_has_notify_family_off():
    """本人的收件 SHALL NOT 受 notify_family 影響（spec「本人收到」）。"""
    replier = _FakeReplier()
    service, _, _ = _service(
        recipients=(),
        profiles={OWNER: {"settings": {"notify_family": False}}},
        replier=replier,
    )

    await service.notify_out_of_range(
        _measurement(level="above_range"), _threshold(systolic_high=140)
    )

    assert [uid for uid, _ in replier.flex] == [OWNER]


@pytest.mark.asyncio
async def test_owner_is_not_duplicated_if_authorization_also_returns_owner():
    replier = _FakeReplier()
    service, _, _ = _service(recipients=(OWNER, GUARDIAN), replier=replier)

    await service.notify_out_of_range(
        _measurement(level="above_range"), _threshold(systolic_high=140)
    )

    assert [uid for uid, _ in replier.flex].count(OWNER) == 1


@pytest.mark.asyncio
async def test_authorization_failure_still_notifies_the_owner():
    """對主流程 fail-open：收件人判定失敗時，本人（不經過那次判定）仍該
    收到自己的警示。"""
    replier = _FakeReplier()
    service, _, _ = _service(auth_error=RuntimeError("db down"), replier=replier)

    await service.notify_out_of_range(
        _measurement(level="above_range"), _threshold(systolic_high=140)
    )

    assert [uid for uid, _ in replier.flex] == [OWNER]


# ── 重複推播的節流 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_pushes_within_30_minutes_only_sends_once():
    replier = _FakeReplier()
    box, clock = _clock_box(datetime(2026, 1, 1, tzinfo=timezone.utc))
    service, claims, _ = _service(replier=replier, clock=clock)

    await service.notify_out_of_range(
        _measurement(level="above_range", measured_at=clock()), _threshold(systolic_high=140)
    )
    box["now"] = box["now"] + timedelta(minutes=10)
    await service.notify_out_of_range(
        _measurement(level="above_range", measured_at=clock()), _threshold(systolic_high=140)
    )

    assert len(replier.flex) == 1


@pytest.mark.asyncio
async def test_push_again_after_throttle_window_passes():
    replier = _FakeReplier()
    box, clock = _clock_box(datetime(2026, 1, 1, tzinfo=timezone.utc))
    service, claims, _ = _service(replier=replier, clock=clock)

    await service.notify_out_of_range(
        _measurement(level="above_range", measured_at=clock()), _threshold(systolic_high=140)
    )
    box["now"] = box["now"] + timedelta(minutes=40)
    await service.notify_out_of_range(
        _measurement(level="above_range", measured_at=clock()), _threshold(systolic_high=140)
    )

    assert len(replier.flex) == 2


@pytest.mark.asyncio
async def test_different_categories_throttle_independently():
    replier = _FakeReplier()
    box, clock = _clock_box(datetime(2026, 1, 1, tzinfo=timezone.utc))
    service, claims, _ = _service(replier=replier, clock=clock)

    await service.notify_out_of_range(
        _measurement(kind="blood_pressure", level="above_range", measured_at=clock()),
        _threshold(systolic_high=140),
    )
    await service.notify_out_of_range(
        _measurement(kind="blood_glucose", level="below_range", measured_at=clock()),
        _threshold(glucose_low=70),
    )

    assert len(replier.flex) == 2
    assert {key for _, key, _ in claims.calls} == {"bp_high", "glucose_low"}


# ── 過時的補記不推播 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_measurement_more_than_6_hours_old_does_not_push():
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    service, claims, _ = _service(clock=lambda: now)
    replier = service._replier

    stale = now - timedelta(hours=7)
    await service.notify_out_of_range(
        _measurement(level="above_range", measured_at=stale), _threshold(systolic_high=140)
    )

    assert claims.calls == []
    assert replier.flex == []


@pytest.mark.asyncio
async def test_measurement_within_6_hours_still_pushes():
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    service, claims, _ = _service(clock=lambda: now)
    replier = service._replier

    recent = now - timedelta(hours=5)
    await service.notify_out_of_range(
        _measurement(level="above_range", measured_at=recent), _threshold(systolic_high=140)
    )

    assert len(replier.flex) == 1


# ── 推播失敗不影響紀錄（服務層自己吞掉，呼叫端不需要包 try/except） ───────


@pytest.mark.asyncio
async def test_push_failure_is_swallowed():
    replier = _FakeReplier(raise_on_push=True)
    service, _, _ = _service(recipients=(GUARDIAN,), replier=replier)

    # 不該拋出例外。
    await service.notify_out_of_range(
        _measurement(level="above_range"), _threshold(systolic_high=140)
    )


# ── 內容：代記者 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_proxy_recorded_measurement_names_the_recorder():
    import json

    replier = _FakeReplier()
    service, _, _ = _service(
        recipients=(),
        profiles={GUARDIAN: {"name": "小美"}},
        replier=replier,
    )

    await service.notify_out_of_range(
        _measurement(level="above_range", recorded_by=GUARDIAN),
        _threshold(systolic_high=140),
    )

    payload = json.dumps(replier.flex[0][1].contents.to_dict(), ensure_ascii=False)
    assert "小美" in payload


@pytest.mark.asyncio
async def test_self_recorded_measurement_has_no_recorder_note():
    replier = _FakeReplier()
    service, _, _ = _service(recipients=(), replier=replier)

    await service.notify_out_of_range(
        _measurement(level="above_range", recorded_by=OWNER),
        _threshold(systolic_high=140),
    )

    # 「由 ○○ 記錄」那一行只在他人代記時才出現。不對整份 dict 字串化後檢查
    # ——版面本身（顏色十六進位碼、layout 關鍵字、按鈕 uri）含有文字是正常
    # 的，只有 `type: text` 節點的 `text` 才是使用者會讀到的內容；字串比對
    # 版本曾經只因為按鈕文案用「紀錄」（紀，不是記）才恰好過關，換一個按鈕
    # 文案就會悄悄失效（同 test_health_alert_flex.py 的 `_collect_text_nodes`
    # 理由）。
    visible_text = "\n".join(_collect_text_nodes(replier.flex[0][1].contents.to_dict()))
    assert "記錄" not in visible_text


# ── 經期異常：只通知本人、最多一次 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_menstrual_anomaly_notifies_only_the_owner():
    replier = _FakeReplier()
    service, claims, auth = _service(replier=replier)

    await service.notify_menstrual_anomaly(OWNER, "R1")

    assert [uid for uid, _ in replier.flex] == [OWNER]
    assert auth.calls == []  # SHALL NOT 呼叫 notification_recipients


@pytest.mark.asyncio
async def test_menstrual_anomaly_notifies_the_same_record_only_once():
    replier = _FakeReplier()
    service, _, _ = _service(replier=replier)

    await service.notify_menstrual_anomaly(OWNER, "R1")
    await service.notify_menstrual_anomaly(OWNER, "R1")

    assert len(replier.flex) == 1


@pytest.mark.asyncio
async def test_menstrual_anomaly_disabled_switch_sends_nothing():
    service, claims, _ = _service(enabled=False)
    replier = service._replier

    await service.notify_menstrual_anomaly(OWNER, "R1")

    assert claims.calls == []
    assert replier.flex == []


@pytest.mark.asyncio
async def test_different_records_each_get_their_own_notification():
    replier = _FakeReplier()
    service, _, _ = _service(replier=replier)

    await service.notify_menstrual_anomaly(OWNER, "R1")
    await service.notify_menstrual_anomaly(OWNER, "R2")

    assert len(replier.flex) == 2


@pytest.mark.asyncio
async def test_menstrual_push_failure_is_swallowed():
    replier = _FakeReplier(raise_on_push=True)
    service, _, _ = _service(replier=replier)

    await service.notify_menstrual_anomaly(OWNER, "R1")
