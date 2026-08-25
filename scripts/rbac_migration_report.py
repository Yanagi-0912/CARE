#!/usr/bin/env python3
"""列印 RBAC 遷移就緒判準所需的指標。

**這支腳本只報數字，不下結論。** 它不會說「可以切換了」——判準 1 與判準 3 的
門檻是部署決策，需要看當時的實際分布才判斷得出來，寫進 repo 等於把那個決定
凍結在寫程式的那一天。

對應 `specs/family-authorization/spec.md` 的「遷移就緒判準」：

    判準 1  收緊差異（legacy 允許、RBAC 拒絕）佔全部判定的比例
    判準 2  放寬差異（legacy 拒絕、RBAC 允許）—— **必須為 0**
    判準 3  有族譜成員且已完成角色指派的擁有者比例
    判準 4  仍在產生收緊差異的擁有者，可逐一列舉

判準 2 是唯一在這裡就能下結論的一項：它不是門檻，是布林。非零代表角色解析
或矩陣有錯，那是 bug 訊號，不是遷移資訊。

用法：
    python scripts/rbac_migration_report.py
    python scripts/rbac_migration_report.py --owner U_E2E_OWNER
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def percent(numerator: int, denominator: int) -> str:
    if not denominator:
        return "n/a（尚無資料）"
    return f"{numerator / denominator * 100:.2f}%  ({numerator}/{denominator})"


async def report(owner_id: str | None) -> int:
    sys.path.insert(0, str(PROJECT_ROOT))
    from app.repositories.family_rbac_metrics_repository import (
        FamilyRbacMetricsRepository,
    )
    from app.repositories.family_tree_repository import FamilyTreeRepository

    if owner_id:
        counters = await FamilyRbacMetricsRepository.get(owner_id)
        print(f"擁有者：{owner_id}")
        print(f"  判定次數        {counters['decisions']}")
        print(f"  收緊差異        {percent(counters['tighten'], counters['decisions'])}")
        print(f"  放寬差異        {counters['loosen']}"
              + ("   ← 非零即為 bug，修正後重新觀察" if counters["loosen"] else ""))
        print(f"  最後一次差異    {counters['last_diff_at']}")
        return 0

    totals = await FamilyRbacMetricsRepository.totals()
    progress = await FamilyTreeRepository.count_assignment_progress()
    offenders = await FamilyRbacMetricsRepository.list_owners_with_tighten()

    print("=== 全體彙總（回答「這波 rollout 值不值得推」，不是單一擁有者的准入條件）===")
    print(f"  判定次數                {totals['decisions']}")
    print(f"  判準 1  收緊差異比例    {percent(totals['tighten'], totals['decisions'])}")
    print(f"  判準 2  放寬差異        {totals['loosen']}"
          + ("   ← 非零即為 bug，SHALL 修正後重新觀察" if totals["loosen"] else "   ✓"))
    print(
        f"  判準 3  已完成指派比例  "
        f"{percent(progress['owners_complete'], progress['owners_with_members'])}"
    )
    print()
    print("=== 判準 4：仍在產生收緊差異的擁有者（由多到少）===")
    if not offenders:
        print("  （無）")
    for row in offenders:
        print(
            f"  {row['owner_id']:24} 收緊={row['tighten']:<6} "
            f"判定={row['decisions']:<6} 比例={percent(row['tighten'], row['decisions'])}"
        )
    print()
    print("門檻由部署時決定；本報告不判斷是否達標。")
    return 0


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--owner", default=None, help="只看單一擁有者（判準要問的是逐家庭的問題）"
    )
    args = parser.parse_args()

    if not os.getenv("MONGODB_URI"):
        print("未設定 MONGODB_URI，無法讀取指標。", file=sys.stderr)
        return 1
    return asyncio.run(report(args.owner))


if __name__ == "__main__":
    raise SystemExit(main())
