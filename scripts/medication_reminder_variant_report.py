#!/usr/bin/env python3
"""用藥提醒拉霸的現況：每個語氣、每個催促時機送了幾頓、準時按了幾頓。

「有結果」只算 T+30 之前按或沒按已經分曉的那幾頓（判定與拉霸學習共用
reminder_variants.outcome_of），還沒走完的不算。

語氣分成「家屬設定的提醒」與「長輩自己設的提醒」兩張表：家人版只會出現在前者，
兩群長輩原本的準時率若不同，放在同一張表比會把這個差距誤讀成語氣的效果。

讀數時要注意：拉霸會把較多的頓數分給看起來較好的選項，各選項的頓數不平均、也不是
隨機均分，所以這裡的準時率不能直接拿來做兩樣本檢定。常說的「兩個選項差 10 個
百分點，每個選項約要 390 頓才分得出來」（兩比例樣本數公式，檢定力 80%、顯著水準
5%，準時率在 50% 附近）只在固定、平均分配時成立，這裡只能當量級參考。

**全程唯讀。**

用法（專案根目錄）：
  uv run python scripts/medication_reminder_variant_report.py
"""

import asyncio
import sys
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.db.mongodb import MongoDBManager  # noqa: E402
from app.models.medication import MedicationLog  # noqa: E402
from app.repositories.medication_repository import MedicationLogRepository  # noqa: E402
from app.services.medication.reminder_variants import (  # noqa: E402
    NUDGE_MINUTES,
    TONE_FAMILY,
    TONES,
    is_family_managed,
    outcome_of,
)

_LABELS = {
    "tone_family_set": "語氣（家屬設定的提醒）",
    "tone_self_set": "語氣（長輩自己設的提醒）",
    "nudge_minutes": "催促（最晚服藥時刻後幾分鐘）",
}


def summarize(history: Iterable[MedicationLog]) -> dict[str, list[tuple]]:
    """每張表是 `[(選項, 有結果的頓數, 準時按的頓數), ...]`。

    選項依固定順序全部列出，沒資料的也列成 0，看得出哪一個還沒被試到。自己設的
    提醒沒有家人版。時機被清掉（長輩當天改了提醒設定）的那一頓只算進語氣。
    """
    tally = {
        "tone_family_set": {arm: [0, 0] for arm in TONES},
        "tone_self_set": {arm: [0, 0] for arm in TONES if arm != TONE_FAMILY},
        "nudge_minutes": {arm: [0, 0] for arm in NUDGE_MINUTES},
    }
    for log in history:
        outcome = outcome_of(log)
        if outcome is None:
            continue
        tone_table = "tone_family_set" if is_family_managed(log) else "tone_self_set"
        for table, arm in ((tone_table, log.reminder_tone), ("nudge_minutes", log.nudge_minutes)):
            if arm in tally[table]:
                tally[table][arm][0] += 1
                tally[table][arm][1] += int(outcome)
    return {
        table: [(arm, resolved, on_time) for arm, (resolved, on_time) in counts.items()]
        for table, counts in tally.items()
    }


async def main() -> None:
    MongoDBManager.configure(settings.MONGODB_URI)
    history = await MedicationLogRepository.list_variant_outcomes()
    users = len({log.user_id for log in history})
    print(f"有結果的提醒 {len(history)} 頓，來自 {users} 位長輩\n")
    for table, rows in summarize(history).items():
        print(_LABELS[table])
        for arm, resolved, on_time in rows:
            rate = f"{on_time / resolved:.1%}" if resolved else "-"
            print(f"  {str(arm):<8} {resolved:>5} 頓  準時按 {on_time:>5}  {rate}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
