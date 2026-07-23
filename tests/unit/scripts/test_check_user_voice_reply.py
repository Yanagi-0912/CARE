from unittest.mock import MagicMock

from pymongo import UpdateOne

from scripts.check_user_voice_reply import audit_users_collection


def _collection_with_docs(docs):
    collection = MagicMock()
    collection.count_documents.return_value = len(docs)
    collection.find.return_value = docs
    return collection


def test_audit_users_collection_dry_run_does_not_write(capsys):
    collection = _collection_with_docs(
        [
            {"_id": 1, "line_id": "U1", "voice_reply_enabled": True},
            {"_id": 2, "line_id": "U2"},
            {"_id": 3, "line_id": "U3", "voice_reply_enabled": "true"},
        ]
    )

    result = audit_users_collection(collection, fix=False)

    assert result.total == 3
    assert result.valid == 1
    assert result.missing == 1
    assert result.invalid == 1
    assert result.fixed == 0
    collection.bulk_write.assert_not_called()

    out = capsys.readouterr().out
    assert "Dry run only" in out
    assert "Missing voice_reply_enabled: 1" in out
    assert "Invalid voice_reply_enabled type/value: 1" in out


def test_audit_users_collection_fix_updates_missing_and_invalid():
    collection = _collection_with_docs(
        [
            {"_id": 1, "line_id": "U1", "voice_reply_enabled": False},
            {"_id": 2, "line_id": "U2"},
            {"_id": 3, "line_id": "U3", "voice_reply_enabled": None},
        ]
    )
    bulk_result = MagicMock()
    bulk_result.modified_count = 2
    bulk_result.upserted_ids = {}
    collection.bulk_write.return_value = bulk_result

    result = audit_users_collection(collection, fix=True, default_value=True)

    assert result.total == 3
    assert result.valid == 1
    assert result.missing == 1
    assert result.invalid == 1
    assert result.fixed == 2
    collection.bulk_write.assert_called_once()

    operations = collection.bulk_write.call_args.args[0]
    assert len(operations) == 2
    assert all(isinstance(operation, UpdateOne) for operation in operations)
    assert collection.bulk_write.call_args.kwargs == {"ordered": False}
