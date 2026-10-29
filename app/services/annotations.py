"""沿用本機盲標題組；標註身分只由登入帳號決定。"""

import hashlib
import json
import random
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from app.models.annotation import SaveAnnotationRequest
from app.repositories.annotation_repository import AnnotationRepository

RESOURCE_DIR = Path(__file__).resolve().parents[2] / "resources" / "annotations"
TASKS = {
    "health": {
        "title": "健康相關標註", "file": "health.json", "hash_key": "samples_sha256",
        "version": "health-annotator-1", "pilot": 100, "gap": 60,
        "description": "判斷 LINE 轉傳訊息是否與健康醫療相關。",
    },
    "claim-pairs": {
        "title": "主張配對標註", "file": "claim_pairs.json", "hash_key": "dataset_sha256",
        "version": "claim-annotator-1", "pilot": 60, "gap": 40,
        "description": "判斷使用者的說法與查核報告是否在講同一件事。",
    },
}


@lru_cache(maxsize=2)
def load_dataset(task: str) -> dict:
    if task not in TASKS:
        raise HTTPException(404, "找不到這個標註題組")
    return json.loads((RESOURCE_DIR / TASKS[task]["file"]).read_text(encoding="utf-8"))


def annotator_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()


def sequence(task: str, user_id: str) -> list[dict]:
    data, config = load_dataset(task), TASKS[task]
    seed = hashlib.sha256(f"{user_id}|{data[config['hash_key']]}".encode()).digest()
    rng = random.Random(seed)
    ids = [item["id"] for item in data["items"]]
    rng.shuffle(ids)
    pool = ids[:max(0, len(ids) - config["gap"])]
    rng.shuffle(pool)
    return ([{"id": value, "retest": False} for value in ids]
            + [{"id": value, "retest": True} for value in pool[:20]])


class AnnotationService:
    def __init__(self, repository=None):
        self.repository = repository if repository is not None else AnnotationRepository()

    def catalog(self):
        return [{
            "id": task, "title": config["title"], "description": config["description"],
            "question_count": len(load_dataset(task)["items"]),
            "total_count": len(load_dataset(task)["items"]) + 20,
        } for task, config in TASKS.items()]

    async def state(self, task: str, user_id: str):
        data = load_dataset(task)
        fingerprint = data[TASKS[task]["hash_key"]]
        key = f"{task}:{fingerprint}:{annotator_id(user_id)}"
        state = await self.repository.get_or_create(key, user_id, task, fingerprint, sequence(task, user_id))
        return key, state

    async def session(self, task: str, user_id: str, name: str):
        _, state = await self.state(task, user_id)
        config, data = TASKS[task], load_dataset(task)
        return {
            "task": task, "title": config["title"],
            "annotator": name.strip() or f"標註者 {annotator_id(user_id)[:8]}",
            "annotator_id": annotator_id(user_id),
            "fingerprint": data[config["hash_key"]], "version": config["version"],
            "hash_key": config["hash_key"], "pilot": config["pilot"],
            "items": data["items"], "order": state["order"],
            "started_at": state["started_at"], "revision": state["revision"],
            "position": state["position"], "answers": state["answers"],
        }

    async def save(self, task: str, user_id: str, position: int, body: SaveAnnotationRequest):
        data = load_dataset(task)
        if body.fingerprint != data[TASKS[task]["hash_key"]]:
            raise HTTPException(409, "題組版本已更新，請重新載入")
        key, state = await self.state(task, user_id)
        order = state["order"]
        if not 0 <= position < len(order):
            raise HTTPException(422, "題目位置不正確")
        if task == "claim-pairs" and body.label is None:
            raise HTTPException(422, "主張配對請選擇同一或不同主張")
        previous = state["answers"].get(str(position))
        answer = {
            **order[position], "label": body.label, "unsure": body.unsure,
            "note": body.note.strip(), "ms": previous["ms"] if previous else body.ms,
            "at": datetime.now(timezone.utc).isoformat(),
            "edits": (previous["edits"] + int(previous["label"] != body.label)) if previous else 0,
        }
        saved = await self.repository.save(key, body.revision, position, answer)
        if saved is None:
            raise HTTPException(409, "其他視窗已更新進度，請重新載入後繼續")
        return {"revision": saved["revision"], "position": position + 1, "answer": answer}

    async def export(self, task: str, user_id: str, name: str):
        state = await self.session(task, user_id, name)
        answers = [value for _, value in sorted(state["answers"].items(), key=lambda p: int(p[0]))]
        return {
            "version": state["version"], "annotator": state["annotator"],
            "annotator_id": state["annotator_id"], state["hash_key"]: state["fingerprint"],
            "started_at": state["started_at"], "exported_at": datetime.now(timezone.utc).isoformat(),
            "n_total": len(state["order"]), "n_answered": len(answers),
            "order": state["order"], "answers": answers,
        }
