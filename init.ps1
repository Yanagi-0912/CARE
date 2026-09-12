# Windows PowerShell environment initialization script for CARE
$ErrorActionPreference = "Stop"

Write-Host "=== Step 1: Checking uv ===" -ForegroundColor Cyan
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "找不到 uv。安裝方式：" -ForegroundColor Red
    Write-Host '  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
    Write-Host "  （或 winget install --id=astral-sh.uv）"
    Exit 1
}
Write-Host "uv: $(uv --version)"

Write-Host "=== Step 2: Syncing Environment ===" -ForegroundColor Cyan
# uv sync 會依 .python-version 取得 CPython 3.12、建立 .venv、
# 並嚴格照 uv.lock 安裝（含 dev 群組的 pytest）
uv sync --locked
if ($LASTEXITCODE -ne 0) {
    Write-Host "Dependency installation failed!" -ForegroundColor Red
    Write-Host "若因 pyproject.toml 有改動而失敗，請先執行 'uv lock' 更新 uv.lock。" -ForegroundColor Yellow
    Exit 1
}
Write-Host "Dependency installation successful!" -ForegroundColor Green

Write-Host "=== Step 3: Running Tests ===" -ForegroundColor Cyan
uv run pytest tests/ -v
if ($LASTEXITCODE -ne 0) {
    Write-Host "Some tests failed! Please review the errors above." -ForegroundColor Red
    Exit 1
}
Write-Host "All tests passed successfully!" -ForegroundColor Green

Write-Host "`n=== Environment is Ready ===" -ForegroundColor Green
Write-Host "To start the development server, run:" -ForegroundColor Green
Write-Host "  uv run uvicorn app.main:app --port 8000 --reload --reload-exclude .venv" -ForegroundColor Yellow
