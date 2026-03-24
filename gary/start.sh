#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# gary/start.sh — One-command launcher for NeverHuman / GARY
#
# Fully autonomous: compiles the inference engine, downloads models, extracts
# and repacks weights — all in one shot. A first-time user can run this cold
# and everything will work.
#
# Flow:
#   1. Dependency checks (Homebrew, Python, ffmpeg, mkcert)
#   2. Compile flash-moe inference engine  (first run ~60s, skipped after)
#   3. Download AI models via HuggingFace CLI  (skipped if already present)
#   4. Extract non-expert weights → flash-moe/out_35b/  (first run ~30s)
#   5. Repack expert weights into model dir  (first run ~5-10 min, ~17 GB)
#   6. Set up Python venv + install dependencies
#   7. Generate TLS cert  (skipped if unsafe mode)
#   8. Start Postgres (Docker/Podman, optional)
#   9. Launch server.py
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/.." && pwd)"
FLASH_MOE_DIR="$REPO_ROOT/flash-moe"
VENV="$DIR/.venv"
CERT="$DIR/cert.pem"
KEY="$DIR/key.pem"
PORT="${GARY_PORT:-7861}"

UNSAFE=0
REBUILD=0
while [[ "$#" -gt 0 ]]; do
  case $1 in
    -unsafe|--unsafe) UNSAFE=1; shift ;;
    -rebuild|--rebuild) REBUILD=1; shift ;;
    -port|--port) PORT="$2"; shift 2 ;;
    -voice|--voice) GARY_TTS_VOICE="$2"; shift 2 ;;
    *) echo "Unknown parameter passed: $1"; exit 1 ;;
  esac
done

# Privileged port check (<1024 requires root or Podman rootful mode)
if [ "$PORT" -lt 1024 ] 2>/dev/null; then
  echo -e "\033[1;33m⚠  Port ${PORT} is a privileged port (< 1024).\033[0m"
  echo ""
  echo -e "  Privileged ports require elevated permissions. Options:"
  echo -e ""
  echo -e "  \033[0;36m  Option 1 — Run with sudo (not recommended):${RST}"
  echo -e "  \033[0;36m    sudo ./start.sh -port $PORT\033[0m"
  echo -e ""
  echo -e "  \033[0;36m  Option 2 — If using Podman, enable rootful mode:\033[0m"
  echo -e "  \033[0;36m    podman machine set --rootful\033[0m"
  echo -e "  \033[0;36m    podman machine stop && podman machine start\033[0m"
  echo -e ""
  echo -e "  \033[0;36m  Option 3 — Use a non-privileged port (recommended):\033[0m"
  echo -e "  \033[0;36m    ./start.sh -port 7861\033[0m"
  echo ""
  echo -e "\033[0;31m✗ Cannot bind to port ${PORT} without elevated permissions.\033[0m"
  exit 1
fi

GRN="\033[0;32m"; CYN="\033[0;36m"; YLW="\033[1;33m"; RED="\033[0;31m"
PNK="\033[1;35m"; RST="\033[0m"; BLD="\033[1m"
log()  { echo -e "${CYN}▶ $*${RST}"; }
ok()   { echo -e "${GRN}✓ $*${RST}"; }
warn() { echo -e "${YLW}⚠  $*${RST}"; }
err()  { echo -e "${RED}✗ $*${RST}"; exit 1; }
step() { echo -e "\n${BLD}${CYN}━━━ $* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"; }

