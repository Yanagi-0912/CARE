# ==============================================================================
# CARE Backend Environment Bootstrapper & Health Check (PowerShell)
# ==============================================================================

Write-Host "=== Step 1: Activating Virtual Environment ===" -ForegroundColor Cyan
if (Test-Path ".venv\Scripts\activate.ps1") {
    Write-Host "Activating Windows-style virtual environment..."
    & .venv\Scripts\activate.ps1
} elseif (Test-Path ".venv\bin\activate.ps1") {
    Write-Host "Activating Unix-style/WSL virtual environment..."
    & .venv\bin\activate.ps1
} else {
    Write-Host "❌ Virtual environment (.venv) not found! Please create it first using 'python -m venv .venv'." -ForegroundColor Red
    Exit 1
}

$PythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
$PipPath = (Get-Command pip -ErrorAction SilentlyContinue).Source
Write-Host "Python Executable: $PythonPath"
Write-Host "Pip Executable: $PipPath"
Write-Host ""

# Configure your commands here (specifically set for the CARE Python FastAPI backend):
$INSTALL_CMD = "pip install -r requirements.txt"
$VERIFY_CMD = "python -m pytest tests/ -v"
$START_CMD = "uvicorn app.main:app --port 8000 --reload --reload-exclude .venv"

Write-Host "=== Environment Info ===" -ForegroundColor Cyan
Write-Host "Current directory: $((Get-Location).Path)"
Write-Host ""

Write-Host "=== Step 2: Installing Dependencies ===" -ForegroundColor Cyan
Write-Host "Executing: $INSTALL_CMD"
Invoke-Expression $INSTALL_CMD
$INSTALL_STATUS = $LASTEXITCODE
if ($null -ne $INSTALL_STATUS -and $INSTALL_STATUS -ne 0) {
    Write-Host "❌ Dependency installation failed (exit code: $INSTALL_STATUS)!" -ForegroundColor Red
    Exit $INSTALL_STATUS
}
Write-Host "✅ Dependency installation completed successfully." -ForegroundColor Green
Write-Host ""

Write-Host "=== Step 3: Running Verification Checks (pytest) ===" -ForegroundColor Cyan
Write-Host "Executing: $VERIFY_CMD"
Invoke-Expression $VERIFY_CMD
$VERIFY_STATUS = $LASTEXITCODE
if ($null -ne $VERIFY_STATUS -and $VERIFY_STATUS -ne 0) {
    Write-Host "❌ Verification failed (exit code: $VERIFY_STATUS)!" -ForegroundColor Red
    Write-Host "⚠️  STOP! Fix the broken baseline before continuing with new features." -ForegroundColor Yellow
    Exit $VERIFY_STATUS
}
Write-Host "✅ Verification successful (All tests passed!)." -ForegroundColor Green
Write-Host ""

Write-Host "=== Step 4: Startup Info ===" -ForegroundColor Cyan
Write-Host "Standard Start Command: $START_CMD"
if ($env:RUN_START_COMMAND -eq "1") {
    Write-Host "Launching FastAPI application..." -ForegroundColor Cyan
    Invoke-Expression $START_CMD
} else {
    Write-Host "Tip: Set `$env:RUN_START_COMMAND='1' and run again to automatically launch on success." -ForegroundColor DarkGray
}
