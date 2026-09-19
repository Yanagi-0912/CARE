"""本地 guardrail 分類器：與 sklearn 的數值一致性，以及已提交模型檔的守門。"""

import json
import math

import pytest

from app.services.guardrail.local import (
    DEFAULT_MODEL_PATH,
    DENSE_FORMAT,
    SUPPORTED_FORMATS,
    MIN_KNOWN_SHARE,
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


class _FakeEncoder:
    """把文字對到訓練時用的那個向量。

    用假的編碼器而不是真的 ONNX 模型：這個測試要驗的是「稀疏與稠密兩塊的
    順序、係數切法、點積」有沒有寫錯，那與向量本身長什麼樣無關。真模型是
    135MB 的檔案，不該成為單元測試的前提。
    """

    def __init__(self, table):
        self._table = table

    def encode(self, text, prefix=""):
        assert prefix == "query: ", f"執行期用了與訓練不同的前綴：{prefix!r}"
        return self._table[text]


def test_dense_model_matches_sklearn_decision_function_exactly():
    """v2（字元片段＋句向量）同樣要與 sklearn 對齊到浮點誤差內。

    這裡最容易出錯而且**不會有任何症狀**的是兩件事：hstack 的順序（稀疏在
    前、稠密在後）與 export_model 切係數的位置。兩者任一錯了，模型照樣載入、
    照樣吐得出 0~1 的機率，只是那個機率沒有意義，而門檻還是舊的那一組。
    """
    pytest.importorskip("sklearn", reason="sklearn 是 dev 依賴，正式映像沒有")
    import numpy as np
    from scipy.sparse import csr_matrix, hstack
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    from scripts.build_guardrail_model import export_model

    texts = HEALTH + NOT_HEALTH
    labels = [1] * len(HEALTH) + [0] * len(NOT_HEALTH)

    rng = np.random.default_rng(20260919)
    vectors = rng.normal(size=(len(texts), 8))
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    table = {t: v.tolist() for t, v in zip(texts, vectors)}

    vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=1)
    sparse = vec.fit_transform(texts)
    combined = hstack([sparse, csr_matrix(vectors)]).tocsr()
    clf = LogisticRegression(C=4.0, max_iter=2000).fit(combined, labels)

    model = export_model(
        Pipeline([("tfidf", vec), ("clf", clf)]),
        thresholds=(0.2, 0.8),
        stats={},
        dataset="(test)",
        n_train=len(texts),
        dense={"model": "fake", "prefix": "query: ", "dim": 8},
    )
    assert model["format"] == DENSE_FORMAT

    local = LocalGuardrailClassifier(model, encoder=_FakeEncoder(table))
    expected = clf.decision_function(combined)
    for text, want in zip(texts, expected):
        assert local.decision(text) == pytest.approx(want, abs=1e-9), text


def test_dense_model_escalates_when_encoder_fails():
    """編碼失敗時必須落在 (low, high) 之間，讓呼叫端照既有路徑升級給 LLM。

    不能退回「只算字元片段」：那是一個少了一半特徵的分數，卻會被拿去跟同一
    組門檻比較，於是一次失敗會偽裝成一次正常判斷。
    """

    class _Broken:
        def encode(self, text, prefix=""):
            raise RuntimeError("模型檔不見了")

    model = {
        "format": DENSE_FORMAT,
        "terms": {},
        "intercept": 0.0,
        "ngram_range": [2, 4],
        "thresholds": {"low": 0.2, "high": 0.8},
        "dense": {"model": "fake", "prefix": "query: ", "dim": 3, "coef": [1.0, 1.0, 1.0]},
    }
    local = LocalGuardrailClassifier(model, encoder=_Broken())
    prob = local.probability("我血壓有點高怎麼辦")
    assert local.low < prob < local.high, prob


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

    assert payload["format"] in SUPPORTED_FORMATS
    if payload["format"] == DENSE_FORMAT:
        # v2 多帶一段句向量權重。維度寫死 384 是刻意的：換模型就是換維度，
        # 而換了之後 `dense.model` 與執行期載入的 ONNX 若對不上，分數會靜靜
        # 地全錯（權重與向量的每一維意義都不同了）。這裡讓它在測試就炸。
        dense = payload["dense"]
        assert dense["dim"] == 384
        assert len(dense["coef"]) == 384
        assert dense["model"] == "multilingual-e5-small-int8"
        assert dense["prefix"] == "query: "
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


FOREIGN_HEALTH = [
    "I have a stomach ache and my knee is hurt.",
    "Can I take ibuprofen with my blood pressure pills?",
    "Saya sakit perut dan lutut saya sakit.",
    "Tôi bị đau bụng và đau đầu gối.",
    "ปวดท้องและปวดเข่า",
    "胃が痛くて、膝も痛いです。",
]


def test_shipped_model_does_not_deny_foreign_health_questions():
    """
    只用中文訓練的模型把英文健康問題幾乎全擋掉（2026-09-15 外語 holdout：英文 198 題
    漏 195 題），第一句就是當天一則真實的英文語音。本地擋下＝拿不到知識庫的答案。
    """
    local = LocalGuardrailClassifier.load()
    for text in FOREIGN_HEALTH:
        assert local.probability(text) >= local.low, text
    assert local.probability("Book me a train ticket to Taichung.") < local.low


def test_shipped_model_does_not_recognize_garbled_speech_transcript():
    # 2026-09-17 正式環境：台語語音辨識出不通順的句子，詞彙表只認得「就大」。
    classifier = LocalGuardrailClassifier.load()
    assert classifier.known_share("恥笑漸漸光，咱就大聲仔想著煞") < MIN_KNOWN_SHARE
    assert classifier.recognizes("恥笑漸漸光，咱就大聲仔想著煞") is False


def test_shipped_model_recognizes_ordinary_health_questions():
    classifier = LocalGuardrailClassifier.load()
    for text in ("高血壓平常要注意什麼？", "血壓藥早上忘記吃，下午補吃可以嗎", "我肚子痛"):
        assert classifier.recognizes(text), text


def test_known_share_of_empty_text_is_zero():
    assert LocalGuardrailClassifier.load().known_share("") == 0.0
