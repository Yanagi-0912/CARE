"""急迫度資料集的組成：外語要有日常訊息，補資料時不能動到既有的列。

外語原本只生成急症與困難負例；閒聊、日常請求、用藥問題這些真實流量的大宗只有
中文（沿用自 guardrail 資料集）。模型因此沒看過外語的日常訊息，holdout 也量不到。
這裡釘住三件事：外語會生成那幾類，而且一律標成不緊急；每一格都補到目標筆數，
自傷兩類在每種語言都跟中文一樣多；`--fill-missing` 只補不足的格子，既有的列
（包含它們的 train／holdout 切分）原封不動——holdout 被重切，就等於拿模型看過的
資料宣稱效果。
"""

import hashlib
import json
from collections import Counter

import pytest

import scripts.build_urgency_dataset as build_urgency_dataset
from scripts.build_guardrail_dataset import BUCKETS as GUARDRAIL_BUCKETS
from scripts.build_urgency_dataset import GUARDRAIL_DATASET, main, planned_cells

# 手抄自 REUSED_GUARDRAIL_BUCKETS，刻意不 import：期望值與被測程式分開寫，
# 清單少了一類才會被抓到。
EVERYDAY_BUCKETS = {
    "guardrail:common_disease",
    "guardrail:medication",
    "guardrail:nutrition_exercise",
    "guardrail:scam_health",
    "guardrail:smalltalk",
    "guardrail:daily_task",
    "guardrail:news_finance",
    "guardrail:food_no_health",
    "guardrail:shopping_travel",
    "guardrail:bot_meta",
    "guardrail:adjacent_but_no",
}
FOREIGN_LANGUAGES = ["en", "id", "vi", "th", "ja"]


def _plan(existing=None):
    """{(語言, bucket): (label, 描述, 還要生成幾筆)}，中文每格 150、外語每格 60。"""
    return {
        (lang, bucket): (label, description, count)
        for lang, bucket, label, description, count in planned_cells(
            existing or {}, per_bucket=150, per_bucket_other=60
        )
    }


@pytest.mark.parametrize("language", FOREIGN_LANGUAGES)
def test_foreign_languages_generate_everyday_messages_as_not_urgent(language):
    """guardrail 把用藥、慢性病問題標成 1（是醫療問題），這裡必須是 0（不緊急）。
    沿用 guardrail 的標籤，會把外籍看護最常問的問題全教成急症。"""
    planned = {
        (bucket, label)
        for (lang, bucket), (label, _, _) in _plan().items()
        if lang == language and bucket.startswith("guardrail:")
    }
    assert planned == {(bucket, 0) for bucket in EVERYDAY_BUCKETS}


def test_zh_tw_keeps_reusing_guardrail_rows_instead_of_generating():
    """中文的日常訊息沿用 guardrail 資料集；再生成一份會讓中文負例重複來源。"""
    generated = [key for key in _plan() if key[0] == "zh-TW" and key[1].startswith("guardrail:")]
    assert generated == []


@pytest.mark.parametrize("bucket", ["smalltalk", "medication"])
def test_everyday_cells_use_the_guardrail_definition_of_the_same_bucket(bucket):
    definitions = {name: description for name, _, description in GUARDRAIL_BUCKETS}
    assert _plan()[("en", f"guardrail:{bucket}")][1] == definitions[bucket]


@pytest.mark.parametrize(
    ("language", "bucket", "count"),
    [
        ("zh-TW", "past_followup", 150),
        ("en", "past_followup", 60),
        ("en", "guardrail:smalltalk", 60),
        # 自傷兩類決定 low：交叉驗證裡機率最低的急症幾乎都在這兩類，外語只有 60 筆時
        # 最先被壓低的就是它們。
        ("en", "self_harm", 150),
        ("th", "indirect_self_harm", 150),
    ],
)
def test_each_cell_targets_its_language_count_and_self_harm_matches_zh_tw(language, bucket, count):
    assert _plan()[(language, bucket)][2] == count


def test_an_under_filled_cell_is_topped_up_to_its_target():
    assert _plan({("en", "indirect_self_harm"): 60})[("en", "indirect_self_harm")][2] == 90


def test_a_full_cell_is_not_planned():
    assert ("en", "guardrail:smalltalk") not in _plan({("en", "guardrail:smalltalk"): 60})


class _FakeGemini:
    """只換掉對外的 Gemini 呼叫。每批依 prompt 回不同的句子，才不會被去重吃掉。"""

    def __init__(self, **_kwargs):
        pass

    async def invoke_structured_output(self, *, prompt, json_schema):
        digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]
        return {"messages": [f"{digest}-{i}" for i in range(3)]}


@pytest.fixture
def fake_gemini(monkeypatch):
    monkeypatch.setattr(build_urgency_dataset, "GeminiService", _FakeGemini)
    monkeypatch.setattr(build_urgency_dataset.settings, "GEMINI_API_KEY", "test-key")


def _write(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def _read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


EXISTING = [
    {
        "text": "阿公叫不醒",
        "label": 1,
        "bucket": "unconscious_collapse",
        "lang": "zh-TW",
        "split": "holdout",
        "source": "synthetic",
        "model": "old-model",
        "generated_at": "2026-09-14T02:21:31+00:00",
    },
    {
        "text": "hi, are you there?",
        "label": 0,
        "bucket": "guardrail:smalltalk",
        "lang": "en",
        "split": "train",
        "source": "synthetic",
        "model": "old-model",
        "generated_at": "2026-09-14T02:21:31+00:00",
    },
]

# 每格目標 1 筆：EXISTING 的兩格已經滿了，不該再生成。
FILL_ARGS = ["--fill-missing", "--per-bucket", "1", "--per-bucket-other", "1"]


def test_fill_missing_leaves_existing_rows_untouched(tmp_path, fake_gemini):
    out = tmp_path / "dataset.jsonl"
    _write(out, EXISTING)

    assert main(["--out", str(out), "--no-reuse-guardrail", *FILL_ARGS]) == 0

    rows = _read(out)
    for row in EXISTING:
        assert row in rows


def test_fill_missing_generates_only_cells_below_their_target(tmp_path, fake_gemini):
    out = tmp_path / "dataset.jsonl"
    _write(out, EXISTING)

    assert main(["--out", str(out), "--no-reuse-guardrail", *FILL_ARGS]) == 0

    cells = Counter((row["lang"], row["bucket"]) for row in _read(out))
    assert cells[("zh-TW", "unconscious_collapse")] == 1
    assert cells[("en", "guardrail:smalltalk")] == 1
    assert cells[("en", "guardrail:daily_task")] > 0
    assert cells[("zh-TW", "self_harm")] > 0


def test_fill_missing_does_not_append_reused_guardrail_rows_twice(tmp_path, fake_gemini):
    reused = next(row for row in _read(GUARDRAIL_DATASET) if row["bucket"] == "smalltalk")
    out = tmp_path / "dataset.jsonl"
    _write(
        out,
        [
            {
                "text": reused["text"],
                "label": 0,
                "bucket": "guardrail:smalltalk",
                "lang": "zh-TW",
                "split": reused.get("split", "train"),
                "source": "guardrail_dataset",
                "model": reused.get("model"),
                "generated_at": reused.get("generated_at"),
            }
        ],
    )

    assert main(["--out", str(out), *FILL_ARGS]) == 0

    texts = Counter(row["text"] for row in _read(out))
    assert texts[reused["text"]] == 1
