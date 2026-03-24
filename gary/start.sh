#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# gary/start.sh — One-command launcher for the NeverHuman / GARY voice agent
#
# Daily driver: run this after install.sh has set everything up.
# The LLM watchdog (core/llm_watchdog.py) manages the flash-moe infer
# binary automatically — this script only needs to start server.py.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
CERT="$DIR/cert.pem"
KEY="$DIR/key.pem"
PORT="${GARY_PORT:-7861}"

GRN="\033[0;32m"; CYN="\033[0;36m"; YLW="\033[1;33m"; RED="\033[0;31m"; RST="\033[0m"; BLD="\033[1m"
log()  { echo -e "${CYN}▶ $*${RST}"; }
ok()   { echo -e "${GRN}✓ $*${RST}"; }
warn() { echo -e "${YLW}⚠ $*${RST}"; }
err()  { echo -e "${RED}✗ $*${RST}"; exit 1; }

echo ""
echo -e "${BLD}${CYN}  ███╗   ██╗███████╗██╗   ██╗███████╗██████╗ ${RST}"
echo -e "${BLD}${CYN}  ████╗  ██║██╔════╝██║   ██║██╔════╝██╔══██╗${RST}"
echo -e "${BLD}${CYN}  ██╔██╗ ██║█████╗  ██║   ██║█████╗  ██████╔╝${RST}"
echo -e "${BLD}${CYN}  ██║╚██╗██║██╔══╝  ╚██╗ ██╔╝██╔══╝  ██╔══██╗${RST}"
echo -e "${BLD}${CYN}  ██║ ╚████║███████╗ ╚████╔╝ ███████╗██║  ██║${RST}"
echo -e "${BLD}${CYN}  ╚═╝  ╚═══╝╚══════╝  ╚═══╝  ╚══════╝╚═╝  ╚═╝${RST}"
echo -e "  ${BLD}NeverHuman — Voice AI Agent${RST}"
echo ""

# ── Stop any existing processes ───────────────────────────────────────────────
"$DIR/stop.sh"

# ── RAM pre-flight check ──────────────────────────────────────────────────────
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

# ── Python venv ───────────────────────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
  log "Creating Python virtual environment…"
  python3 -m venv "$VENV"
  ok "venv created at gary/.venv/"
else
  ok "Reusing gary/.venv/"
fi
source "$VENV/bin/activate"

# ── Dependencies ──────────────────────────────────────────────────────────────
log "Verifying dependencies…"
pip install --quiet --upgrade pip
pip install --quiet -r "$DIR/requirements.txt"
ok "Dependencies ready"

# ── TLS cert (mkcert → Python cryptography → openssl) ────────────────────────
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  log "Generating TLS certificate…"
  if command -v mkcert &>/dev/null; then
    mkcert -cert-file "$CERT" -key-file "$KEY" localhost 127.0.0.1 2>/dev/null || true
    ok "TLS cert via mkcert"
  else
    CERT="$CERT" KEY="$KEY" DIR="$DIR" python3 - <<'PYEOF'
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

# ── Memory Spine (Postgres) ───────────────────────────────────────────────────
DC_CMD=""
if docker info >/dev/null 2>&1; then DC_CMD="docker compose"
elif podman info >/dev/null 2>&1; then DC_CMD="podman compose"; fi

if [ -n "$DC_CMD" ]; then
  if [ -f "$DIR/docker/compose.yml" ]; then
    log "Starting Memory Spine (Postgres)…"
    $DC_CMD -f "$DIR/docker/compose.yml" up -d postgres >/dev/null 2>&1 || true
    ok "Memory Spine up"
  fi
else
  warn "No container runtime found. Memory Spine (Postgres) will be offline."
fi

# ── LAN IP ───────────────────────────────────────────────────────────────────
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "YOUR_LAN_IP")

echo ""
echo -e "${BLD}${GRN}  GARY starting on port ${PORT}${RST}"
echo -e "  Local:  ${CYN}https://localhost:${PORT}${RST}"
echo -e "  LAN:    ${CYN}https://${LAN_IP}:${PORT}${RST}"
echo -e "  LLM:    ${YLW}http://localhost:8088${RST} (flash-moe, auto-managed)"
echo ""
echo -e "${YLW}  Accept the self-signed cert warning once in your browser.${RST}"
echo -e "  Press ${BLD}Ctrl+C${RST} to stop."
echo ""

export GARY_PORT="$PORT"
export SSL_CERTFILE="$CERT"
export SSL_KEYFILE="$KEY"
export PYTHONPATH="$DIR"

exec python3 "$DIR/server.py"
