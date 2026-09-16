"""啟動期的設定檢查：拿著會讓整套認證失效的設定，行程就不該起來。

這些檢查放在 lifespan 的最前面、建索引之前。理由是失敗模式的不對稱：

- 檢查失敗 → pod 起不來，readiness probe 不過，滾動更新停在舊版，使用者無感。
- 不檢查     → JWT 用的是程式碼裡寫死的預設密鑰（2026-04 到 09 線上就是這樣，
  見 memory care-jwt-secret-missing），任何讀過原始碼的人都能替任何 line_user_id
  簽一張有效的 token；而且沒有任何錯誤訊息，要到資安檢視才會發現。

`APP_ENV` 缺席時視為 production：缺一個環境變數的後果應該是「起不來」，
不是「靜靜地用開發設定跑正式流量」。
"""

import logging
from typing import Any, List

logger = logging.getLogger(__name__)

# config.py 裡的預設值。這裡再寫一次而不是從 config 讀，是因為要比對的正是
# 「設定值等不等於那個預設」——從同一個地方讀就永遠相等。
DEFAULT_JWT_SECRET = "dev-only-change-me"

# RFC 7518 §3.2：HMAC-SHA 的金鑰長度 MUST 不小於雜湊輸出（HS256 為 256 位元＝
# 32 位元組），更短的金鑰會讓暴力搜尋空間小於演算法本身的安全強度。
MIN_JWT_SECRET_LENGTH = 32

# 只允許對稱的 HMAC 家族。`jwt_service` 簽發與驗證用的是同一把 `AUTH_JWT_SECRET`，
# 設成 RS256 之類的非對稱演算法會讓 PyJWT 把那個字串當成私鑰去解析而在第一次
# 登入時才炸；設成 `none` 則任何未簽章的 token 都會被接受。兩者都該在啟動時擋。
ALLOWED_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})

DEVELOPMENT_ENV = "development"


def _is_development(app_env: str) -> bool:
    return (app_env or "").strip().lower() == DEVELOPMENT_ENV


def validate_runtime_config(settings: Any) -> None:
    """檢查認證相關設定；有任何一項不合格就拋 RuntimeError，一次列出全部問題。

    一次列全部而不是碰到第一個就拋：部署的人修一個、重啟、再看到下一個，
    每一輪都是一次滾動更新的時間。

    `AUTH_JWT_SECRET` 的檢查在 `APP_ENV=development` 時降為警告——本機開發沒有
    理由生一把正式密鑰。其餘檢查（LINE 憑證、演算法）在任何環境都是硬性的：
    少了 LINE 憑證 webhook 簽章根本驗不了，演算法錯了則是登入必炸。
    """
    problems: List[str] = []
    app_env = getattr(settings, "APP_ENV", "production")

    secret = getattr(settings, "AUTH_JWT_SECRET", "") or ""
    secret_problem = None
    if secret == DEFAULT_JWT_SECRET:
        secret_problem = (
            "AUTH_JWT_SECRET 仍是程式碼裡的預設值，任何讀過原始碼的人都能替"
            "任意使用者簽發有效 token"
        )
    elif len(secret) < MIN_JWT_SECRET_LENGTH:
        # 太短只警告、不擋：正式環境的密鑰由 GitHub secret 注入，這裡看不到
        # 長度；若既有的那把剛好短於 32 字元，拒絕啟動會讓滾動更新停在舊版，
        # 而「短但隨機」的密鑰不是當下的漏洞。預設值才是必須擋的那個。
        logger.warning(
            "AUTH_JWT_SECRET 長度 %d 不足 %d 字元（RFC 7518 §3.2 要求 HS256 金鑰"
            "至少 256 位元），請換一把更長的",
            len(secret),
            MIN_JWT_SECRET_LENGTH,
        )
    if secret_problem is not None:
        if _is_development(app_env):
            logger.warning(
                "【開發環境】%s——正式環境會拒絕啟動，請勿把這個設定帶上線",
                secret_problem,
            )
        else:
            problems.append(secret_problem)

    for name in ("LINE_CHANNEL_ID", "LINE_CHANNEL_SECRET"):
        if not (getattr(settings, name, None) or "").strip():
            problems.append(f"{name} 未設定，LINE webhook 的簽章與 LIFF 登入都無法驗證")

    algorithm = getattr(settings, "AUTH_JWT_ALGORITHM", "") or ""
    if algorithm not in ALLOWED_JWT_ALGORITHMS:
        problems.append(
            f"AUTH_JWT_ALGORITHM={algorithm!r} 不在允許清單 "
            f"{sorted(ALLOWED_JWT_ALGORITHMS)} 內"
        )

    if problems:
        raise RuntimeError(
            "啟動設定檢查未通過（APP_ENV=%s）：\n  - %s"
            % (app_env, "\n  - ".join(problems))
        )

    logger.info("啟動設定檢查通過：APP_ENV=%s, jwt_alg=%s", app_env, algorithm)


def openapi_visibility(app_env: str) -> dict:
    """`FastAPI(...)` 的文件相關參數：只有開發環境才開 /docs、/redoc、/openapi.json。

    正式環境關掉的理由不是「藏起來就安全」，而是 openapi.json 把每一支端點、
    參數與回應模型（含家庭授權的欄位分類）整份攤給未登入的人，是最省力的
    攻擊面盤點。開發時要看文件，設 APP_ENV=development。
    """
    if _is_development(app_env):
        return {
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        }
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}
