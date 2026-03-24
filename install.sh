#!/usr/bin/env bash
# install.sh — NeverHuman one-command installer
# Usage: bash install.sh
# Supports: macOS 13+ · Apple Silicon (M1/M2/M3/M4)
set -euo pipefail
IFS=$'\n\t'

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GARY_DIR="$REPO_DIR/gary"
NH_HOME="${GARY_HOME:-$HOME/.neverhuman}"
VENV="$GARY_DIR/.venv"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[0;33m'; BLU='\033[0;34m'; BOLD='\033[1m'; RST='\033[0m'

banner() { echo -e "\n${BOLD}${BLU}▸ $1${RST}"; }
ok()     { echo -e "  ${GRN}✓${RST} $1"; }
warn()   { echo -e "  ${YLW}⚠${RST}  $1"; }
die()    { echo -e "\n${RED}✗ ERROR: $1${RST}\n" >&2; exit 1; }

echo -e "\n${BOLD}NeverHuman · GARY Setup${RST}"
echo -e "─────────────────────────────────────────"

# ── 1. Platform check ─────────────────────────────────────────────────────────
banner "Checking platform"
[[ "$(uname -s)" == "Darwin" ]] || die "NeverHuman requires macOS (Apple Silicon)"
ARCH="$(uname -m)"
[[ "$ARCH" == "arm64" ]] || die "NeverHuman requires Apple Silicon (arm64). Got: $ARCH"
MACOS_VER="$(sw_vers -productVersion)"
ok "macOS $MACOS_VER · arm64"

# ── 2. Xcode CLI tools ────────────────────────────────────────────────────────
banner "Checking Xcode CLI tools"
if ! xcode-select -p &>/dev/null; then
    warn "Xcode CLI tools not found — installing (this may open a dialog)..."
    xcode-select --install 2>/dev/null || true
    echo "  Please click 'Install' in the dialog, then re-run this script."
    exit 1
fi
ok "Xcode CLI tools present"

# ── 3. Homebrew ───────────────────────────────────────────────────────────────
banner "Checking Homebrew"
if ! command -v brew &>/dev/null; then
    warn "Homebrew not found — installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
ok "Homebrew $(brew --version | head -1)"

# ── 4. Python 3.11+ ───────────────────────────────────────────────────────────
banner "Checking Python"
PYTHON=""
for py in python3.13 python3.12 python3.11 python3; do
    if command -v "$py" &>/dev/null; then
        VER="$($py -c 'import sys; print(sys.version_info[:2])')"
        if $py -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
            PYTHON="$py"
            ok "Using $py ($($py --version))"
            break
        fi
    fi
done
if [[ -z "$PYTHON" ]]; then
    warn "Python 3.11+ not found — installing via Homebrew..."
    brew install python@3.11
    PYTHON="python3.11"
fi

# ── 5. Virtual environment ────────────────────────────────────────────────────
banner "Setting up Python environment"
if [[ ! -d "$VENV" ]]; then
    $PYTHON -m venv "$VENV"
    ok "Created $VENV"
else
    ok "Virtual environment exists"
fi
source "$VENV/bin/activate"
pip install --upgrade pip --quiet
pip install -e "$REPO_DIR" --quiet
ok "Dependencies installed"

# ── 6. Docker / Postgres ──────────────────────────────────────────────────────
banner "Checking Docker + Postgres"
if ! command -v docker &>/dev/null; then
    die "Docker Desktop is not installed. Download from https://www.docker.com/products/docker-desktop/ and re-run."
fi
if ! docker info &>/dev/null; then
    die "Docker is installed but not running. Start Docker Desktop and re-run."
fi
ok "Docker running"
cd "$GARY_DIR"
docker compose -f docker/compose.yml up -d --quiet-pull 2>/dev/null && ok "Postgres container started"

# ── 7. TLS certificates ───────────────────────────────────────────────────────
banner "TLS certificates"
if [[ ! -f "$REPO_DIR/cert.pem" ]]; then
    if command -v mkcert &>/dev/null; then
        cd "$REPO_DIR" && mkcert -install && mkcert -key-file key.pem -cert-file cert.pem localhost 127.0.0.1 ::1
        ok "TLS cert created via mkcert (browser-trusted)"
    else
        warn "mkcert not found — generating self-signed cert (browser will warn)"
        openssl req -x509 -newkey rsa:4096 -keyout "$REPO_DIR/key.pem" \
            -out "$REPO_DIR/cert.pem" -sha256 -days 3650 -nodes \
            -subj "/CN=localhost" \
            -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" 2>/dev/null
        ok "Self-signed cert created"
    fi
else
    ok "TLS cert already present"
fi

# ── 8. flash-moe inference engine ────────────────────────────────────────────
banner "flash-moe inference engine"
INFER_BIN="${GARY_INFER_BIN:-$NH_HOME/flash-moe/infer}"
mkdir -p "$(dirname "$INFER_BIN")"
if [[ -x "$INFER_BIN" ]]; then
    ok "engine already at $INFER_BIN"
else
    warn "flash-moe binary not found at $INFER_BIN"
    echo ""
    echo -e "  ${BOLD}To build flash-moe:${RST}"
    echo "    git clone https://github.com/neverhuman/mac_flash_moe /tmp/mac_flash_moe"
    echo "    cd /tmp/mac_flash_moe && make"
    echo "    cp build/infer $INFER_BIN"
    echo ""
    echo "  flash-moe is the GPU inference engine for GARY's brain."
    echo "  Without it, the LLM won't start — but you can still download models via the web setup."
    echo ""
    warn "Continuing without flash-moe — you'll need to build it before GARY can talk."
fi

# ── 9. Create ~/.neverhuman ────────────────────────────────────────────────────
banner "Runtime directories"
mkdir -p "$NH_HOME/flash-moe" "$NH_HOME/data"
ok "~/.neverhuman ready"

# ── 10. .env file ─────────────────────────────────────────────────────────────
banner "Environment config"
ENV_FILE="$GARY_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$GARY_DIR/.env.example" "$ENV_FILE"
    ok ".env created from .env.example"
else
    ok ".env already exists"
fi

# ── 11. Launch setup wizard ───────────────────────────────────────────────────
banner "Starting GARY setup wizard"
echo ""
echo -e "  ${BOLD}GARY will now start in setup mode.${RST}"
echo -e "  Your browser will open at ${BOLD}https://localhost:7861/setup${RST}"
echo -e "  Use it to download ASR, TTS, and LLM models."
echo -e "  (You may need to click 'Advanced → Proceed' on the TLS warning)"
echo ""

cd "$GARY_DIR"
GARY_SKIP_AUTOLAUNCH=1 "$VENV/bin/python" server.py &
SERVER_PID=$!

# wait for server
for i in $(seq 1 20); do
    if curl -sk https://localhost:7861/health &>/dev/null; then
        break
    fi
    sleep 0.5
done

# open browser
if command -v open &>/dev/null; then
    open "https://localhost:7861/setup"
fi

echo ""
echo -e "${GRN}${BOLD}✓ GARY setup wizard is running (PID $SERVER_PID)${RST}"
echo -e "  Open ${BOLD}https://localhost:7861/setup${RST} if browser didn't open automatically."
echo -e "  After all models download, click ${BOLD}Launch GARY${RST} in the wizard."
echo -e "  Press Ctrl+C here to stop the setup server."
echo ""

wait $SERVER_PID 2>/dev/null || true
