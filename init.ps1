# Windows PowerShell environment initialization script for CARE
$ErrorActionPreference = "Stop"

Write-Host "=== Step 1: Activating Virtual Environment ===" -ForegroundColor Cyan
$VenvDir = Join-Path $PSScriptRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating Python virtual environment..." -ForegroundColor Yellow
    python -m venv .venv
}

Write-Host "Python Executable: $VenvPython"
Write-Host "Pip Executable: $VenvPip"
Write-Host "Current directory: $PSScriptRoot"

Write-Host "=== Step 2: Installing Dependencies ===" -ForegroundColor Cyan
try {
    & $VenvPip install -r requirements.txt
    Write-Host "Dependency installation successful!" -ForegroundColor Green
} catch {
    Write-Host "Dependency installation failed!" -ForegroundColor Red
    Exit 1
}

Write-Host "=== Step 3: Running Tests ===" -ForegroundColor Cyan
try {
    & $VenvPython -m pytest tests/ -v
    Write-Host "All tests passed successfully!" -ForegroundColor Green
} catch {
    Write-Host "Some tests failed! Please review the errors above." -ForegroundColor Red
    Exit 1
}

Write-Host "`n=== Environment is Ready ===" -ForegroundColor Green
Write-Host "To start the development server, run:" -ForegroundColor Green
Write-Host "  & `"$VenvPython`" -m uvicorn app.main:app --port 8000 --reload --reload-exclude .venv" -ForegroundColor Yellow
