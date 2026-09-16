"""走失求救的判斷：關鍵字先判，認不得的交給本地分類器。

關鍵字（lost_intent）快、可解釋，但只認得寫進規則的中文講法。分類器
（`resources/lost_model.json`，由 `scripts/build_lost_dataset.py` 產資料、
`scripts/build_guardrail_model.py` 訓練）補兩個洞：規則沒寫到的講法，以及其他
五種語言。

分類器的機率分三段，門檻是建置期在交叉驗證上選的（見模型檔的 thresholds）：
- `>= high`：直接啟動走失求救，與關鍵字命中同等對待。
- `low` 到 `high` 之間：**不攔**，訊息照常進 agent，回覆下方多一顆「我迷路了，
  通知家人」，長輩按了才啟動。長輩自己最知道是不是迷路了，讓他決定比問模型準。
  不改成先問「你是不是迷路了？」：holdout 裡落在這一段的一般訊息多半是急症與
  自傷（「叫不醒」「我站在頂樓」），攔下來問，就拿不到 agent 的紅卡。
- `< low`：當作一般訊息，照常進 agent。

模型檔缺席或載不起來時只用關鍵字，不擋啟動，理由同 guardrail 的本地模型。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from app.services.guardrail.local import LocalGuardrailClassifier
from app.services.lost.lost_intent import LostIntent, detect_lost_intent

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOST_MODEL_PATH = _PROJECT_ROOT / "resources" / "lost_model.json"


@dataclass(frozen=True)
class LostDetection:
    """`intent` 為 None 表示不是走失求救。`needs_confirmation` 只在分類器沒把握（只附按鈕）時為 True。"""

    intent: Optional[LostIntent]
    needs_confirmation: bool = False
    source: Literal["keyword", "classifier", "none"] = "none"
    probability: Optional[float] = None


NOT_LOST = LostDetection(intent=None)


class LostIntentDetector:
    def __init__(self, classifier: Optional[LocalGuardrailClassifier] = None) -> None:
        self._classifier = classifier

    @classmethod
    def load(cls, path: Path | str = LOST_MODEL_PATH) -> "LostIntentDetector":
        try:
            return cls(LocalGuardrailClassifier.load(path))
        except Exception:  # noqa: BLE001
            logger.exception("走失分類器載入失敗，只用關鍵字判斷")
            return cls(None)

    def detect(self, text: str) -> LostDetection:
        intent = detect_lost_intent(text)
        if intent is not None:
            return LostDetection(intent=intent, source="keyword")
        if self._classifier is None or not (text or "").strip():
            return NOT_LOST
        try:
            probability = self._classifier.probability(text)
        except Exception:  # noqa: BLE001
            logger.exception("走失分類器推論失敗，當作一般訊息")
            return NOT_LOST
        if probability >= self._classifier.high:
            return LostDetection("lost", source="classifier", probability=probability)
        if probability >= self._classifier.low:
            return LostDetection(
                "lost", needs_confirmation=True, source="classifier", probability=probability
            )
        return LostDetection(intent=None, source="classifier", probability=probability)
