"""`APP_ROLE` 決定本行程要不要啟動背景排程器。

排程器與 API 拆成不同 pod 之後，這個判斷是唯一決定「誰負責推播」的地方。
判錯的兩個方向代價不對稱——多跑一份只是讓既有的原子搶佔多擋一次，一份都
沒跑則會讓當下所有服藥時段永久錯過（medication-reminders 明訂錯過不補推播）。
因此這裡窮舉包含打錯字、空字串與大小寫在內的各種輸入，確認只有明確設成
`api` 才會關掉排程器。
"""

import pytest

from app.core.config import should_run_schedulers


@pytest.mark.parametrize(
    "role",
    [
        "all",
        "scheduler",
        "ALL",
        "Scheduler",
        "  scheduler  ",
    ],
)
def test_roles_that_run_schedulers(role):
    assert should_run_schedulers(role) is True


@pytest.mark.parametrize(
    "role",
    [
        "api",
        "API",
        "  api  ",
    ],
)
def test_only_api_opts_out(role):
    assert should_run_schedulers(role) is False


@pytest.mark.parametrize(
    "role",
    [
        "",
        "   ",
        "apii",
        "web",
        "backend",
        "SCHEDULAR",  # 刻意拼錯
    ],
)
def test_unknown_roles_fail_safe_to_running(role):
    """未知或打錯的角色一律啟動排程器。

    這是刻意的失效方向：漏推播沒有補救路徑，重複推播有原子搶佔擋著。
    """
    assert should_run_schedulers(role) is True


def test_default_setting_runs_schedulers():
    """未設定 APP_ROLE 時（預設 `all`）行為與拆分前完全相同。

    這條守的是「先上程式碼、後改部署設定」這個必然會發生的中間狀態：
    此時所有 pod 的 APP_ROLE 都還沒設，不能因此沒有任何行程在推播。
    """
    from app.core.config import Settings

    assert should_run_schedulers(Settings.APP_ROLE) is True
