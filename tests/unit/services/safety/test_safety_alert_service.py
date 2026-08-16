import inspect
import json
import logging
from datetime import datetime, timezone

import pytest

from app.models.family_tree import FamilyMember, FamilyTree
from app.models.safety import DrugMention
from app.services.medication.drug_catalog_service import (
    DrugCatalogEntry,
    DrugCatalogService,
)
from app.services.safety.safety_alert_service import SafetyAlertService

PATIENT = "U_PATIENT"
JAPANESE_TEXT = "朋友從日本帶回來的合利他命強効錠 EX PLUS、パッケージは日本語です"
UNKNOWN_DRUG_TEXT = "這個藥叫做某某錠可以吃嗎"


class FakeExtractor:
    def __init__(self, mentions=None, error=None):
        self._mentions = mentions or []
        self._error = error
        self.calls = []

    async def extract(self, text):
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        return list(self._mentions)


class FakeAlertRepository:
    def __init__(self, granted=True):
        self._granted = granted
        self.claims = []

    async def try_claim(self, user_id, drug_key, risk_level, ttl_hours):
        self.claims.append(
            {
                "user_id": user_id,
                "drug_key": drug_key,
                "risk_level": risk_level,
                "ttl_hours": ttl_hours,
            }
        )
        return self._granted


class FakeFamilyTreeRepository:
    def __init__(self, member_ids=None, tree=True, error=None):
        self._member_ids = member_ids or []
        self._tree = tree
        self._error = error

    async def get_by_user_id(self, user_id):
        if self._error is not None:
            raise self._error
        if not self._tree:
            return None
        now = datetime.now(timezone.utc)
        return FamilyTree(
            user_id=user_id,
            family_members=[FamilyMember(user_id=mid) for mid in self._member_ids],
            created_at=now,
            updated_at=now,
        )


class FakeReplier:
    def __init__(self):
        self.flex_pushes = []
        self.text_pushes = []

    async def push_flex(self, user_id, flex_message):
        self.flex_pushes.append((user_id, flex_message))
        return True

    async def push_text(self, user_id, text):
        self.text_pushes.append((user_id, text))
        return True


class FakeUserProfileService:
    def __init__(self, name="王大明"):
        self._name = name

    async def get_user_profile(self, user_id):
        return {
            "name": self._name,
            "settings": {"language": "zh-TW", "font_size": "large"},
        }


def _catalog() -> DrugCatalogService:
    return DrugCatalogService(
        [
            DrugCatalogEntry(
                license_number="衛署藥輸字第025431號", name_zh="合利他命 強效錠"
            ),
            DrugCatalogEntry(
                license_number="衛署藥製字第012345號", name_zh="普拿疼錠500毫克"
            ),
        ],
        threshold=0.88,
    )


def _service(
    extractor,
    alert_repository=None,
    family_tree_repository=None,
    replier=None,
    catalog=None,
    user_profile_service=None,
):
    return SafetyAlertService(
        extractor=extractor,
        catalog_service=catalog if catalog is not None else _catalog(),
        alert_repository=alert_repository or FakeAlertRepository(),
        family_tree_repository=family_tree_repository or FakeFamilyTreeRepository(),
        replier=replier or FakeReplier(),
        user_profile_service=user_profile_service or FakeUserProfileService(),
        dedupe_hours=24,
    )


async def test_none_sends_nothing():
    """台灣核准藥、正常通路：不介入。"""
    replier = FakeReplier()
    service = _service(
        FakeExtractor([DrugMention(raw_name="普拿疼錠500毫克")]), replier=replier
    )

    await service.check(PATIENT, "普拿疼錠500毫克一次吃幾顆")

    assert replier.flex_pushes == []
    assert replier.text_pushes == []


async def test_low_replies_to_the_patient_only():
    replier = FakeReplier()
    family = FakeFamilyTreeRepository(member_ids=["U_A", "U_B"])
    service = _service(
        FakeExtractor([DrugMention(raw_name="某某錠")]),
        replier=replier,
        family_tree_repository=family,
    )

    await service.check(PATIENT, UNKNOWN_DRUG_TEXT)

    assert [user_id for user_id, _ in replier.text_pushes] == [PATIENT]
    assert replier.flex_pushes == []


async def test_high_alerts_every_family_member_and_the_patient():
    replier = FakeReplier()
    service = _service(
        FakeExtractor([DrugMention(raw_name="合利他命強効錠 EX PLUS")]),
        replier=replier,
        family_tree_repository=FakeFamilyTreeRepository(
            member_ids=["U_A", "U_B", "U_C"]
        ),
    )

    await service.check(PATIENT, JAPANESE_TEXT)

    assert sorted(user_id for user_id, _ in replier.flex_pushes) == [
        "U_A",
        "U_B",
        "U_C",
    ]
    assert [user_id for user_id, _ in replier.text_pushes] == [PATIENT]


