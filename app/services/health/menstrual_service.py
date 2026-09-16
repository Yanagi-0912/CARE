"""經期紀錄 (menstrual_records) 的服務層（menstrual-cycle-log spec「僅女性
使用者可建立」「記錄經期」「週期資訊由後端計算」）。

跨使用者的授權判定不在這裡——同 ``health_alert_threshold_service``／
``health_measurement_service`` 的慣例，由
``app/routers/users/health.py`` 直接處理；唯一不同的是，經期是 PERSONAL
分類（只有本人可讀寫，任何家人角色一律無權，見
``app/models/family_authorization.py``），因此 router 那一層比對的是操作者
與資料所有者的識別碼是否相同，SHALL NOT 呼叫 ``FamilyAuthorizationService``
——經期不經家庭矩陣。這裡只負責本人資料的業務規則：

- 建立限本人個人健康檔案性別為「女性」，否則 403（gender 只在 CREATE
  檢查；GET／PATCH／DELETE 對既有紀錄不重新檢查，性別變更後舊紀錄仍可
  管理，見 spec「變更性別後仍可管理舊紀錄」）。
- 重疊檢查：交給 ``MenstrualRecordRepository.find_overlapping``，一筆沒有
  ``end_date`` 的紀錄（進行中或本次要新增／更新成的）視為佔滿
  ``[start, start + MENSTRUAL_MAX_SPAN_DAYS]``——15 天是輸入驗證允許的
  最長合法經期天數，也是這裡用來界定「開放中的紀錄佔用多久」的同一個
  常數（見 repository 該方法的說明）。
- PATCH 是部分更新（``exclude_unset``），套用在既有紀錄之上後，整筆重新
  以 ``CreateMenstrualRecordRequest`` 的規則驗證（結束不早於開始、間隔
  不超過 15 天、開始不晚於今天）——不重複寫一份規則，直接借用建立時的
  驗證模型。
- 查詢／建立／更新的回應都附上後端計算的 ``cycle_length_days``（與前一筆
  開始日期的間隔）與 ``period_length_days``（含頭尾兩天的經期天數）。
"""

import logging
from datetime import date, timedelta
from typing import Any, List, Optional

from fastapi import HTTPException
from pydantic import ValidationError

from app.models.health import (
    CreateMenstrualRecordRequest,
    MenstrualRecord,
    UpdateMenstrualRecordRequest,
)
from app.repositories.menstrual_record_repository import MenstrualRecordRepository
from app.services.health.menstrual_anomaly import detect_menstrual_anomaly
from app.services.users.user_profile_service import UserProfileService

logger = logging.getLogger(__name__)

# health-alerts spec「僅女性使用者可建立」：性別未填或非女性時的說明，
# 訊息本身要讓使用者知道該去哪裡處理——同 app/routers/users/appointments.py
# 的 FORBIDDEN_*_DETAIL 慣例（純文字常數，不是 LINE 推播才需要的多語系
# 訊息目錄；這支端點只服務 LIFF 內建、以繁體中文呈現的個人健康頁）。
GENDER_REQUIRED_DETAIL = "請先至個人健康頁設定性別為女性，才能新增經期紀錄。"

MENSTRUAL_OVERLAP_DETAIL = "這段日期與既有的經期重疊，請確認開始／結束日期。"

_RECORD_NOT_FOUND_DETAIL = "找不到該筆紀錄"


