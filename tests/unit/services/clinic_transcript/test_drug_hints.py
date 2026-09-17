"""藥名標示：驗「只標示不改寫」「不碰劑量」「母體是長輩自己的清單」三條規則。"""

from app.services.clinic_transcript.drug_hints import (
    MATCH_THRESHOLD,
    DrugHint,
    dose_digits,
    find_hints,
    medication_names,
)


class _Med:
    def __init__(self, name, generic_name=None):
        self.name = name
        self.generic_name = generic_name


def test_聽對時標得出來():
    hints = find_hints("那個脈優錠先停掉", ["脈優錠5毫克"])
    assert [h.medication_name for h in hints] == ["脈優錠5毫克"]


def test_長藥名聽錯一個字仍標得出來():
    """語音辨識最常見的失敗，正是這個功能要補的洞。"""
    hints = find_hints("思樂康持續性藥校錠先停", ["思樂康持續性藥效錠50毫克"])
    assert len(hints) == 1


def test_三字藥名聽錯一個字就標不出來():
    """已知限制，不是 bug。

    「脈優錠」對中兩個字，SequenceMatcher 只給 0.667，過不了 0.80 的門檻。
    短藥名等於沒有容錯——要讓它有容錯就得降門檻，而降門檻會讓標錯藥的比率
    跳回四到六倍（見 `drug_hints.MATCH_THRESHOLD` 的表），不划算。
    這種情形家人看得到逐字稿原文，只是少一個提示。
    """
    assert find_hints("那個脈憂錠先停掉", ["脈優錠5毫克"]) == []


def test_heard_是逐字稿原文而不是清單上的藥名():
    """只標示不改寫：兩邊並排，不宣稱哪一個才對。"""
    hints = find_hints("思樂康持續性藥校錠先停", ["思樂康持續性藥效錠50毫克"])
    assert "藥校錠" in hints[0].heard
    assert hints[0].medication_name == "思樂康持續性藥效錠50毫克"


def test_不在清單上的藥不會被標():
    """母體就是長輩自己的清單，講到別顆藥不該冒出提示。"""
    assert find_hints("那個普拿疼先停掉", ["脈優錠5毫克"]) == []


def test_劑量不同不影響比對也不產生更正():
    """比對前劑量就被拿掉，所以永遠不會說「你聽錯劑量了」。"""
    hints = find_hints("脈優錠10毫克", ["脈優錠5毫克"])
    assert len(hints) == 1
    # 比對的是拿掉劑量後的名稱，所以標到的是藥名本身，完全沒碰到那個 10。
    assert hints[0].heard == "脈優錠"


def test_劑量數字只被挑出來不被比較():
    assert dose_digits("脈優錠5毫克，一天1次") == ["5", "1"]
    assert dose_digits("") == []


def test_太短的藥名不比對():
    """兩三個字在一整段對話裡幾乎一定找得到相似的東西，標出來都是雜訊。"""
    assert find_hints("今天天氣不錯我們來聊聊", ["普拿"]) == []


def test_同一顆藥只回最像的那一處():
    hints = find_hints("脈優錠先停掉，脈優錠真的先停", ["脈優錠5毫克"])
    assert len(hints) == 1


def test_空逐字稿不爆():
    assert find_hints("", ["脈優錠5毫克"]) == []
    assert find_hints("   ", ["脈優錠5毫克"]) == []


def test_沒有用藥清單時回空():
    assert find_hints("那個脈優錠先停掉", []) == []


def test_清單抽名稱含學名且去重():
    meds = [_Med("脈優錠5毫克", "AMLODIPINE"), _Med("脈優錠5毫克"), _Med("  ", None)]
    assert medication_names(meds) == ["脈優錠5毫克", "AMLODIPINE"]


def test_命中位置能回到原文():
    text = "醫師說那個脈優錠先停掉"
    hint = find_hints(text, ["脈優錠5毫克"])[0]
    assert text[hint.start : hint.start + len(hint.heard)] == hint.heard


def test_門檻是量出來的那個值():
    """改這個值要連同 drug_hints 註解裡那張表一起更新，別憑感覺調。"""
    assert MATCH_THRESHOLD == 0.80


def test_hint_欄位不含任何說話者資訊():
    assert set(DrugHint.__dataclass_fields__) == {"medication_name", "heard", "start", "score"}
