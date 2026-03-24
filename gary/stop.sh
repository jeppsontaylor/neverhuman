#!/usr/bin/env bash
# gary/stop.sh — Shutdown script for NeverHuman / GARY

echo "══════════════════════════════════════"
echo "  Stopping GARY (NeverHuman)"
echo "══════════════════════════════════════"

cd "$(dirname "$0")"

echo "◀ Stopping GARY server (port 7861)…"
lsof -ti :7861 | xargs kill -9 2>/dev/null || true
pkill -9 -f "gary/server.py" 2>/dev/null || true
pkill -9 -f "server.py" 2>/dev/null || true

echo "◀ Stopping flash-moe inference (port 8088)…"
lsof -ti :8088 | xargs kill -9 2>/dev/null || true
pkill -9 -f "metal_infer/infer" 2>/dev/null || true
pkill -9 -f "flash-moe/infer" 2>/dev/null || true

DC_CMD=""
if docker info >/dev/null 2>&1; then DC_CMD="docker compose"
elif podman info >/dev/null 2>&1; then DC_CMD="podman compose"; fi

if [ -n "$DC_CMD" ] && [ -f "docker/compose.yml" ]; then
  echo "◀ Stopping Memory Spine (Postgres)…"
  $DC_CMD -f docker/compose.yml stop postgres 2>/dev/null || true
fi

echo "✓ GARY stopped."
