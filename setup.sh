#!/usr/bin/env bash
# Docksmith Setup Script
# Run this once on your WSL/Linux system to install and initialize Docksmith.
#
# Usage: bash setup.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}  ____             _            _ _   _     ${NC}"
echo -e "${CYAN}${BOLD} |  _ \  ___   ___| | ___ _ __ (_) |_| |__  ${NC}"
echo -e "${CYAN}${BOLD} | | | |/ _ \\ / __| |/ __| '_ \\| | __| '_ \\ ${NC}"
echo -e "${CYAN}${BOLD} | |_| | (_) | (__| | (__| | | | | |_| | | |${NC}"
echo -e "${CYAN}${BOLD} |____/ \\___/ \\___|_|\\___|_| |_|_|\\__|_| |_|${NC}"
echo ""
echo -e "${BOLD}  Setup & Installation${NC}"
echo "  ─────────────────────────────────────────"
echo ""

# Check Python 3
echo -e "${CYAN}[1/4]${NC} Checking Python 3..."
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}ERROR: Python 3 is required but not found.${NC}"
    echo "  Install with: sudo apt-get install python3"
    exit 1
fi
PY=$(python3 --version)
echo -e "      ${GREEN}✓${NC} $PY"

# Check unshare
echo -e "${CYAN}[2/4]${NC} Checking Linux namespace tools..."
if ! command -v unshare &>/dev/null; then
    echo -e "${RED}ERROR: 'unshare' not found. Install util-linux:${NC}"
    echo "  sudo apt-get install util-linux"
    exit 1
fi
if ! command -v chroot &>/dev/null; then
    echo -e "${RED}ERROR: 'chroot' not found.${NC}"
    exit 1
fi
echo -e "      ${GREEN}✓${NC} unshare available"
echo -e "      ${GREEN}✓${NC} chroot available"

# Test namespace support
echo -e "${CYAN}[2/4]${NC} Testing namespace isolation..."
if unshare --mount --uts --map-root-user --fork echo "ok" &>/dev/null; then
    echo -e "      ${GREEN}✓${NC} User namespaces working (unprivileged operation confirmed)"
else
    echo -e "${YELLOW}WARNING: User namespaces may not be available.${NC}"
    echo "  On some systems you may need: sudo sysctl -w kernel.unprivileged_userns_clone=1"
fi

# Install CLI
echo -e "${CYAN}[3/4]${NC} Installing 'docksmith' CLI..."
INSTALL_DIR="$HOME/.local/bin"
mkdir -p "$INSTALL_DIR"

cat > "$INSTALL_DIR/docksmith" << EOF
#!/usr/bin/env python3
import sys, os
sys.path.insert(0, '${SCRIPT_DIR}')
from docksmith.cli import main
if __name__ == '__main__':
    main()
EOF
chmod +x "$INSTALL_DIR/docksmith"

# Also install docksmith-import
cat > "$INSTALL_DIR/docksmith-import" << EOF
#!/usr/bin/env python3
import sys, os
sys.path.insert(0, '${SCRIPT_DIR}')
# Run import script
exec(open('${SCRIPT_DIR}/scripts/import_base_image.py').read())
EOF
chmod +x "$INSTALL_DIR/docksmith-import"

# Check PATH
if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo ""
    echo -e "      ${YELLOW}NOTE: Add $INSTALL_DIR to your PATH:${NC}"
    echo "        echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
    echo "        source ~/.bashrc"
    echo ""
fi
echo -e "      ${GREEN}✓${NC} CLI installed to $INSTALL_DIR/docksmith"

# Import base images
echo -e "${CYAN}[4/4]${NC} Importing base images into ~/.docksmith/..."
python3 scripts/import_base_image.py
echo -e "      ${GREEN}✓${NC} Base images ready"

echo ""
echo -e "${GREEN}${BOLD}  ✓ Setup complete!${NC}"
echo ""
echo -e "  ${BOLD}Quick Start:${NC}"
echo ""
echo -e "  ${CYAN}# Build the sample app${NC}"
echo -e "  docksmith build -t myapp:latest ./sample-app"
echo ""
echo -e "  ${CYAN}# List images${NC}"
echo -e "  docksmith images"
echo ""
echo -e "  ${CYAN}# Run a container${NC}"
echo -e "  docksmith run myapp:latest"
echo ""
echo -e "  ${CYAN}# Run with env override${NC}"
echo -e "  docksmith run -e GREETING=Hi myapp:latest"
echo ""
echo -e "  ${CYAN}# Launch the Web UI${NC}"
echo -e "  python3 ui_server.py"
echo -e "  ${YELLOW}→ Open http://localhost:7474${NC}"
echo ""
