"""
急迫度判斷：這段話描述的狀況是不是「正在發生、且需要立即處置」。

為什麼從關鍵字改成語意判斷：
    前一版用字面 regex 比對。它的上限已經被實測打穿——`車禍` 收了但「被車撞」
    沒收，`血流很多` 收了但「流好多血」沒收。中文同一件事的語序是開放集合，
    補一百條仍會有第一百零一種說法，而漏掉的那一種沒有補救機會。
    更關鍵的是多語言：那份清單全是 zh-TW，換成其他語言時 detector 一律回
    「不緊急」——不是判斷變弱，是完全沒有安全網，而使用者不會知道。

判準是「是否正在發生」，不是「有沒有提到急症詞」：
    這是本模組唯一重要的設計。「我阿公昏迷」與「昏迷的原因有哪些」含有同一個
    詞，但前者要的是緊急處置、後者要的是衛教。用關鍵字分不出來，所以前一版只
    好在「攔截」與「別把 RAG 吃掉」之間二選一。判準換成語意之後這個取捨消失
    了——衛教問句本來就會被判成不緊急。

急迫度與掛號意圖是正交的：
    前一版把急迫度判斷放在「症狀＋掛號意圖」的分支後面，於是「我阿公昏迷」
    因為沒問科別而完全跳過檢查。這裡刻意獨立成一個判斷，不依賴使用者是否
    問了科別、是否描述了症狀、是否在求助。

自殺與自傷意念不在本判斷器的範圍內：
    本判斷器只負責生理急症。表達自殺或自傷意念（「我要燒炭自殺」「我想死」）
    SHALL 回不緊急，交由一般流程處理。

    這是刻意的範圍決定，不是判斷器判不出來——它判得出來，而且會判對。問題在於
    本模組只有一個緊急出口，而那個出口是為生理急症設計的：紅色警報底、「請立即
    就醫」、119 與 110。對正在說「我想死」的人，紅色是施壓不是支持，「前往急診」
    答非所問，110 警察報案讀起來像威脅，而唯一真正對口的 1925 安心專線並不在
    卡片上。判對了送錯卡，比不判更糟。

    代價明確：這類訊息的回覆完全不受控（走 RAG 自由生成）。危機支持若要做，
    SHALL 是獨立的功能與獨立的卡片，不是本模組的一個分支。

    邊界：排除的是「意念」，不是「已經發生的自傷行為造成的生理危險」。
    「我剛剛吞了一整罐藥」「我割腕血流不止」是進行中的中毒與出血，仍須攔下——
    那時需要的就是 119，卡片是對的。

為什麼失敗方向是 fail-open（與前一版相反）：
    前一版是 fail-closed，理由是「漏放沒有補救機會」。那個理由的前提是偵測器
    為本地 regex——它只會因為程式寫壞而失敗，機率極低且該讓人立刻發現。換成
    LLM 之後失敗來源變成網路、配額、逾時，是常態而非異常；fail-closed 會讓
    每一次 API 中斷都變成「所有使用者都被叫去打 119」。那不是保守，是把卡片
    變成雜訊，使用者會很快學會忽略它——連帶真的急症也被忽略。
    這是純 LLM 方案必須接受的代價：沒有地板，中斷期間就沒有安全網。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Services:Urgency]"

URGENCY_EMERGENCY = "emergency"
URGENCY_NONE = "none"

# 判斷器的逾時。這個判斷擋在所有回覆前面，慢了等於整個 bot 慢；judge 沒有在
# 這個時間內回來時走 fail-open，理由見模組註解。
DEFAULT_TIMEOUT_SECONDS = 4.0


# 全形數字 → 半形。re 的 \D 在 Unicode 模式下不會濾掉全形數字（它們屬於 Nd
# 類別），所以必須先轉換——否則 tel:１１９ 會原樣送出，部分裝置撥不出去。
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


@dataclass(frozen=True)
class Hotline:
    name: str
    number: str
    note: str = ""

    @property
    def tel_uri(self) -> str:
        """Flex 撥號按鈕用。只留半形數字，避免全形或分隔符讓 LINE 撥不出去。"""
        digits = re.sub(r"[^0-9]", "", self.number.translate(_FULLWIDTH_DIGITS))
        return f"tel:{digits}" if digits else "tel:"


EMERGENCY_HOTLINES: tuple[Hotline, ...] = (
    Hotline("緊急救護", "119", "救護車與消防"),
    Hotline("警察報案", "110", "意外或人身安全"),
)


@dataclass(frozen=True)
class UrgencyVerdict:
    level: str
    display: str = ""
    """白話說明「是哪一點讓系統判定需要立即處置」，由判斷器以使用者的語言產生。"""

    @property
    def is_emergency(self) -> bool:
        return self.level == URGENCY_EMERGENCY

    @property
    def hotlines(self) -> tuple[Hotline, ...]:
        return EMERGENCY_HOTLINES if self.is_emergency else ()


NOT_URGENT = UrgencyVerdict(level=URGENCY_NONE)

# display 的長度上限。這行字會出現在卡片標題下方，過長會把版面撐開；同時也是
# 一道防線——模型若開始長篇解釋，代表它偏離了「一句話說明」的任務。
_MAX_DISPLAY_CHARS = 40

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "happening_now": {"type": "boolean"},
        "needs_immediate_care": {"type": "boolean"},
        "display": {"type": "string"},
    },
    "required": ["happening_now", "needs_immediate_care", "display"],
}

_PROMPT_TEMPLATE = """你的任務：判斷使用者這則訊息描述的狀況，是不是「正在發生、且需要立即急診處置」。

