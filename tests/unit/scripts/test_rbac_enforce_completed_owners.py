from scripts.rbac_enforce_completed_owners import ready_owner_ids


def test_selects_only_owners_whose_every_member_has_a_role():
    docs = [
        {
            "user_id": "U-done",
            "family_members": [
                {"user_id": "a", "family_role": "GUARDIAN"},
                {"user_id": "b", "family_role": "MEMBER"},
            ],
        },
        {
            "user_id": "U-half",
            "family_members": [
                {"user_id": "a", "family_role": "GUARDIAN"},
                {"user_id": "b"},
            ],
        },
        {
            "user_id": "U-blank-role",
            "family_members": [{"user_id": "a", "family_role": ""}],
        },
        {"user_id": "U-empty", "family_members": []},
        {
            "user_id": "U-already",
            "rbac_migration_state": "enforced",
            "family_members": [{"user_id": "a", "family_role": "CAREGIVER"}],
        },
    ]

    assert ready_owner_ids(docs) == ["U-done"]
