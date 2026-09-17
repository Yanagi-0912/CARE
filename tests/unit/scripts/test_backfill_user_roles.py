"""`backfill_user_roles.py` 以前無條件把全體設成 admin；現在對象要明說、預設 dry-run。"""

import pytest

from scripts.backfill_user_roles import (
    build_query,
    confirm_all,
    main,
    parse_args,
    plan_role_changes,
)


def test_target_is_required():
    with pytest.raises(SystemExit):
        parse_args([])


def test_line_id_and_all_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse_args(["--line-id", "U1", "--all"])


def test_default_is_dry_run_with_admin_role():
    args = parse_args(["--line-id", "U1", "--line-id", "U2"])

    assert args.apply is False
    assert args.role == "admin"
    assert args.line_ids == ["U1", "U2"]


def test_build_query_limits_to_given_line_ids():
    assert build_query(["U1", "U2", "U1"]) == {"line_id": {"$in": ["U1", "U2"]}}
    assert build_query(None) == {}


def test_plan_skips_users_already_in_role_and_without_line_id():
    docs = [
        {"_id": 1, "line_id": "U1", "role": "user"},
        {"_id": 2, "line_id": "U2", "role": "admin"},
        {"_id": 3, "role": "user"},
        {"_id": 4, "line_id": "U4"},
    ]

    changes = plan_role_changes(docs, "admin")

    assert [(c["_id"], c["from"], c["to"]) for c in changes] == [
        (1, "user", "admin"),
        (4, None, "admin"),
    ]


def test_confirm_all_requires_exact_token():
    assert confirm_all(lambda _prompt: "ALL") is True
    assert confirm_all(lambda _prompt: "all") is False
    assert confirm_all(lambda _prompt: "") is False


class _FakeResult:
    matched_count = 1
    modified_count = 1


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs
        self.updates = []

    def find(self, query, projection=None):
        ids = (query.get("line_id") or {}).get("$in")
        return [d for d in self._docs if ids is None or d.get("line_id") in ids]

    def update_many(self, query, update):
        self.updates.append((query, update))
        return _FakeResult()


def _run(monkeypatch, argv, docs, answer=None):
    collection = _FakeCollection(docs)
    monkeypatch.setenv("MONGODB_URI", "mongodb://test")
    monkeypatch.setattr("scripts.backfill_user_roles.pymongo.MongoClient", lambda uri: {"CARE_database": {"users": collection}})
    monkeypatch.setattr("scripts.backfill_user_roles.load_dotenv", lambda: None)
    if answer is not None:
        monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    code = main(argv)
    return code, collection


def test_dry_run_writes_nothing(monkeypatch, capsys):
    code, collection = _run(monkeypatch, ["--line-id", "U1"], [{"_id": 1, "line_id": "U1", "role": "user"}])

    assert code == 0
    assert collection.updates == []
    assert "dry-run" in capsys.readouterr().out


def test_apply_with_line_ids_updates_only_those(monkeypatch):
    docs = [{"_id": 1, "line_id": "U1", "role": "user"}, {"_id": 2, "line_id": "U2", "role": "user"}]

    code, collection = _run(monkeypatch, ["--line-id", "U1", "--apply"], docs)

    assert code == 0
    assert collection.updates == [({"_id": {"$in": [1]}}, {"$set": {"role": "admin"}})]


def test_apply_all_without_confirmation_writes_nothing(monkeypatch):
    docs = [{"_id": 1, "line_id": "U1", "role": "user"}]

    code, collection = _run(monkeypatch, ["--all", "--apply"], docs, answer="yes")

    assert code == 1
    assert collection.updates == []


def test_apply_all_with_typed_confirmation_writes(monkeypatch):
    docs = [{"_id": 1, "line_id": "U1", "role": "user"}]

    code, collection = _run(monkeypatch, ["--all", "--apply"], docs, answer="ALL")

    assert code == 0
    assert len(collection.updates) == 1