這是一個安全判斷，不是醫療診斷。請只回答下面三個欄位。

判準（最重要）：
判斷依據是「這件事是不是正在發生」，不是「訊息裡有沒有出現嚴重的病名」。
同一個詞出現在陳述句和知識性問句裡，意義完全不同。

happening_now：使用者或他身邊的人，此刻正處於這個狀況嗎？
  是 → 正在發生的事件、剛剛發生的事件、現在的身體狀態。
  否 → 詢問知識、詢問成因、詢問預防、詢問保險或理賠、
       描述已經過去且正在做後續照護（復健、回診、追蹤）的事。

needs_immediate_care：這個狀況需要立刻叫救護車或前往急診，
不適合等待一般門診掛號嗎？
  是的例子：意識不清、叫不醒、大量出血、呼吸困難、抽搐、
           疑似中風的徵象、劇烈胸痛、重大外傷、中毒、嚴重燒燙傷。
  否的例子：一般疼痛、發燒、咳嗽、腹瀉、皮膚問題、慢性病追蹤、
           想知道要掛哪一科。

範圍限制（重要）：
本判斷只涵蓋生理急症。**表達自殺、輕生或自傷的意念不屬於本判斷的範圍**，
兩個欄位一律回 false，交由一般流程處理。例如「我要燒炭自殺」「我想死」
「不想活了」「我想跳樓」——即使你認為情況緊迫，也請一律回 false。
這不是因為那些話不重要，而是本判斷觸發的卡片是為生理急症設計的
（內容為前往急診與 119、110），對這類訊息並不適用。

但**已經發生、且正在造成生理危險的自傷行為**仍屬本判斷範圍：
「我剛剛吞了一整罐藥」「我割腕血流不止」是進行中的中毒與出血，
兩個欄位皆回 true。分界是「意念」與「已造成的生理傷害」。

只有兩者都為「是」時，系統才會顯示緊急卡片。有疑慮時，
請依實際描述誠實判斷，不要為了保險起見一律回是——過度觸發會讓
使用者學會忽略這張卡片，真正的急症也會被忽略。

display：一句話說明「是哪一點讓你判斷需要立即處置」，{language} 書寫，
不超過 20 個字，只描述使用者說了什麼，不要加建議也不要下診斷。
例如「你提到有人失去意識、叫不醒」。
兩者不是都為「是」時，display 請回空字串。

