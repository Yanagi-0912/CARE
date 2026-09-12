"""看診紀錄的彙整規則。

釘住的是「一次看診」怎麼界定。分組錯了畫面上會出現同一家醫院三列，而長輩
只去了一次——實測資料就是這樣：同一個藥袋在 42 分鐘內被掃了三次。
"""

import pytest

from app.models.medication import MedicationVisit
from app.services.medication.medication_service import MedicationService


class _Repo:
    """只實作 list_visits 的替身。彙整本身是一次 Mongo aggregate，
    這裡驗的是它上面那一層的轉換與端點行為。"""

    def __init__(self, rows):
        self._rows = rows

    async def list_visits(self, user_id):
        return self._rows


def _service(rows) -> MedicationService:
    service = MedicationService.__new__(MedicationService)
    service._medication_repository = _Repo(rows)
    return service


@pytest.mark.asyncio
async def test_rows_become_visit_models():
    service = _service(
        [
            {
                "institution": "臺大醫院",
                "dispensed_date": "2026-08-16",
                "medication_ids": ["m1", "m2"],
                "medication_names": ["安樂筋錠", "勿炎糖衣錠"],
                "scan_count": 3,
                "first_created_at": None,
            }
        ]
    )

    (visit,) = await service.get_user_visits("u1")

    assert isinstance(visit, MedicationVisit)
    assert visit.institution == "臺大醫院"
    assert visit.medication_names == ["安樂筋錠", "勿炎糖衣錠"]
    # 同一次看診掃了三次，仍然只是一次看診——scan_count 保留那個事實供除錯，
    # 但不是分組依據。
    assert visit.scan_count == 3


@pytest.mark.asyncio
async def test_missing_institution_is_kept_as_one_group():
    """手動新增的藥、以及欄位落地之前的舊紀錄都沒有機構名。把它們各自當成
    獨立看診會產生一堆空白列，因此歸入同一組，由呈現面標示「未記錄來源」。"""
    service = _service(
        [
            {
                "institution": None,
                "dispensed_date": None,
                "medication_ids": ["m1", "m2", "m3"],
                "medication_names": ["A", "B", "C"],
                "scan_count": 0,
                "first_created_at": None,
            }
        ]
    )

    (visit,) = await service.get_user_visits("u1")

    assert visit.institution is None
    assert len(visit.medication_ids) == 3


@pytest.mark.asyncio
async def test_empty_history():
    assert await _service([]).get_user_visits("u1") == []