class MenstrualRecordService:
    """``menstrual_records`` 的讀寫。repository／user profile service 皆以
    類別或物件注入，方便測試換成假的（同專案其餘 health 服務的慣例）。"""

    def __init__(
        self,
        repository: type[MenstrualRecordRepository] = MenstrualRecordRepository,
        user_profile_service: Optional[UserProfileService] = None,
        alert_service: Any = None,
    ) -> None:
        self._repository = repository
        self._user_profile_service = user_profile_service
        # Task 7：經期異常只通知本人（health-alerts spec）。型別刻意是
        # ``Any``（同 ``health_measurement_service`` 的理由），選填是因為
        # 既有呼叫端（測試）尚未全部升級到會注入它。
        self._alert_service = alert_service

    async def create(
        self, user_id: str, request: CreateMenstrualRecordRequest
    ) -> MenstrualRecord:
        """新增一筆經期紀錄。``user_id`` 已由呼叫端（router）確認就是操作者
        本人——經期沒有代記，也沒有代理寫入。"""
        await self._require_female(user_id)

        overlapping = await self._repository.find_overlapping(
            user_id=user_id,
            start_date=request.start_date,
            end_date=request.end_date,
        )
        if overlapping:
            raise HTTPException(status_code=409, detail=MENSTRUAL_OVERLAP_DETAIL)

        record = MenstrualRecord(
            user_id=user_id,
            start_date=request.start_date,
            end_date=request.end_date,
            flow=request.flow,
            note=request.note,
        )
        saved = await self._repository.add(record)
        # ── 存檔之後 ──────────────────────────────────────────────────
        # 經期異常通知（health-alerts spec「經期異常只通知本人」）。建立時
        # 一律檢查一次：cycle_length_days／period_length_days 已經由
        # `_with_computed_fields_for` 算好，直接餵給 `detect_menstrual_anomaly`
        # 即可，不需要另外查詢「前一筆」。推播 SHALL 只通知本人、SHALL NOT
        # 通知任何家人；推播失敗 SHALL NOT 影響這支請求的結果，因此掛在
        # 存檔「之後」。
        computed = await self._with_computed_fields_for(user_id, saved)
        await self._maybe_notify_anomaly(user_id, computed)
        return computed

    async def list(self, user_id: str) -> List[MenstrualRecord]:
        """查詢本人全部經期紀錄，新到舊排序、單次回應至多 200 筆（repository
        的責任），並附上後端計算的週期長度與經期天數。"""
        records = await self._repository.list_by_user(user_id)
        return _compute_fields_for_list(records)

    async def get(self, record_id: str) -> MenstrualRecord:
        """取得單筆紀錄，供呼叫端（router）在比對操作者與所有者識別碼之前
        得知這筆紀錄屬於誰。不存在時 SHALL 回 404。"""
        record = await self._repository.get_by_id(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail=_RECORD_NOT_FOUND_DETAIL)
        return record

    async def update(
        self, record_id: str, request: UpdateMenstrualRecordRequest
    ) -> MenstrualRecord:
        """部分更新。存在性已由呼叫端（router）先呼叫 ``get`` 確認過並比對
        過所有者，這裡仍再確認一次存在性（服務層不假設呼叫順序），因為
        合併更新需要既有紀錄的完整內容。"""
        existing = await self.get(record_id)

        changes = request.model_dump(exclude_unset=True)
        merged_start = changes.get("start_date", existing.start_date)
        merged_end = changes["end_date"] if "end_date" in changes else existing.end_date
        merged_flow = changes["flow"] if "flow" in changes else existing.flow
        merged_note = changes["note"] if "note" in changes else existing.note

        # 整筆重新驗證（結束不早於開始、間隔不超過 15 天、開始不晚於今天）
        # ——直接借用建立時的規則，不重複寫一份（見本模組 docstring）。
        try:
            CreateMenstrualRecordRequest(
                start_date=merged_start,
                end_date=merged_end,
                flow=merged_flow,
                note=merged_note,
            )
        except ValidationError as exc:
            # Task 1 修復：detail SHALL 是 FastAPI 對「Pydantic 邊界」422 的
            # 慣例陣列形狀（``[{"type", "loc", "msg", ...}, ...]``），不是
            # ``str(ValidationError)``——後者是一整段人類調適用的多行文字
            # （含模型類別名稱、方括號型別中繼資料、使用者原樣回顯的輸入、
            # pydantic 文件連結），前端 `extractValidationMessages` 只認得
            # 陣列＋每項的 `msg` 字串（見 CARE-LIFF `src/api/healthApi.ts`），
            # 字串 detail 會被整段原樣顯示給使用者。這裡雖然不是走 FastAPI
            # 的請求邊界（是服務層合併欄位後重新驗證），仍要產出同一種形狀，
            # 讓前端解析邏輯不必分兩套。``include_url=False``：文件連結對
            # 使用者沒有意義，不必送到前端。
            raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc

        overlapping = await self._repository.find_overlapping(
            user_id=existing.user_id,
            start_date=merged_start,
            end_date=merged_end,
            exclude_id=record_id,
        )
        if overlapping:
            raise HTTPException(status_code=409, detail=MENSTRUAL_OVERLAP_DETAIL)

        updated = await self._repository.update(
            record_id,
            {
                "start_date": merged_start,
                "end_date": merged_end,
                "flow": merged_flow,
                "note": merged_note,
            },
        )
        if updated is None:
            raise HTTPException(status_code=404, detail=_RECORD_NOT_FOUND_DETAIL)
        # ── 存檔之後 ──────────────────────────────────────────────────
        # PATCH 補上／改動 start_date 或 end_date 時要判定經期異常（週期
        # 長度 < 24 或 > 38 天、經期天數 > 8 天；dispatch notes「trigger
        # points」）。單純改 note／flow 不會讓這兩個計算欄位有任何變化，
        # 不必多一次 claim 嘗試。推播失敗不影響這支請求。
        computed = await self._with_computed_fields_for(existing.user_id, updated)
        # Task 8 修復：只在 `end_date` 出現在這次請求裡才檢查異常，漏看了
        # `start_date` 單獨變動也可能讓「週期長度」（與前一筆 start_date 的
        # 間隔）新產生一個落在異常區間的值——搬動開始日期不會改變這筆紀錄
        # 本身的「經期天數」，但會改變它與前一筆的「週期長度」，兩者都是
        # `detect_menstrual_anomaly` 判定的依據。因此只要 `start_date` 或
        # `end_date` 任一個真的出現在這次請求裡，就要重新檢查一次；單純改
        # note／flow 兩者都不出現，維持原本「不必多一次 claim 嘗試」的優化。
        # 「一筆紀錄最多通知一次」的語意不變——`_maybe_notify_anomaly` 背後
        # 的 claim 仍是 `menstrual:<record_id>`，同一筆紀錄不論被檢查幾次，
        # 只有第一次成立的異常會真的推播。
        if "start_date" in changes or "end_date" in changes:
            await self._maybe_notify_anomaly(existing.user_id, computed)
        return computed

    async def delete(self, record_id: str) -> None:
        """刪除一筆紀錄。存在性與所有權已由呼叫端（router）確認過。"""
        await self._repository.delete(record_id)

    async def _maybe_notify_anomaly(self, user_id: str, record: MenstrualRecord) -> None:
        """判定這筆（已附計算欄位的）紀錄是否異常，異常才呼叫推播服務。

        ``previous_start`` 由 ``record.cycle_length_days`` 反推
        （``cycle_length_days = (start - previous_start).days``，見
        ``_compute_fields_for_list``），不必為了取得前一筆的開始日期另外
        查一次資料庫——那份資訊已經在 ``_with_computed_fields_for`` 算過了。

        整段（含日期解析與異常判定，不只是推播那一次呼叫）都包在 try/except
        裡：紀錄已經先寫入、這裡只是「寫入之後」的附加動作，任何一步失敗
        （包括存進資料庫的日期字串因故壞掉、``date.fromisoformat`` 拋錯）都
        SHALL NOT 讓已經成功的請求變成 500（spec「推播失敗不影響紀錄」——
        這裡從嚴解讀為「這整段附加動作失敗」，不只是「LINE 推播本身失敗」）。
        """
        if self._alert_service is None or not record.id:
            return
        try:
            start = date.fromisoformat(record.start_date)
            end = date.fromisoformat(record.end_date) if record.end_date else None
            previous_start = (
                start - timedelta(days=record.cycle_length_days)
                if record.cycle_length_days is not None
                else None
            )
            if not detect_menstrual_anomaly(previous_start, start, end):
                return
            await self._alert_service.notify_menstrual_anomaly(user_id, record.id)
        except Exception:  # noqa: BLE001
            logger.warning("經期異常判定或推播失敗，紀錄本身不受影響", exc_info=True)

    async def _require_female(self, user_id: str) -> None:
        """建立限本人個人健康檔案性別為「女性」（spec「僅女性使用者可
        建立」）。只在 CREATE 呼叫——GET／PATCH／DELETE 不呼叫這個方法。
        """
        profile = None
        if self._user_profile_service is not None:
            profile = await self._user_profile_service.get_user_profile(user_id)
        gender = (profile or {}).get("gender")
        if gender != "female":
            raise HTTPException(status_code=403, detail=GENDER_REQUIRED_DETAIL)

    async def _with_computed_fields_for(
        self, user_id: str, record: MenstrualRecord
    ) -> MenstrualRecord:
        """POST／PATCH 的回應也要附上與 GET 相同的計算欄位（dispatch notes
        「計算欄位」）：重新查一次本人全部紀錄、算好之後取出這一筆。一位
        使用者的經期紀錄量不大，多這一次查詢可忽略。"""
        records = _compute_fields_for_list(await self._repository.list_by_user(user_id))
        for computed in records:
            if computed.id == record.id:
                return computed
        # 理論上不會發生：剛寫入／更新的紀錄一定在自己的查詢結果裡。保底
        # 回傳未附計算欄位的原始紀錄，不讓這支請求整個失敗。
        return record


def _compute_fields_for_list(records: List[MenstrualRecord]) -> List[MenstrualRecord]:
    """依「新到舊」排序的紀錄清單，逐筆算出 ``cycle_length_days``（與下一筆
    ——即時間上較早的那一筆——開始日期的間隔）與 ``period_length_days``
    （含頭尾兩天的經期天數）。假設 ``records`` 已由 repository 排序好，這裡
    不重新排序。
    """
    computed: List[MenstrualRecord] = []
    for index, record in enumerate(records):
        cycle_length_days: Optional[int] = None
        if index + 1 < len(records):
            previous = records[index + 1]
            this_start = date.fromisoformat(record.start_date)
            previous_start = date.fromisoformat(previous.start_date)
            cycle_length_days = (this_start - previous_start).days

        period_length_days: Optional[int] = None
        if record.end_date:
            start = date.fromisoformat(record.start_date)
            end = date.fromisoformat(record.end_date)
            period_length_days = (end - start).days + 1

        computed.append(
            record.model_copy(
                update={
                    "cycle_length_days": cycle_length_days,
                    "period_length_days": period_length_days,
                }
            )
        )
    return computed
