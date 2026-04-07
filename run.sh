#!/usr/bin/env bash
# =============================================================================
# run.sh – One-command launcher for Provenance Archive Wiki
#
# What this script does:
#   1. Creates a Python virtual environment (first run only)
#   2. Installs all Python dependencies
#   3. Installs Node.js dependencies and builds the React frontend
#   4. Starts the Flask web server on http://localhost:5000
#   5. Starts the MCP server on port 5001 (stdio)
#
# Usage:
#   chmod +x run.sh
#   ./run.sh
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()   { echo -e "${CYAN}▶ $*${NC}"; }
ok()    { echo -e "${GREEN}✓ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $*${NC}"; }
error() { echo -e "${RED}✗ $*${NC}"; exit 1; }

# ── Check .env ────────────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    warn ".env file not found."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        warn "Created .env from .env.example — please edit it and add your ANTHROPIC_API_KEY."
        warn "Then run this script again."
        exit 1
    else
        error ".env.example not found. Please create .env with ANTHROPIC_API_KEY=your_key"
    fi
fi

if grep -q "YOUR_KEY_HERE" .env; then
    warn "ANTHROPIC_API_KEY not set in .env — image ingestion will not work."
    warn "Edit .env and replace YOUR_KEY_HERE with your actual API key."
fi

# ── Python virtual environment ────────────────────────────────────────────────
VENV_DIR="$ROOT/.venv"
if [ ! -d "$VENV_DIR" ]; then
    log "Creating Python virtual environment…"
    python3 -m venv "$VENV_DIR"
    ok "Virtual environment created at .venv/"
fi

# Activate venv
source "$VENV_DIR/bin/activate"

log "Installing / updating Python dependencies…"
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt
ok "Python dependencies ready"

# ── Node / React frontend ─────────────────────────────────────────────────────
if ! command -v node &>/dev/null; then
    warn "Node.js not found. Skipping frontend build."
    warn "Install Node.js from https://nodejs.org and run this script again to build the UI."
else
    log "Installing Node dependencies…"
    cd frontend
    npm install --silent
    log "Building React frontend…"
    npm run build
    cd "$ROOT"
    ok "Frontend built to dist/"
fi

# ── Create photos directory if missing ───────────────────────────────────────
mkdir -p "$ROOT/photos" "$ROOT/data"

# ── Start servers ─────────────────────────────────────────────────────────────

# Trap Ctrl+C to kill both servers cleanly
cleanup() {
    echo ""
    log "Shutting down servers…"
    kill "$FLASK_PID" 2>/dev/null || true
    kill "$MCP_PID"   2>/dev/null || true
    wait
    ok "Done. Goodbye."
    exit 0
}
trap cleanup INT TERM

# Start MCP server in background (writes to mcp.log)
log "Starting MCP server (port 5001, stdio mode)…"
PYTHONPATH="$ROOT/backend" python "$ROOT/backend/mcp_server.py" \
    > "$ROOT/data/mcp.log" 2>&1 &
MCP_PID=$!
ok "MCP server started (PID $MCP_PID)"

# Small pause to let MCP initialise
sleep 1

# Start Flask in foreground
log "Starting Flask server on http://localhost:5100 …"
echo ""
echo "  ┌─────────────────────────────────────────────────────────┐"
echo "  │                                                         │"
echo "  │   Provenance Archive Wiki                               │"
echo "  │   http://localhost:5100                                 │"
echo "  │                                                         │"
echo "  │   Press Ctrl+C to stop                                  │"
echo "  │                                                         │"
echo "  └─────────────────────────────────────────────────────────┘"
echo ""

PYTHONPATH="$ROOT/backend" python "$ROOT/backend/app.py" &
FLASK_PID=$!

wait "$FLASK_PID"
