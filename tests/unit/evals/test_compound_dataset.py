"""Guard the shipped compound benchmark against pairing and label regressions."""
import copy
import importlib.util
from pathlib import Path

import pytest


DATASET_DIR = Path(__file__).resolve().parents[3] / "evals" / "rag_compound"
SPEC = importlib.util.spec_from_file_location("compound_dataset_validator", DATASET_DIR / "validate.py")
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


@pytest.fixture(scope="module")
def dataset_rows():
    return VALIDATOR.read_jsonl(DATASET_DIR / "golden.jsonl")


def test_compound_dataset_contract(dataset_rows):
    report = VALIDATOR.validate(dataset_rows)
    assert report["ok"], report["errors"]
    assert report["numeric_token_changes_for_manual_review"] == []


def test_validator_rejects_variant_label_drift(dataset_rows):
    rows = copy.deepcopy(dataset_rows)
    variant = next(row for row in rows if row["variant_of"] is not None)
    variant["subquestions"][0]["ask"] = "這是被誤改的另一個資訊需求"
    report = VALIDATOR.validate(rows)
    assert not report["ok"]
    assert any("variant changed subquestions" in error for error in report["errors"])


def test_validator_rejects_pair_split_leakage(dataset_rows):
    rows = copy.deepcopy(dataset_rows)
    variant = next(row for row in rows if row["variant_of"] is not None)
    variant["split"] = "holdout" if variant["split"] == "train" else "train"
    report = VALIDATOR.validate(rows)
    assert not report["ok"]
    assert any("split leakage" in error for error in report["errors"])


def test_validator_flags_altered_clinical_numbers(dataset_rows):
    rows = copy.deepcopy(dataset_rows)
    variant = next(row for row in rows if row["variant_of"] is not None)
    variant["query"] += "體溫99度"
    report = VALIDATOR.validate(rows)
    assert report["numeric_token_changes_for_manual_review"]