echo ""
echo -e "${BLD}${CYN}  ███╗   ██╗███████╗██╗   ██╗███████╗██████╗ ${RST}"
echo -e "${BLD}${CYN}  ████╗  ██║██╔════╝██║   ██║██╔════╝██╔══██╗${RST}"
echo -e "${BLD}${CYN}  ██╔██╗ ██║█████╗  ██║   ██║█████╗  ██████╔╝${RST}"
echo -e "${BLD}${CYN}  ██║╚██╗██║██╔══╝  ╚██╗ ██╔╝██╔══╝  ██╔══██╗${RST}"
echo -e "${BLD}${CYN}  ██║ ╚████║███████╗ ╚████╔╝ ███████╗██║  ██║${RST}"
echo -e "${BLD}${CYN}  ╚═╝  ╚═══╝╚══════╝  ╚═══╝  ╚══════╝╚═╝  ╚═╝${RST}"
echo -e "  ${BLD}NeverHuman — Sovereign AI Entity${RST}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Helper: detect the HuggingFace snapshot path for a given repo id
# ─────────────────────────────────────────────────────────────────────────────
detect_hf_model() {
  local repo="$1"
  local cache_dir="$HOME/.cache/huggingface/hub/models--${repo//\//-/}"
  # try the standard double-dash format
  local cache_dir2="$HOME/.cache/huggingface/hub/models--${repo/\//-}"
  # proper format: org--name  (slash → double-dash)
  local a="${repo%%/*}"
  local b="${repo##*/}"
  local cache_dir3="$HOME/.cache/huggingface/hub/models--${a}--${b}"
  for d in "$cache_dir3" "$cache_dir2" "$cache_dir"; do
    if [ -d "$d/snapshots" ]; then
      local snap
      snap="$(ls -td "$d/snapshots"/*/  2>/dev/null | head -1)"
      snap="${snap%/}"  # strip trailing slash
      if [ -d "$snap" ]; then echo "$snap"; return 0; fi
    fi
  done
  return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Dependency checks
# ─────────────────────────────────────────────────────────────────────────────
step "Pre-flight dependency checks"

MISSING_DEPS=()
SUGGESTIONS=()

dep_ok()   { echo -e "${GRN}[✓] $*${RST}"; }
dep_fail() { echo -e "${RED}[✗] $*${RST}"; }

# ── Homebrew ──────────────────────────────────────────────────────────────────
if [[ "$OSTYPE" == "darwin"* ]]; then
  if command -v brew &>/dev/null; then
    dep_ok "Homebrew $(brew --version 2>/dev/null | head -1 | awk '{print $2}')"
  else
    dep_fail "Homebrew — not installed (required for all other dependencies)"
    echo ""
    echo -e "${BLD}  Install Homebrew, then re-run start.sh:${RST}"
    echo -e "${CYN}    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"${RST}"
    echo ""
    err "Homebrew is required. Install it using the command above and re-run."
  fi
fi

# ── git ───────────────────────────────────────────────────────────────────────
if command -v git &>/dev/null; then
  dep_ok "git $(git --version | awk '{print $3}')"
else
  dep_fail "git — not installed"
  MISSING_DEPS+=("git")
  SUGGESTIONS+=("    brew install git")
fi

# ── Python 3.9+ ───────────────────────────────────────────────────────────────
if command -v python3 &>/dev/null; then
  PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  PY_MAJ=$(echo "$PY_VER" | cut -d. -f1)
  PY_MIN=$(echo "$PY_VER" | cut -d. -f2)
  if [ "$PY_MAJ" -lt 3 ] || { [ "$PY_MAJ" -eq 3 ] && [ "$PY_MIN" -lt 9 ]; }; then
    dep_fail "Python ${PY_VER} — need 3.9 or newer"
    MISSING_DEPS+=("Python 3.9+")
    SUGGESTIONS+=("    brew install python3   # installs latest Python 3")
  else
    dep_ok "Python ${PY_VER}"
  fi
else
  dep_fail "python3 — not installed"
  MISSING_DEPS+=("Python 3")
  SUGGESTIONS+=("    brew install python3")
fi

# ── ffmpeg ────────────────────────────────────────────────────────────────────
if command -v ffmpeg &>/dev/null; then
  FFMPEG_VER=$(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')
  dep_ok "ffmpeg ${FFMPEG_VER}"
else
  dep_fail "ffmpeg — not installed"
  MISSING_DEPS+=("ffmpeg")
  SUGGESTIONS+=("    brew install ffmpeg")
fi

# ── Xcode Command Line Tools (clang + Metal SDK) ──────────────────────────────
if xcode-select -p &>/dev/null 2>&1 && [ -d "$(xcode-select -p 2>/dev/null)" ]; then
  if command -v clang &>/dev/null; then
    XCODE_PATH="$(xcode-select -p)"
    CLANG_VER="$(clang --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
    dep_ok "Xcode CLI Tools — clang ${CLANG_VER} (${XCODE_PATH})"
  else
    dep_fail "clang not found (Xcode path exists but clang is missing)"
    MISSING_DEPS+=("clang")
    SUGGESTIONS+=("    sudo xcode-select --reset")
    SUGGESTIONS+=("    xcode-select --install")
  fi
else
  dep_fail "Xcode Command Line Tools — not installed (required to compile inference engine)"
  MISSING_DEPS+=("Xcode CLI Tools")
  SUGGESTIONS+=("    xcode-select --install")
  SUGGESTIONS+=("    # A GUI installer appears. After it completes, re-run: ./start.sh -unsafe")
fi

# ── make (part of Xcode CLI Tools) ───────────────────────────────────────────
if command -v make &>/dev/null; then
  dep_ok "make $(make --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+')"
else
  dep_fail "make — not found (should come with Xcode CLI Tools)"
  MISSING_DEPS+=("make")
  SUGGESTIONS+=("    xcode-select --install   # provides make, clang, and Metal SDK")
fi

# ── mkcert (trusted TLS — skipped in -unsafe mode) ───────────────────────────
if [ "$UNSAFE" -eq 0 ]; then
  if command -v mkcert &>/dev/null; then
    dep_ok "mkcert"
  else
    dep_fail "mkcert — not installed (required for trusted local HTTPS)"
    MISSING_DEPS+=("mkcert")
    SUGGESTIONS+=("    brew install mkcert")
    SUGGESTIONS+=("    # Or skip TLS entirely: ./start.sh -unsafe")
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
if [ ${#MISSING_DEPS[@]} -ne 0 ]; then
  echo ""
  echo -e "${RED}${BLD}  ✗ Missing: ${MISSING_DEPS[*]}${RST}"
  echo ""
  echo -e "${BLD}  Fix with:${RST}"
  for s in "${SUGGESTIONS[@]}"; do
    if [ -z "$s" ]; then echo ""; else echo -e "${CYN}$s${RST}"; fi
  done
  echo ""
  if [[ " ${MISSING_DEPS[*]} " == *" mkcert "* ]] && [ ${#MISSING_DEPS[@]} -eq 1 ]; then
    echo -e "${PNK}  💡 mkcert is the ONLY missing dep — shortcut: ./start.sh -unsafe${RST}"
    echo ""
  fi
  err "Install the above and re-run ./start.sh"
fi


# RAM check
if command -v vm_stat &>/dev/null; then
  FREE_PAGES=$(vm_stat | awk '/Pages free/ { gsub(/\./, "", $3); print $3 }')
  INACT_PAGES=$(vm_stat | awk '/Pages inactive/ { gsub(/\./, "", $3); print $3 }')
  FREE_PAGES=${FREE_PAGES:-0}; INACT_PAGES=${INACT_PAGES:-0}
  AVAIL_GB=$(( (FREE_PAGES + INACT_PAGES) * 16384 / 1073741824 ))
  if [ "$AVAIL_GB" -lt 3 ]; then
    warn "Only ~${AVAIL_GB}GB RAM available. GARY+LLM need ≈6GB. Quit heavy apps first."
  else
    ok "RAM OK: ~${AVAIL_GB}GB available"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Compile flash-moe inference engine (first run only)
# ─────────────────────────────────────────────────────────────────────────────
step "Inference engine (flash-moe)"

INFER_BIN="$FLASH_MOE_DIR/infer"

_needs_build=0
if [ "$REBUILD" -eq 1 ]; then
  _needs_build=1
  warn "Force-rebuild requested (-rebuild flag)"
elif [ ! -f "$INFER_BIN" ]; then
  _needs_build=1
  warn "flash-moe binary not found — will compile from source"
elif [ ! -s "$INFER_BIN" ]; then
  _needs_build=1
  warn "flash-moe binary is empty (damaged) — recompiling"
elif [ ! -x "$INFER_BIN" ]; then
  _needs_build=1
  warn "flash-moe binary not executable — recompiling"
fi

if [ "$_needs_build" -eq 1 ]; then
  # Verify build tools before attempting compile
  if ! xcode-select -p &>/dev/null 2>&1; then
    echo ""
    echo -e "${RED}  ✗ Xcode Command Line Tools are required to compile the inference engine.${RST}"
    echo -e "  Run this command, then re-run start.sh:"
    echo -e "  ${CYN}  xcode-select --install${RST}"
    echo ""
    err "Missing Xcode CLI Tools."
  fi
  if ! command -v clang &>/dev/null; then
    err "clang not found. Run: xcode-select --install"
  fi

  log "Compiling flash-moe Metal inference engine…"
  echo -e "  ${YLW}One-time step — takes roughly 30–90 seconds on Apple Silicon.${RST}"
  COMPILE_START=$SECONDS
  echo ""
  if ! (cd "$FLASH_MOE_DIR" && make infer 2>&1); then
    echo ""
    COMPILE_ELAPSED=$(( SECONDS - COMPILE_START ))
    echo -e "${RED}  ✗ Compilation failed after ${COMPILE_ELAPSED}s.${RST}"
    echo -e "  Common fixes:"
    echo -e "  ${CYN}  xcode-select --install            # Missing Xcode CLI Tools${RST}"
    echo -e "  ${CYN}  sudo xcode-select --reset          # Broken toolchain path${RST}"
    echo -e "  ${CYN}  sudo xcodebuild -license accept    # Unconsented license${RST}"
    echo ""
    err "flash-moe compilation failed. See errors above."
  fi
  chmod +x "$INFER_BIN"
  COMPILE_ELAPSED=$(( SECONDS - COMPILE_START ))
  echo ""
  ok "flash-moe compiled in ${COMPILE_ELAPSED}s ✓ ($(du -sh "$INFER_BIN" | cut -f1))"
else
  ok "flash-moe binary ready ✓ ($(du -sh "$INFER_BIN" 2>/dev/null | cut -f1 || echo '?'))"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Download AI models
# ─────────────────────────────────────────────────────────────────────────────
step "AI model downloads"

# Ensure huggingface-cli is available via the venv we'll build in step 6
# For now, check if it's on PATH
if ! command -v huggingface-cli &>/dev/null && ! python3 -c 'import huggingface_hub' 2>/dev/null; then
  log "Installing huggingface_hub (needed for model downloads)…"
  python3 -m pip install --quiet huggingface_hub[cli] 2>/dev/null || true
fi

download_model() {
  local repo="$1"
  local name="$2"
  if detect_hf_model "$repo" &>/dev/null; then
    echo -e "${GRN}[✓] $name already downloaded${RST}"
  else
    log "Downloading $name ($repo)…"
    echo -e "  ${YLW}This may take a while — models are large. Progress shown below.${RST}"
    if command -v huggingface-cli &>/dev/null; then
      huggingface-cli download "$repo"
    else
      python3 -c "from huggingface_hub import snapshot_download; snapshot_download('$repo')"
    fi
    ok "$name downloaded ✓"
  fi
}

download_model "Qwen/Qwen3-ASR-0.6B"             "ASR model (Qwen3-ASR-0.6B)"
download_model "hexgrad/Kokoro-82M"               "TTS model (Kokoro-82M)"
download_model "mlx-community/Qwen3.5-35B-A3B-4bit" "LLM (Qwen3.5-35B, ~18 GB)"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Extract non-expert weights (one-time preprocessing)
# ─────────────────────────────────────────────────────────────────────────────
step "Non-expert weight extraction"

WEIGHTS_BIN="$FLASH_MOE_DIR/out_35b/model_weights.bin"
WEIGHTS_JSON="$FLASH_MOE_DIR/out_35b/model_weights.json"

if [ -f "$WEIGHTS_BIN" ] && [ -s "$WEIGHTS_BIN" ] && [ -f "$WEIGHTS_JSON" ]; then
  ok "Non-expert weights already extracted ✓ ($(du -sh "$WEIGHTS_BIN" | cut -f1))"
else
  LLM_MODEL_PATH="$(detect_hf_model "mlx-community/Qwen3.5-35B-A3B-4bit" 2>/dev/null || echo "")"
  if [ -z "$LLM_MODEL_PATH" ]; then
    err "LLM model not found after download step — something went wrong."
  fi
  log "Extracting non-expert weights from HuggingFace safetensors → flash-moe/out_35b/"
  echo -e "  ${YLW}One-time step (~30 seconds). Reads model shards, writes ~1.3 GB.${RST}"
  mkdir -p "$FLASH_MOE_DIR/out_35b"
  python3 "$FLASH_MOE_DIR/extract_weights_35b.py" \
    --model "$LLM_MODEL_PATH" \
    --output "$FLASH_MOE_DIR/out_35b"
  ok "Non-expert weights extracted ✓"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Repack expert weights (one-time, ~5–15 minutes, ~17 GB written)
# ─────────────────────────────────────────────────────────────────────────────
step "Expert weight repacking"

LLM_MODEL_PATH="$(detect_hf_model "mlx-community/Qwen3.5-35B-A3B-4bit" 2>/dev/null || echo "")"
PACKED_CHECK="$LLM_MODEL_PATH/packed_experts/layer_39.bin"   # last layer = all done

if [ -f "$PACKED_CHECK" ] && [ -s "$PACKED_CHECK" ]; then
  ok "Expert weights already repacked ✓"
else
  log "Repacking expert weights into contiguous per-layer binary files…"
  echo -e "  ${YLW}One-time step. Writes ~17 GB of packed expert data to:${RST}"
  echo -e "  ${YLW}  $LLM_MODEL_PATH/packed_experts/${RST}"
  echo -e "  ${YLW}ETA: 5–15 minutes depending on SSD speed. Do NOT interrupt.${RST}"
  echo ""

  # Build a fresh expert index pointing at the user's actual model path
  EXPERT_INDEX="$FLASH_MOE_DIR/expert_index_35b.json"
  # Regenerate the index so model_path is correct for this machine
  python3 "$FLASH_MOE_DIR/build_expert_index_35b.py" \
    --model-path "$LLM_MODEL_PATH" \
    --out "$EXPERT_INDEX"

  python3 "$FLASH_MOE_DIR/repack_experts_35b.py" \
    --index "$EXPERT_INDEX"
  ok "Expert weights repacked ✓"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Python venv + dependencies
# ─────────────────────────────────────────────────────────────────────────────
step "Python environment"

# Stop any existing processes first
"$DIR/stop.sh" 2>/dev/null || true

if [ -d "$VENV" ] && { [ ! -f "$VENV/bin/python3" ] || [ ! -f "$VENV/bin/pip" ]; }; then
  warn "Corrupted virtual environment — rebuilding..."
  rm -rf "$VENV"
fi

if [ ! -d "$VENV" ]; then
  log "Creating isolated Python environment…"
  python3 -m venv "$VENV" >/dev/null 2>&1 || err "Failed to create venv. Is your Python installation healthy?"
  ok "Venv created at gary/.venv/"
else
  ok "Reusing gary/.venv/"
fi
source "$VENV/bin/activate"

PIP_ERR=$(mktemp)
if ! "$VENV/bin/python3" -m pip install --quiet --upgrade pip 2>"$PIP_ERR"; then
  cat "$PIP_ERR"; rm -f "$PIP_ERR"
  err "Failed to upgrade pip."
fi

if ! "$VENV/bin/python3" -m pip install --quiet -r "$DIR/requirements.txt" 2>"$PIP_ERR"; then
  cat "$PIP_ERR"; rm -f "$PIP_ERR"
  err "Failed to install Python packages."
fi
rm -f "$PIP_ERR"
ok "Python dependencies ready"

# Verify critical imports
IMPORT_FAILURES=()
check_import() {
  local mod=$1 label=$2 fix=$3
  if "$VENV/bin/python3" -c "import $mod" 2>/dev/null; then
    echo -e "${GRN}[✓] $label${RST}"
  else
    echo -e "${RED}[✗] $label — import $mod failed${RST}"
    IMPORT_FAILURES+=("  ${YLW}$fix${RST}")
  fi
}
check_import "fastapi"     "FastAPI"       "$VENV/bin/pip install 'fastapi>=0.111.0'"
check_import "uvicorn"     "Uvicorn"       "$VENV/bin/pip install 'uvicorn[standard]>=0.29.0'"
check_import "numpy"       "NumPy"         "$VENV/bin/pip install 'numpy>=1.26.0'"
check_import "httpx"       "HTTPX"         "$VENV/bin/pip install 'httpx>=0.27.0'"
check_import "kokoro_onnx" "Kokoro TTS"    "$VENV/bin/pip install 'kokoro-onnx>=0.4.0'"
check_import "soundfile"   "SoundFile"     "$VENV/bin/pip install 'soundfile>=0.12.1'"
if [ ${#IMPORT_FAILURES[@]} -ne 0 ]; then
  echo ""
  warn "Import failures:"
  for f in "${IMPORT_FAILURES[@]}"; do echo -e "$f"; done
  err "Cannot start GARY with broken Python dependencies."
fi
ok "All Python imports verified"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — TLS certificate
# ─────────────────────────────────────────────────────────────────────────────
if [ "$UNSAFE" -eq 1 ]; then
  warn "Running in UNSAFE mode (HTTP). Microphone access may be blocked on LAN."
  CERT=""; KEY=""
elif [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  step "TLS certificate"
  if command -v mkcert &>/dev/null; then
    log "Generating browser-trusted TLS certificate…"
    mkcert -install 2>/dev/null || true
    mkcert -cert-file "$CERT" -key-file "$KEY" localhost 127.0.0.1 0.0.0.0 ::1 2>/dev/null || true
    ok "TLS cert generated via mkcert"
  else
    "$VENV/bin/python3" - <<'PYEOF'
import ssl, datetime, ipaddress, os, pathlib
try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False).sign(key, hashes.SHA256()))
    d = pathlib.Path(os.environ["DIR"])
    open(d/"cert.pem","wb").write(cert.public_bytes(serialization.Encoding.PEM))
    open(d/"key.pem","wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    print("OK")
except ImportError:
    import subprocess
    subprocess.run(["openssl","req","-x509","-newkey","rsa:2048","-keyout",os.environ["KEY"],"-out",os.environ["CERT"],"-days","825","-nodes","-subj","/CN=localhost"],check=True,capture_output=True)
    print("OK via openssl")
PYEOF
    ok "TLS cert generated"
  fi
else
  ok "TLS cert found"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 8 — Memory Spine (Postgres)
# Diagnose Docker/Podman state precisely so the user knows exactly what to do.
# ─────────────────────────────────────────────────────────────────────────────
step "Memory Spine (Postgres)"

DC_CMD=""

_docker_installed=0
_docker_running=0
_podman_installed=0
_podman_running=0
_compose_ok=0

# ── Docker ────────────────────────────────────────────────────────────────────
if command -v docker &>/dev/null; then
  _docker_installed=1
  if docker info >/dev/null 2>&1; then
    _docker_running=1
    dep_ok "Docker — running ✓"
    # Check for compose plugin (v2: 'docker compose') vs legacy standalone
    if docker compose version &>/dev/null 2>&1; then
      DC_CMD="docker compose"
      dep_ok "Docker Compose plugin ✓"
    elif command -v docker-compose &>/dev/null; then
      DC_CMD="docker-compose"
      dep_ok "docker-compose (legacy) ✓"
    else
      dep_fail "Docker Compose not found (Docker is running but compose is missing)"
      echo -e "  ${CYN}Fix: brew install docker-compose${RST}"
      echo -e "  ${CYN}  or: Install Docker Desktop (includes Compose): https://www.docker.com/products/docker-desktop${RST}"
    fi
  else
    dep_fail "Docker — installed but daemon is NOT running"
    echo ""
    echo -e "  ${BLD}The Docker daemon is not running. Start it one of these ways:${RST}"
    echo -e ""
    if [ -d "/Applications/Docker.app" ]; then
      echo -e "  ${CYN}  open -a Docker                    # start Docker Desktop${RST}"
      echo -e "  ${CYN}  # Then re-run: ./start.sh -unsafe${RST}"
    else
      echo -e "  ${CYN}  open -a Docker                    # if Docker Desktop is installed${RST}"
      echo -e "  ${CYN}  brew services start docker        # if using Colima / homebrew docker${RST}"
    fi
    echo ""
    warn "Memory Spine (Postgres) will be offline until Docker is started."
    echo -e "  ${YLW}Re-run ./start.sh after starting Docker to enable persistent memory.${RST}"
  fi
else
  # ── Podman (alternative) ───────────────────────────────────────────────────
  if command -v podman &>/dev/null; then
    _podman_installed=1
    if podman info >/dev/null 2>&1; then
      _podman_running=1
      dep_ok "Podman — running ✓"
      if podman compose version &>/dev/null 2>&1 || command -v podman-compose &>/dev/null; then
        DC_CMD="podman compose"
        dep_ok "podman-compose ✓"
      fi
    else
      dep_fail "Podman — installed but not running"
      echo -e "  ${CYN}Fix: podman machine start${RST}"
    fi
  fi

  if [ -z "$DC_CMD" ] && [ $_podman_installed -eq 0 ]; then
    dep_fail "No container runtime found (Docker or Podman required for Memory Spine)"
    echo ""
    echo -e "  ${BLD}Memory Spine stores GARY's long-term memory. To enable it, install Docker:${RST}"
    echo ""
    echo -e "  ${CYN}  Option 1 — Docker Desktop (easiest):${RST}"
    echo -e "  ${CYN}    https://www.docker.com/products/docker-desktop${RST}"
    echo -e "  ${CYN}    # Free for personal use. After install, open Docker Desktop and re-run start.sh${RST}"
    echo ""
    echo -e "  ${CYN}  Option 2 — Homebrew (CLI only):${RST}"
    echo -e "  ${CYN}    brew install docker docker-compose colima${RST}"
    echo -e "  ${CYN}    colima start${RST}"
    echo -e "  ${CYN}    # Then re-run: ./start.sh -unsafe${RST}"
    echo ""
    warn "GARY will start WITHOUT Memory Spine. Long-term memory will be disabled."
    echo -e "  ${YLW}Install Docker and re-run start.sh to enable persistent memory.${RST}"
  fi
fi

# ── Start Postgres if runtime is ready ───────────────────────────────────────
if [ -n "$DC_CMD" ]; then
  if [ -f "$DIR/docker/compose.yml" ]; then
    log "Starting Memory Spine (Postgres)…"
    if $DC_CMD -f "$DIR/docker/compose.yml" up -d postgres >/dev/null 2>&1; then
      ok "Memory Spine (Postgres) up ✓"
    else
      warn "Postgres failed to start — check: $DC_CMD -f docker/compose.yml logs postgres"
    fi
  else
    warn "docker/compose.yml not found — Memory Spine skipped"
  fi
fi


# ─────────────────────────────────────────────────────────────────────────────
# STEP 9 — Launch
# ─────────────────────────────────────────────────────────────────────────────
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "YOUR_LAN_IP")

echo ""
echo -e "${BLD}${GRN}  GARY starting on port ${PORT}${RST}"
if [ "$UNSAFE" -eq 1 ]; then
  echo -e "  Local:  ${CYN}http://localhost:${PORT}${RST}"
  echo -e "  LAN:    ${CYN}http://${LAN_IP}:${PORT}${RST}"
else
  echo -e "  Local:  ${CYN}https://localhost:${PORT}${RST}"
  echo -e "  LAN:    ${CYN}https://${LAN_IP}:${PORT}${RST}"
fi
echo -e "  LLM:    ${YLW}http://localhost:8088${RST} (flash-moe, auto-managed)"
echo ""
if [ "$UNSAFE" -eq 0 ]; then
  echo -e "${YLW}  Accept the self-signed cert warning once in your browser.${RST}"
fi
echo -e "  Press ${BLD}Ctrl+C${RST} to stop."
echo ""

export GARY_PORT="$PORT"
if [ -n "${CERT:-}" ]; then
  export SSL_CERTFILE="$CERT"
  export SSL_KEYFILE="$KEY"
else
  unset SSL_CERTFILE 2>/dev/null || true
  unset SSL_KEYFILE 2>/dev/null || true
fi
export PYTHONPATH="$DIR"
export GARY_CONTEXT_PACK=1
export GARY_TTS_VOICE="${GARY_TTS_VOICE:-am_adam}"

exec "$VENV/bin/python3" "$DIR/server.py"