參考判斷：
  「我阿公昏迷」→ happening_now=true, needs_immediate_care=true
  「我阿公昏迷要掛哪一科」→ happening_now=true, needs_immediate_care=true
  「昏迷的原因有哪些」→ happening_now=false
  「我剛剛被車撞，現在流好多血」→ happening_now=true, needs_immediate_care=true
  「我阿公中風了」→ happening_now=true, needs_immediate_care=true
  「中風前兆有哪些」→ happening_now=false
  「我阿公中風後在做復健，要掛哪一科」→ happening_now=false
  「我肚子痛要掛哪一科」→ happening_now=true, needs_immediate_care=false
  「食物中毒可以吃什麼」→ happening_now=false
  「我要燒炭自殺」→ happening_now=false, needs_immediate_care=false（範圍外）
  「我想跳樓」→ happening_now=false, needs_immediate_care=false（範圍外）
  「我剛剛吞了一整罐安眠藥」→ happening_now=true, needs_immediate_care=true

使用者訊息：
{text}"""


class UrgencyClassifier:
    """
    語意急迫度判斷器。

    與 SymptomDepartmentService 刻意分開：急迫度是「要不要現在就去急診」，
    科別建議是「門診該掛哪一科」，兩者是正交的問題。綁在一起正是前一版
    「沒問科別就不做安全檢查」的成因。
    """

    def __init__(
        self,
        *,
        gemini_service=None,
        invoke: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # invoke 可注入，測試才能在不打 API 的情況下驗證判斷結果的處置。
        self._gemini = gemini_service
        self._invoke = invoke
        self._timeout = timeout_seconds

    async def classify(self, text: str, *, language: str = "繁體中文") -> UrgencyVerdict:
        cleaned = (text or "").strip()
        if not cleaned:
            return NOT_URGENT

        prompt = _PROMPT_TEMPLATE.format(language=language, text=cleaned)
        try:
            raw = await asyncio.wait_for(self._call(prompt), timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 判斷逾時（%.1fs），依 fail-open 視為不緊急",
                self._timeout,
            )
            return NOT_URGENT
        except Exception:  # noqa: BLE001
            logger.error(
                f"{LOGGER_HEADER_TEXT} 判斷失敗，依 fail-open 視為不緊急",
                exc_info=True,
            )
            return NOT_URGENT

        return self._to_verdict(raw)

    def _to_verdict(self, raw: Any) -> UrgencyVerdict:
        if not isinstance(raw, dict):
            logger.warning(f"{LOGGER_HEADER_TEXT} 判斷回傳非預期型別 %r", type(raw))
            return NOT_URGENT

        # 兩個條件都必須成立。只有 needs_immediate_care 為真時多半是知識性問句
        # 提到了嚴重狀況（「中風要怎麼急救」），那不該跳出緊急卡。
        if not (raw.get("happening_now") and raw.get("needs_immediate_care")):
            return NOT_URGENT

        display = str(raw.get("display") or "").strip()
        if len(display) > _MAX_DISPLAY_CHARS:
            display = display[:_MAX_DISPLAY_CHARS]
        logger.info(f"{LOGGER_HEADER_TEXT} 判定為緊急，display=%r", display)
        return UrgencyVerdict(level=URGENCY_EMERGENCY, display=display)

    async def _call(self, prompt: str) -> dict[str, Any]:
        if self._invoke is not None:
            return await self._invoke(prompt)
        if self._gemini is None:
            raise RuntimeError("UrgencyClassifier requires gemini_service or invoke")
        structured = self._gemini.chat_model.with_structured_output(
            _SCHEMA,
            method="json_schema",
        )
        result = await structured.ainvoke([HumanMessage(content=prompt)])
        if not isinstance(result, dict):
            raise ValueError(f"unexpected urgency payload: {type(result)}")
        return result
