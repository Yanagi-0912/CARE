#!/bin/bash

# ==============================================================================
# CARE Backend Environment Bootstrapper & Health Check
# ==============================================================================

echo "=== Step 1: Activating Virtual Environment ==="
# Detect and activate Python virtual environment
if [ -f ".venv/Scripts/activate" ]; then
    echo "Detected Windows-style virtual environment."
    source .venv/Scripts/activate
elif [ -f ".venv/bin/activate" ]; then
    echo "Detected Unix-style/WSL virtual environment."
    source .venv/bin/activate
else
    echo "❌ Virtual environment (.venv) not found! Please create it first using 'python -m venv .venv'."
    exit 1
fi

echo "Python Executable: $(which python)"
echo "Pip Executable: $(which pip)"
echo ""

# Configure your commands here (specifically set for the CARE Python FastAPI backend):
INSTALL_CMD="pip install -r requirements.txt"
VERIFY_CMD="python -m pytest tests/ -v"
START_CMD="uvicorn app.main:app --port 8000 --reload --reload-exclude .venv"

# Set RUN_START_COMMAND=1 to automatically run the start command on success
RUN_START_COMMAND=${RUN_START_COMMAND:-0}

echo "=== Environment Info ==="
echo "Current directory: $(pwd)"
echo ""

echo "=== Step 2: Installing Dependencies ==="
echo "Executing: $INSTALL_CMD"
eval "$INSTALL_CMD"
INSTALL_STATUS=$?
if [ $INSTALL_STATUS -ne 0 ]; then
    echo "❌ Dependency installation failed (exit code: $INSTALL_STATUS)!"
    exit $INSTALL_STATUS
fi
echo "✅ Dependency installation completed successfully."
echo ""

echo "=== Step 3: Running Verification Checks (pytest) ==="
echo "Executing: $VERIFY_CMD"
eval "$VERIFY_CMD"
VERIFY_STATUS=$?
if [ $VERIFY_STATUS -ne 0 ]; then
    echo "❌ Verification failed (exit code: $VERIFY_STATUS)!"
    echo "⚠️  STOP! Fix the broken baseline before continuing with new features."
    exit $VERIFY_STATUS
fi
echo "✅ Verification successful (All tests passed!)."
echo ""

echo "=== Step 4: Startup Info ==="
echo "Standard Start Command: $START_CMD"
if [ "$RUN_START_COMMAND" = "1" ]; then
    echo "Launching FastAPI application..."
    eval "$START_CMD"
else
    echo "Tip: Run with 'RUN_START_COMMAND=1 ./init.sh' to automatically launch on success."
fi
