from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from app.dependencies import CurrentUser, get_current_user, get_user_profile_service
from app.repositories.annotation_repository import AnnotationRepository
from app.routers.admin.annotations import get_annotation_service, router
from app.services.annotations import AnnotationService, load_dataset, sequence


class MemoryRepository:
    def __init__(self):
        self.states = {}

    async def get_or_create(self, key, user_id, task, fingerprint, order):
        self.states.setdefault(key, {
            "line_user_id": user_id, "task": task, "fingerprint": fingerprint,
            "started_at": "2026-10-29T00:00:00+00:00", "revision": 0,
            "position": 0, "answers": {}, "order": order,
        })
        return deepcopy(self.states[key])

    async def save(self, key, revision, position, answer):
        state = self.states[key]
        if state["revision"] != revision:
            return None
        state["answers"][str(position)] = deepcopy(answer)
        state["position"] = position + 1
        state["revision"] += 1
        return deepcopy(state)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/admin/annotations")

    def user(authorization: str | None = Header(default=None)):
        if authorization is None:
            raise HTTPException(401)
        return CurrentUser(line_user_id=authorization.removeprefix("Bearer "))

    profiles = AsyncMock()
    profiles.get_user_profile.side_effect = lambda user_id: {
        "name": "同名同學", "role": "admin" if user_id in ("admin-a", "admin-b") else "user",
    }
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_user_profile_service] = lambda: profiles
    memory = MemoryRepository()
    app.dependency_overrides[get_annotation_service] = lambda: AnnotationService(memory)
    return TestClient(app)


ADMIN = {"Authorization": "Bearer admin-a"}
OTHER = {"Authorization": "Bearer admin-b"}
ROOT = "/api/admin/annotations"


def body(task="health", **changes):
    data = load_dataset(task)
    fingerprint = data["samples_sha256" if task == "health" else "dataset_sha256"]
    return {"fingerprint": fingerprint, "revision": 0, "label": 1,
            "unsure": False, "note": "我的判斷", "ms": 300, **changes}


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "", None), ("GET", "/health/session", None),
    ("PUT", "/health/answers/0", body()), ("GET", "/health/export", None),
])
@pytest.mark.parametrize("headers,status", [({}, 401), ({"Authorization": "Bearer ordinary"}, 403)])
def test_all_endpoints_require_admin(client, method, path, payload, headers, status):
    assert client.request(method, ROOT + path, json=payload, headers=headers).status_code == status


def test_blind_questions_and_identity_are_from_server(client):
    response = client.get(ROOT + "/health/session", headers=ADMIN)
    assert response.headers["cache-control"] == "no-store"
    state = response.json()
    assert state["annotator"] == "同名同學"
    assert "line_user_id" not in state
    assert len(state["items"]) == 1720
    assert all(set(item) == {"id", "t"} for item in state["items"])
    pairs = client.get(ROOT + "/claim-pairs/session", headers=ADMIN).json()
    assert len(pairs["items"]) == 340
    assert all(set(item) == {"id", "u", "r"} for item in pairs["items"])


def test_accounts_with_same_display_name_are_independent_and_resume(client):
    response = client.put(ROOT + "/health/answers/0", headers=ADMIN, json=body(label=None))
    assert response.status_code == 200
    state = client.get(ROOT + "/health/session", headers=ADMIN).json()
    assert state["position"] == 1
    assert state["answers"]["0"]["label"] is None
    other = client.get(ROOT + "/health/session", headers=OTHER).json()
    assert other["answers"] == {}
    assert other["position"] == 0
    assert state["annotator_id"] != other["annotator_id"]
    assert state["order"] != other["order"]


def test_stale_tab_cannot_overwrite_saved_answer(client):
    assert client.put(ROOT + "/health/answers/0", headers=ADMIN, json=body()).status_code == 200
    stale = client.put(ROOT + "/health/answers/0", headers=ADMIN, json=body(label=0))
    assert stale.status_code == 409
    assert client.get(ROOT + "/health/session", headers=ADMIN).json()["answers"]["0"]["label"] == 1


def test_revision_edit_and_export_are_compatible_with_existing_analysis(client):
    client.put(ROOT + "/health/answers/0", headers=ADMIN, json=body())
    changed = client.put(ROOT + "/health/answers/0", headers=ADMIN, json=body(revision=1, label=0, ms=999))
    assert changed.status_code == 200
    exported = client.get(ROOT + "/health/export", headers=ADMIN).json()
    assert exported["n_answered"] == 1
    assert exported["n_total"] == 1740
    assert exported["samples_sha256"] == body()["fingerprint"]
    assert exported["answers"][0]["edits"] == 1
    assert exported["answers"][0]["ms"] == 300
    assert exported["answers"][0]["id"] == exported["order"][0]["id"]
    assert client.get(ROOT + "/health/export", headers=OTHER).json()["answers"] == []


@pytest.mark.parametrize("payload", [body(label=True), body(label=2), body(ms=-1), body(note="x" * 2001), body(annotator="冒名")])
def test_invalid_answers_or_client_identity_are_rejected(client, payload):
    assert client.put(ROOT + "/health/answers/0", headers=ADMIN, json=payload).status_code == 422


def test_wrong_dataset_and_positions_are_rejected(client):
    assert client.put(ROOT + "/health/answers/0", headers=ADMIN, json=body(fingerprint="a" * 64)).status_code == 409
    assert client.put(ROOT + "/health/answers/1740", headers=ADMIN, json=body()).status_code == 422
    assert client.put(ROOT + "/claim-pairs/answers/0", headers=ADMIN, json=body("claim-pairs", label=None)).status_code == 422
    assert client.get(ROOT + "/missing/session", headers=ADMIN).status_code == 404


@pytest.mark.parametrize("task,gap", [("health", 60), ("claim-pairs", 40)])
def test_sequence_is_stable_and_retests_are_separated(task, gap):
    order = sequence(task, "admin-a")
    assert order == sequence(task, "admin-a")
    count = len(load_dataset(task)["items"])
    assert len(order) == count + 20
    assert len({entry["id"] for entry in order[:count]}) == count
    for index, entry in enumerate(order[count:], start=count):
        original = next(i for i, item in enumerate(order[:count]) if item["id"] == entry["id"])
        assert entry["retest"] and index - original > gap


@pytest.mark.asyncio
async def test_repository_writes_with_account_key_and_expected_revision():
    collection = AsyncMock()
    repository = AnnotationRepository(collection)
    await repository.save("account-and-dataset", 4, 7, {"label": 1, "at": "now"})
    args = collection.find_one_and_update.call_args.args
    assert args[0] == {"_id": "account-and-dataset", "revision": 4}
    assert args[1]["$set"]["answers.7"] == {"label": 1, "at": "now"}
    assert args[1]["$inc"] == {"revision": 1}
