#!/usr/bin/env bash
# Build a full wheel package including the latest console frontend.
# Run from repo root: bash scripts/wheel_build.sh
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# 检测构建环境
source "${REPO_ROOT}/scripts/pack/check_env.sh"

CONSOLE_DIR="$REPO_ROOT/console"
CONSOLE_DEST="$REPO_ROOT/src/qwenpaw/console"

echo "[wheel_build] Building console frontend..."
(cd "$CONSOLE_DIR" && npm ci)
(cd "$CONSOLE_DIR" && npm run build)

echo "[wheel_build] Copying console/dist/* -> src/qwenpaw/console/..."
rm -rf "$CONSOLE_DEST"/*

mkdir -p "$CONSOLE_DEST"
cp -R "$CONSOLE_DIR/dist/"* "$CONSOLE_DEST/"

echo "[wheel_build] Building wheel + sdist..."
# Use a temporary venv to avoid PEP 668 restrictions on system Python
BUILD_VENV="$REPO_ROOT/.build_venv"
if [ ! -d "$BUILD_VENV" ]; then
  python3 -m venv "$BUILD_VENV"
fi
"$BUILD_VENV/bin/pip" install --quiet build
rm -rf dist/*
"$BUILD_VENV/bin/python" -m build --outdir dist .

echo "[wheel_build] Done. Wheel(s) in: $REPO_ROOT/dist/"
