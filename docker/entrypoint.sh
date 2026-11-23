#!/bin/sh
set -eu

DATA_DIR="${HUANGGUO_DATA_DIR:-/data}"
DOWNLOAD_DIR="${HUANGGUO_DOWNLOAD_ROOT:-/downloads}"
PORT="${PORT:-8765}"

mkdir -p "$DATA_DIR" "$DOWNLOAD_DIR"

echo "[huangguo] data=${DATA_DIR}"
echo "[huangguo] downloads=${DOWNLOAD_DIR}"
echo "[huangguo] listening on 0.0.0.0:${PORT}"

exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --proxy-headers \
  --forwarded-allow-ips="*"
