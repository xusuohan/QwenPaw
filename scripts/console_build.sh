#!/usr/bin/env bash
# Build the QwenPaw web console and copy artifacts into the Python package.
# Run from repo root: bash scripts/console_build.sh
#
# Requires: Node.js + npm (see console/package.json).
# After this script, restart QwenPaw: qwenpaw app
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CONSOLE_DIR="$REPO_ROOT/console"
CONSOLE_DEST="$REPO_ROOT/src/qwenpaw/console"

if ! command -v npm >/dev/null 2>&1; then
  echo "[console_build] Error: npm not found. Install Node.js first." >&2
  exit 1
fi

if [[ ! -f "$CONSOLE_DIR/package.json" ]]; then
  echo "[console_build] Error: $CONSOLE_DIR/package.json not found." >&2
  exit 1
fi

echo "[console_build] Installing dependencies (npm ci)..."
(cd "$CONSOLE_DIR" && npm ci)

echo "[console_build] Building frontend (npm run build)..."
(cd "$CONSOLE_DIR" && npm run build)

if [[ ! -f "$CONSOLE_DIR/dist/index.html" ]]; then
  echo "[console_build] Error: build output missing at $CONSOLE_DIR/dist/index.html" >&2
  exit 1
fi

echo "[console_build] Copying console/dist -> src/qwenpaw/console/..."
mkdir -p "$CONSOLE_DEST"
rm -rf "$CONSOLE_DEST"/*
cp -R "$CONSOLE_DIR/dist/." "$CONSOLE_DEST/"

echo "[console_build] Done. Restart QwenPaw to load the web console: qwenpaw app"
