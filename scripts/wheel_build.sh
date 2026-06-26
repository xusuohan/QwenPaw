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

# --- Source hash check: skip rebuild if source unchanged ---
CACHE_DIR="$REPO_ROOT/.cache"
HASH_MARKER="$CACHE_DIR/wheel_source_hash"
mkdir -p "$CACHE_DIR"

# Compute source hash: all .py files in src/ + pyproject.toml + console sources.
# Exclude __pycache__ and .pyc to avoid false positives from bytecode.
SOURCE_HASH=$(
  {
    find "$REPO_ROOT/src" -name "*.py" -not -path "*__pycache__*" 2>/dev/null | sort | xargs cat 2>/dev/null
    cat "$REPO_ROOT/pyproject.toml" 2>/dev/null
    find "$REPO_ROOT/console/src" \( -name "*.ts" -o -name "*.tsx" \) 2>/dev/null | sort | xargs cat 2>/dev/null
    cat "$REPO_ROOT/console/package-lock.json" 2>/dev/null
  } | sha256sum | awk '{print $1}'
)

# Check if wheel already exists for current version with same source hash
CURRENT_VERSION=""
if [ -f "$REPO_ROOT/src/qwenpaw/__version__.py" ]; then
  CURRENT_VERSION=$(
    sed -n 's/^__version__[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
      "$REPO_ROOT/src/qwenpaw/__version__.py" 2>/dev/null
  )
fi

SKIP_BUILD=false
if [ -n "$CURRENT_VERSION" ] && [ -f "$HASH_MARKER" ]; then
  PREV_HASH=$(cat "$HASH_MARKER")
  # Check if wheel exists
  shopt -s nullglob
  EXISTING_WHEELS=("$REPO_ROOT"/dist/qwenpaw-"$CURRENT_VERSION"-*.whl)
  if [ "$PREV_HASH" = "$SOURCE_HASH" ] && [ ${#EXISTING_WHEELS[@]} -gt 0 ]; then
    echo "[wheel_build] Source unchanged (hash: ${SOURCE_HASH:0:12}...), skipping rebuild."
    SKIP_BUILD=true
  fi
fi

if [ "$SKIP_BUILD" = "true" ]; then
  echo "[wheel_build] Done. Using existing wheel(s) in: $REPO_ROOT/dist/"
  exit 0
fi

# --- Full rebuild ---
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
# 只清理 wheel 构建产物，不要全删 dist/（里面可能已有历史 QwenPaw-Portable_* 便携版）
rm -rf dist/qwenpaw-*.whl dist/qwenpaw-*.tar.gz build/ src/*.egg-info
"$BUILD_VENV/bin/python" -m build --outdir dist .

# Save source hash for future cache checks
echo "$SOURCE_HASH" > "$HASH_MARKER"

echo "[wheel_build] Done. Wheel(s) in: $REPO_ROOT/dist/"
