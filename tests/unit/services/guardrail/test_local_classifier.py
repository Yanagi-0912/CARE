"""本地 guardrail 分類器：與 sklearn 的數值一致性，以及已提交模型檔的守門。"""

import json
import math

import pytest

from app.services.guardrail.local import (
    DEFAULT_MODEL_PATH,
    SUPPORTED_FORMAT,
    LocalGuardrailClassifier,
)

HEALTH = [
    "我血壓有點高怎麼辦",
    "這個藥可以跟感冒藥一起吃嗎",
    "膝蓋痛要看哪一科",
    "金罵頭金暈甘要去看醫生",
    "健保局傳簡訊說要我匯款不然鎖卡",
    "PANADOL 一天最多吃幾顆",
]
NOT_HEALTH = [
    "今天天氣好熱",
    "幫我訂高鐵票",
    "台大醫院附近好停車嗎",
    "三加五等於多少",
    "推薦好看的電影",
]


# ── 與 sklearn 的數值一致性 ─────────────────────────────────────────


def test_matches_sklearn_decision_function_exactly():
    """
    純 Python 的算式必須與 `TfidfVectorizer` + `LogisticRegression` 對齊到
    浮點誤差內。對不齊的後果不是「準度差一點」——建置期選出的 low／high
    門檻是在 sklearn 的機率尺度上選的，執行期若換了一把尺，那兩個數字就
    失去意義，而它們正是決定「要不要升級給 LLM」的唯一依據。
    """
    pytest.importorskip("sklearn", reason="sklearn 是 dev 依賴，正式映像沒有")
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    from scripts.build_guardrail_model import export_model

    texts = HEALTH + NOT_HEALTH
    labels = [1] * len(HEALTH) + [0] * len(NOT_HEALTH)

    pipeline = Pipeline(
        [
            ("tfidf", TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=1)),
            ("clf", LogisticRegression(C=4.0, max_iter=2000, solver="liblinear")),
        ]
    )
    pipeline.fit(texts, labels)

    model = export_model(
        pipeline,
        thresholds=(0.2, 0.8),
        stats={},
        dataset="(test)",
        n_train=len(texts),
    )
    local = LocalGuardrailClassifier(model)

    probe = texts + ["完全沒看過的句子 12345", "血壓", "", "阿"]
    expected = pipeline.decision_function(probe)
    for text, want in zip(probe, expected):
        got = local.decision(text)
        assert got == pytest.approx(want, abs=1e-9), text


def test_probability_matches_sigmoid_of_decision():
    model = json.loads(DEFAULT_MODEL_PATH.read_text(encoding="utf-8"))
    local = LocalGuardrailClassifier(model)
    for text in HEALTH + NOT_HEALTH:
        score = local.decision(text)
        assert local.probability(text) == pytest.approx(1 / (1 + math.exp(-score)))


# ── 邊界 ────────────────────────────────────────────────────────────


def test_text_with_no_known_ngrams_falls_back_to_intercept():
    """
    一個詞彙表片段都沒命中時不能除以零。這條路徑在正式環境是會發生的——
    純表情符號、純英數字串、其他語言的訊息都可能整串落在詞彙表外。
    """
    model = json.loads(DEFAULT_MODEL_PATH.read_text(encoding="utf-8"))
    local = LocalGuardrailClassifier(model)
    for text in ("", "a", "🙂🙂🙂", "🐈"):
        prob = local.probability(text)
        assert 0.0 <= prob <= 1.0


def test_rejects_unknown_model_format():
    """模型格式換版時要直接炸，不能拿舊算式去讀新權重。"""
    with pytest.raises(ValueError, match="unsupported guardrail model format"):
        LocalGuardrailClassifier({"format": "something-else", "terms": {}})


# ── 已提交的模型檔（產出物守門） ────────────────────────────────────


def test_shipped_model_is_loadable_and_well_formed():
    local = LocalGuardrailClassifier.load()
    payload = json.loads(DEFAULT_MODEL_PATH.read_text(encoding="utf-8"))

    assert payload["format"] == SUPPORTED_FORMAT
    assert payload["terms"], "詞彙表是空的"
    assert 0.0 < local.low < local.high < 1.0, (local.low, local.high)


def test_shipped_model_separates_the_obvious_cases():
    """
    不是準確率測試（那是 scripts/guardrail_eval.py 的事），是產出物守門：
    模型檔若被錯誤的訓練覆蓋掉，最明顯的例子會先失守。
    """
    local = LocalGuardrailClassifier.load()
    lowest_health = min(local.probability(t) for t in HEALTH)
    highest_chat = max(local.probability(t) for t in ("今天天氣好熱", "幫我訂高鐵票"))
    assert lowest_health > highest_chat
