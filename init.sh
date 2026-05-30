#!/bin/bash
set -e

# ANSI escape codes for colors
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${CYAN}=== Step 1: Activating Virtual Environment ===${NC}"
VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Creating Python virtual environment...${NC}"
    python3 -m venv .venv
fi

# Detect OS to activate virtual environment
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
    PYTHON_EXE="$VENV_DIR/Scripts/python.exe"
    PIP_EXE="$VENV_DIR/Scripts/pip.exe"
else
    PYTHON_EXE="$VENV_DIR/bin/python"
    PIP_EXE="$VENV_DIR/bin/pip"
fi

echo "Python Executable: $PYTHON_EXE"
echo "Pip Executable: $PIP_EXE"
echo "Current directory: $SCRIPT_DIR"

echo -e "${CYAN}=== Step 2: Installing Dependencies ===${NC}"
if $PIP_EXE install -r requirements.txt; then
    echo -e "${GREEN}Dependency installation successful!${NC}"
else
    echo -e "${RED}Dependency installation failed!${NC}"
    exit 1
fi

echo -e "${CYAN}=== Step 3: Running Tests ===${NC}"
if $PYTHON_EXE -m pytest tests/ -v; then
    echo -e "${GREEN}All tests passed successfully!${NC}"
else
    echo -e "${RED}Some tests failed! Please review the errors above.${NC}"
    exit 1
fi

echo -e "\n${GREEN}=== Environment is Ready ===${NC}"
echo -e "To start the development server, run:"
echo -e "  $PYTHON_EXE -m uvicorn app.main:app --port 8000 --reload --reload-exclude .venv"
