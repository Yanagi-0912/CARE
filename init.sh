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

echo -e "${CYAN}=== Step 1: Checking uv ===${NC}"
if ! command -v uv >/dev/null 2>&1; then
    echo -e "${RED}找不到 uv。安裝方式：${NC}"
    echo -e "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo -e "  （或 brew install uv）"
    exit 1
fi
echo "uv: $(uv --version)"

echo -e "${CYAN}=== Step 2: Syncing Environment ===${NC}"
# uv sync 會依 .python-version 取得 CPython 3.12、建立 .venv、
# 並嚴格照 uv.lock 安裝（含 dev 群組的 pytest）
if uv sync --locked; then
    echo -e "${GREEN}Dependency installation successful!${NC}"
else
    echo -e "${RED}Dependency installation failed!${NC}"
    echo -e "${YELLOW}若因 pyproject.toml 有改動而失敗，請先執行 'uv lock' 更新 uv.lock。${NC}"
    exit 1
fi

echo -e "${CYAN}=== Step 3: Running Tests ===${NC}"
if uv run pytest tests/ -v; then
    echo -e "${GREEN}All tests passed successfully!${NC}"
else
    echo -e "${RED}Some tests failed! Please review the errors above.${NC}"
    exit 1
fi

echo -e "\n${GREEN}=== Environment is Ready ===${NC}"
echo -e "To start the development server, run:"
echo -e "  uv run uvicorn app.main:app --port 8000 --reload --reload-exclude .venv"
