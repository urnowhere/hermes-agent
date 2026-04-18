#!/bin/bash
# ============================================================================
# Hermes Web Console GUI Setup Script
# ============================================================================
# Quick setup for installing frontend dependencies and ensuring the base
# Hermes agent environment is ready.
#
# Usage:
#   ./setup-gui.sh
# ============================================================================

set -e

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${CYAN}⚕ Hermes Web Console Setup${NC}"
echo ""

# 1. Base Hermes Setup Check
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}⚠${NC} Base Hermes environment (venv) not found."
    echo -e "${CYAN}→${NC} Running base setup script (setup-hermes.sh)..."
    bash "$SCRIPT_DIR/setup-hermes.sh"
else
    echo -e "${GREEN}✓${NC} Base Hermes environment (venv) found."
fi

# 2. Node.js Check
echo -e "${CYAN}→${NC} Checking for Node.js..."
if ! command -v node &> /dev/null; then
    echo -e "${RED}✗ Node.js is required but not installed.${NC}"
    echo "Please install Node.js (https://nodejs.org/) and run this script again."
    exit 1
fi
echo -e "${GREEN}✓${NC} Node.js found ($(node -v))"

# 3. npm Check
if ! command -v npm &> /dev/null; then
    echo -e "${RED}✗ npm is required but not installed.${NC}"
    echo "Please install npm to continue."
    exit 1
fi
echo -e "${GREEN}✓${NC} npm found ($(npm -v))"

# 4. Install Frontend Dependencies
echo -e "${CYAN}→${NC} Installing frontend dependencies in ./web_console ..."
cd "$SCRIPT_DIR/web_console"
npm install

echo ""
echo -e "${GREEN}✓ Setup complete!${NC}"
echo ""
echo "To run the Web Console GUI, simply use the following 1-line command:"
echo -e "${CYAN}  ./run-gui.sh${NC}"
echo ""
