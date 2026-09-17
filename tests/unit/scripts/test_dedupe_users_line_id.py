"""`dedupe_users_line_id.py` 的合併判斷：保留最舊、補空欄位、不蓋既有值。"""

from datetime import datetime, timezone

from scripts.dedupe_users_line_id import apply_plans, merge_fields, plan_dedupe


def _dt(day, hour=0):
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc)


def test_keeps_oldest_by_created_at_and_deletes_the_rest():
    docs = [
        {"_id": "b", "line_id": "U1", "created_at": _dt(2)},
        {"_id": "a", "line_id": "U1", "created_at": _dt(1)},
        {"_id": "c", "line_id": "U1", "created_at": _dt(3)},
        {"_id": "z", "line_id": "U2", "created_at": _dt(1)},
    ]

    [plan] = plan_dedupe(docs)

    assert plan["line_id"] == "U1"
    assert plan["keep_id"] == "a"
    assert plan["delete_ids"] == ["b", "c"]


def test_documents_without_created_at_are_never_the_keeper():
    docs = [
        {"_id": "no-ts", "line_id": "U1"},
        {"_id": "ts", "line_id": "U1", "created_at": _dt(5)},
    ]

    [plan] = plan_dedupe(docs)

    assert plan["keep_id"] == "ts"


def test_ties_break_by_id():
    docs = [
        {"_id": "y", "line_id": "U1", "created_at": _dt(1)},
        {"_id": "x", "line_id": "U1", "created_at": _dt(1)},
    ]

    [plan] = plan_dedupe(docs)

    assert plan["keep_id"] == "x"


def test_fills_empty_fields_from_newer_docs_but_keeps_existing_values():
    keeper = {
        "_id": "a",
        "line_id": "U1",
        "name": "李老先生",
        "height": None,
        "chronic_diseases": [],
        "settings": {"language": "zh-TW", "font_size": None},
        "created_at": _dt(1),
        "updated_at": _dt(1),
    }
    newer = {
        "_id": "b",
        "line_id": "U1",
        "name": "LINE User",
        "height": 170,
        "chronic_diseases": ["高血壓"],
        "settings": {"language": "en", "font_size": "xlarge"},
        "created_at": _dt(2),
        "updated_at": _dt(4),
    }

    filled, dropped = merge_fields(keeper, [newer])

    assert filled == {
        "height": 170,
        "chronic_diseases": ["高血壓"],
        "settings.font_size": "xlarge",
        "updated_at": _dt(4),
    }
    assert dropped == {"name": ["LINE User"], "settings.language": ["en"]}


def test_newest_doc_wins_among_the_newer_ones():
    keeper = {"_id": "a", "line_id": "U1", "age": None, "created_at": _dt(1)}
    docs = [
        {"_id": "c", "line_id": "U1", "age": 80, "created_at": _dt(3)},
        {"_id": "b", "line_id": "U1", "age": 70, "created_at": _dt(2)},
    ]

    filled, _ = merge_fields(keeper, docs)  # 依新到舊排

    assert filled["age"] == 80


def test_identity_and_timestamp_fields_are_not_merged():
    keeper = {"_id": "a", "line_id": "U1", "created_at": _dt(1)}
    newer = {"_id": "b", "line_id": "U1", "created_at": _dt(2), "role": "admin"}

    filled, _ = merge_fields(keeper, [newer])

    assert "_id" not in filled and "created_at" not in filled and "line_id" not in filled
    assert filled["role"] == "admin"


def test_no_plan_for_unique_or_blank_line_ids():
    docs = [
        {"_id": "a", "line_id": "U1", "created_at": _dt(1)},
        {"_id": "b", "line_id": "", "created_at": _dt(1)},
        {"_id": "c", "created_at": _dt(1)},
    ]

    assert plan_dedupe(docs) == []


class _Result:
    def __init__(self, **counts):
        self.__dict__.update(counts)


class _FakeCollection:
    def __init__(self):
        self.updates = []
        self.deletes = []

    def update_one(self, query, update):
        self.updates.append((query, update))
        return _Result(modified_count=1)

    def delete_many(self, query):
        self.deletes.append(query)
        return _Result(deleted_count=len(query["_id"]["$in"]))


def test_apply_plans_sets_filled_fields_then_deletes_the_rest():
    collection = _FakeCollection()
    plans = [
        {"line_id": "U1", "keep_id": "a", "delete_ids": ["b", "c"], "filled": {"height": 170}, "dropped": {}},
        {"line_id": "U2", "keep_id": "x", "delete_ids": ["y"], "filled": {}, "dropped": {}},
    ]

    filled_docs, deleted = apply_plans(collection, plans)

    assert filled_docs == 1
    assert deleted == 3
    assert collection.updates == [({"_id": "a"}, {"$set": {"height": 170}})]
    assert collection.deletes == [{"_id": {"$in": ["b", "c"]}}, {"_id": {"$in": ["y"]}}]