async def test_high_tells_the_patient_the_family_was_told():
    """SHALL NOT 在當事人不知情的情況下通報。"""
    replier = FakeReplier()
    service = _service(
        FakeExtractor([DrugMention(raw_name="合利他命強効錠 EX PLUS")]),
        replier=replier,
        family_tree_repository=FakeFamilyTreeRepository(member_ids=["U_A"]),
    )

    await service.check(PATIENT, JAPANESE_TEXT)

    assert "家人" in replier.text_pushes[0][1]


async def test_high_claims_the_notification_right_with_a_normalized_key():
    repository = FakeAlertRepository()
    service = _service(
        FakeExtractor([DrugMention(raw_name="合利他命強効錠 EX PLUS")]),
        alert_repository=repository,
    )

    await service.check(PATIENT, JAPANESE_TEXT)

    assert repository.claims[0]["user_id"] == PATIENT
    assert repository.claims[0]["risk_level"] == "high"
    assert repository.claims[0]["ttl_hours"] == 24
    assert " " not in repository.claims[0]["drug_key"]


async def test_high_sends_nothing_without_the_notification_right():
    """節流期間內已通報過：家人與當事人都不再收到。"""
    replier = FakeReplier()
    service = _service(
        FakeExtractor([DrugMention(raw_name="合利他命強効錠 EX PLUS")]),
        alert_repository=FakeAlertRepository(granted=False),
        replier=replier,
        family_tree_repository=FakeFamilyTreeRepository(member_ids=["U_A"]),
    )

    await service.check(PATIENT, JAPANESE_TEXT)

    assert replier.flex_pushes == []
    assert replier.text_pushes == []


async def test_high_still_replies_to_the_patient_without_a_family_tree():
    replier = FakeReplier()
    service = _service(
        FakeExtractor([DrugMention(raw_name="合利他命強効錠 EX PLUS")]),
        replier=replier,
        family_tree_repository=FakeFamilyTreeRepository(tree=False),
    )

    await service.check(PATIENT, JAPANESE_TEXT)

    assert replier.flex_pushes == []
    assert [user_id for user_id, _ in replier.text_pushes] == [PATIENT]


async def test_high_survives_a_failing_family_tree_lookup():
    replier = FakeReplier()
    service = _service(
        FakeExtractor([DrugMention(raw_name="合利他命強効錠 EX PLUS")]),
        replier=replier,
        family_tree_repository=FakeFamilyTreeRepository(error=RuntimeError("db down")),
    )

    await service.check(PATIENT, JAPANESE_TEXT)


async def test_extraction_failure_is_silent_and_sends_nothing():
    replier = FakeReplier()
    service = _service(
        FakeExtractor(error=RuntimeError("boom")),
        replier=replier,
    )

    await service.check(PATIENT, JAPANESE_TEXT)

    assert replier.flex_pushes == []
    assert replier.text_pushes == []


async def test_prefilter_blocks_the_model_call():
    extractor = FakeExtractor()
    service = _service(extractor)

    await service.check(PATIENT, "明天下午三點要去公園散步")

    assert extractor.calls == []


async def test_missing_catalog_skips_assessment_entirely():
    """藥證庫缺席時 SHALL NOT 判定、SHALL NOT 通報——判定的一半資料不在。"""
    extractor = FakeExtractor([DrugMention(raw_name="某某錠")])
    replier = FakeReplier()
    service = _service(
        extractor,
        replier=replier,
        catalog=DrugCatalogService([], threshold=0.88),
    )

    await service.check(PATIENT, UNKNOWN_DRUG_TEXT)

    assert extractor.calls == []
    assert replier.text_pushes == []


async def test_catalog_match_fills_in_the_hit_before_assessing():
    """抽取器不填 catalog_hit；沒有這一步，核准藥會被當成查無而誤報 low。"""
    replier = FakeReplier()
    service = _service(
        FakeExtractor([DrugMention(raw_name="普拿疼")]), replier=replier
    )

    await service.check(PATIENT, "普拿疼可以吃嗎")

    assert replier.text_pushes == []


async def test_family_alert_does_not_carry_the_original_text():
    replier = FakeReplier()
    service = _service(
        FakeExtractor([DrugMention(raw_name="合利他命強効錠 EX PLUS")]),
        replier=replier,
        family_tree_repository=FakeFamilyTreeRepository(member_ids=["U_A"]),
    )

    await service.check(PATIENT, JAPANESE_TEXT)

    _, flex = replier.flex_pushes[0]
    payload = json.dumps(flex.contents.to_dict(), ensure_ascii=False)
    assert "朋友從日本帶回來的" not in payload


async def test_log_does_not_leak_the_input_text(caplog):
    service = _service(FakeExtractor(error=RuntimeError("boom")))

    with caplog.at_level(logging.WARNING):
        await service.check(PATIENT, JAPANESE_TEXT)

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "合利他命" not in logged


def test_service_never_reaches_medication_data():
    """偵測不建檔：藥袋建檔有自己的確認閘門，背景偵測不得繞過它。"""
    import app.services.safety.safety_alert_service as module

    source = inspect.getsource(module)

    assert "medication_repository" not in source
    assert "medication_service" not in source
    assert "MedicationRepository" not in source
    assert "MedicationService" not in source
