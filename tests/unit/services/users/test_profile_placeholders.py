"""健康檔案的舊佔位值（年齡 0、身高 1、體重 1）不能被當成真資料讀走。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.core.user_age import is_pediatric_age, normalize_user_age
from app.models.user import UserProfileData, without_legacy_placeholders
from app.services.agent.utils.nodes import format_user_profile_prompt
from app.services.users.user_profile_service import UserProfileService

LEGACY_NEW_ACCOUNT = {
    "name": "王美玲",
    "gender": "unknown",
    "age": 0,
    "height": 1.0,
    "weight": 1.0,
    "chronic_diseases": ["diabetes"],
    "chronic_custom": [],
    "major_illness_history": "",
    "surgery_history": "",
}


def test_legacy_placeholders_become_none():
    cleaned = without_legacy_placeholders(LEGACY_NEW_ACCOUNT)

    assert cleaned["age"] is None
    assert cleaned["height"] is None
    assert cleaned["weight"] is None
    assert cleaned["chronic_diseases"] == ["diabetes"]
    assert cleaned["name"] == "王美玲"


def test_real_measurements_are_kept():
    cleaned = without_legacy_placeholders({"age": 72, "height": 158.0, "weight": 55.5})

    assert cleaned == {"age": 72, "height": 158.0, "weight": 55.5}


def test_input_is_not_mutated():
    original = dict(LEGACY_NEW_ACCOUNT)

    without_legacy_placeholders(original)

    assert original == LEGACY_NEW_ACCOUNT


def test_prompt_no_longer_claims_one_centimetre_and_one_kilogram():
    prompt = format_user_profile_prompt(without_legacy_placeholders(LEGACY_NEW_ACCOUNT))

    assert "1.0 cm" not in prompt
    assert "1.0 kg" not in prompt
    assert "身高：未提供" in prompt
    assert "體重：未提供" in prompt


def test_placeholder_age_no_longer_counts_as_a_child():
    """年齡 0 曾讓症狀分科把大人當成兒童（is_pediatric_age(0) 為 True）。"""
    age = without_legacy_placeholders(LEGACY_NEW_ACCOUNT)["age"]

    assert is_pediatric_age(normalize_user_age(age)) is False


@pytest.mark.asyncio
async def test_every_reader_gets_the_cleaned_profile():
    repo = MagicMock()
    repo.get_user_profile = AsyncMock(return_value=dict(LEGACY_NEW_ACCOUNT))

    profile = await UserProfileService(repo).get_user_profile("U1")

    assert profile["age"] is None
    assert profile["height"] is None
    assert profile["weight"] is None


@pytest.mark.asyncio
async def test_missing_profile_stays_none():
    repo = MagicMock()
    repo.get_user_profile = AsyncMock(return_value=None)

    assert await UserProfileService(repo).get_user_profile("U1") is None


@pytest.mark.asyncio
async def test_new_account_is_stored_without_placeholders():
    repo = MagicMock()
    repo.upsert_user_profile = AsyncMock(return_value=True)

    await UserProfileService(repo).create_default_user_profile("U1", display_name="王美玲")

    payload = repo.upsert_user_profile.await_args.args[1]
    assert payload["age"] is None
    assert payload["height"] is None
    assert payload["weight"] is None


@pytest.mark.parametrize("age", [0, 131])
def test_age_zero_is_rejected_because_it_reads_back_as_missing(age):
    with pytest.raises(ValidationError):
        UserProfileData(
            name="王美玲",
            gender="female",
            age=age,
            height=158.0,
            weight=55.5,
            chronic_diseases=[],
            chronic_custom=[],
            major_illness_history="",
            surgery_history="",
        )


def test_the_form_submission_still_requires_measurements():
    """本人送出的是整份表單；資料庫允許缺席，不代表 API 放寬。"""
    with pytest.raises(ValidationError):
        UserProfileData(
            name="王美玲",
            gender="female",
            chronic_diseases=[],
            chronic_custom=[],
            major_illness_history="",
            surgery_history="",
        )
