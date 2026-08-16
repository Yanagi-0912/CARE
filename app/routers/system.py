from fastapi import APIRouter, HTTPException, Response

from app.core import scheduler_heartbeat
from app.core.config import settings, should_run_schedulers
from app.schemas import HealthResponse, RootResponse

router = APIRouter(tags=["系統"])


@router.get(
    "/",
    response_model=RootResponse,
    summary="根路徑",
    description="檢查 API 是否正常運行",
)
async def root():
    return {"message": "CARE Backend Running"}


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="健康檢查",
    description="回傳服務狀態",
)
async def health():
    return {"status": "Welcome to CARE Backend!"}


@router.get(
    "/health/scheduler",
    summary="排程器健康檢查",
    description=(
        "回報各背景排程器最近一次 tick 距今多久。任一排程器超過自身容忍門檻時回 503，"
        "供 scheduler pod 的 livenessProbe 使用。不跑排程器的角色一律回 200。"
    ),
)
async def scheduler_health():
    """排程器專用的健康檢查。

    為什麼不共用 `/health`：那支只證明 uvicorn 還能回應 HTTP。排程器是事件
    迴圈上的一個 asyncio task，它整個停掉的時候 uvicorn 依然健康，`/health`
    回 200，探針完全看不出異常——而用藥提醒已經停止推播，且錯過的時段不會
    補推。健康的定義必須是「最近一次 tick 距今多久」。

    APP_ROLE 不跑排程器時回 200 而非 503：這支端點掛在共用的 app 上，API pod
    也看得到。對一個本來就不該有排程器的行程回報「不健康」，會讓誤設定的
    探針把正常的 API pod 一直重啟。
    """
    if not should_run_schedulers(settings.APP_ROLE):
        return {
            "status": "not-applicable",
            "role": settings.APP_ROLE,
            "schedulers": {},
        }

    registered = scheduler_heartbeat.registered()
    if not registered:
        # 角色該跑排程器卻一個都沒登記：start() 從未被呼叫，或啟動失敗。
        # 這是真的異常，要讓探針看見。
        raise HTTPException(
            status_code=503,
            detail={
                "status": "no-scheduler-registered",
                "role": settings.APP_ROLE,
            },
        )

    stale = scheduler_heartbeat.stale()
    snapshot = scheduler_heartbeat.snapshot()
    if stale:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "stale",
                "stale": [name for name, _age in stale],
                "schedulers": snapshot,
            },
        )
    return {"status": "ok", "role": settings.APP_ROLE, "schedulers": snapshot}


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)
