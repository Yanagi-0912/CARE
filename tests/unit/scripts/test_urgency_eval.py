"""急迫度本地模型評測的計數規則。

報告要回答的是「每種語言、每一類訊息，本地放行了多少，有沒有放掉急症」。算錯的
方式都很安靜：把 train 列算進去會讓數字變好看；把 guardrail 的日常訊息跟困難負例
混在一起，就會像之前那樣，拿外語困難負例的放行率去跟中文日常訊息的放行率比。
"""

from scripts.urgency_eval import summarize


def _row(text, bucket, label, split="holdout"):
    return {"text": text, "lang": "en", "bucket": bucket, "label": label, "split": split}


ROWS = [
    _row("everyday-low", "guardrail:smalltalk", 0),
    _row("everyday-mid", "guardrail:smalltalk", 0),
    _row("hard-low", "past_followup", 0),
    _row("hard-high", "figurative_self_harm", 0),
    _row("emergency-high", "self_harm", 1),
    _row("emergency-low", "stroke_signs", 1),
    _row("train-row", "guardrail:smalltalk", 0, split="train"),
]
PROBABILITY = {
    "everyday-low": 0.01,
    "everyday-mid": 0.30,
    "hard-low": 0.05,
    "hard-high": 0.90,
    "emergency-high": 0.90,
    "emergency-low": 0.02,
    "train-row": 0.0,
}


def _summary():
    return summarize(ROWS, PROBABILITY.__getitem__, low=0.1)


def test_only_holdout_rows_are_counted():
    assert _summary()[("en", "everyday")].total == 2


def test_everyday_and_hard_negatives_are_counted_separately():
    summary = _summary()
    assert (summary[("en", "everyday")].released, summary[("en", "everyday")].total) == (1, 2)
    assert (summary[("en", "hard_negative")].released, summary[("en", "hard_negative")].total) == (1, 2)


def test_an_emergency_below_low_is_reported_as_released():
    """急症被本地放行就是漏判：沒有紅卡，也沒有家人通報。"""
    assert _summary()[("en", "emergency")].released == 1


def test_red_card_when_llm_is_down_uses_the_local_fallback_cutoff_not_low():
    """LLM 中斷時，沒被放行的訊息要本地機率達到 0.5 才出紅卡。
    everyday-mid（0.30）超過 low 但未達 0.5：升級給 LLM，但中斷時不出紅卡。"""
    summary = _summary()
    assert summary[("en", "emergency")].red_card_if_llm_down == 1
    assert summary[("en", "hard_negative")].red_card_if_llm_down == 1
    assert summary[("en", "everyday")].red_card_if_llm_down == 0
