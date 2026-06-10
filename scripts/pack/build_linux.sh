#!/usr/bin/env bash
# One-click build for Linux portable version. Run from repo root.
# Requires: conda, node/npm (for console).

set -eo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
PACK_DIR="$(cd "$(dirname "$0")" && pwd)"

# 检测构建环境
source "${PACK_DIR}/check_env.sh"
DIST="${DIST:-dist}"
ARCHIVE="${DIST}/qwenpaw-env-linux.tar.gz"

echo "== Building wheel (includes console frontend) =="
# Skip wheel_build if dist already has a wheel for current version
VERSION_FILE="${REPO_ROOT}/src/qwenpaw/__version__.py"
CURRENT_VERSION=""
if [[ -f "${VERSION_FILE}" ]]; then
  CURRENT_VERSION="$(
    sed -n 's/^__version__[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
      "${VERSION_FILE}" 2>/dev/null
  )"
fi
if [[ -n "${CURRENT_VERSION}" ]]; then
  shopt -s nullglob
  whls=("${REPO_ROOT}/${DIST}/qwenpaw-${CURRENT_VERSION}-"*.whl)
  if [[ ${#whls[@]} -gt 0 ]]; then
    echo "dist/ already has wheel for version ${CURRENT_VERSION}, skipping."
  else
    # Clean up old wheels to avoid confusion
    old_whls=("${REPO_ROOT}/${DIST}/qwenpaw-"*.whl)
    if [[ ${#old_whls[@]} -gt 0 ]]; then
      echo "Removing old wheel files: ${old_whls[*]}"
      rm -f "${old_whls[@]}"
    fi
    bash scripts/wheel_build.sh
  fi
else
  bash scripts/wheel_build.sh
fi

echo "== Building conda-packed env =="
python "${PACK_DIR}/build_common.py" --output "$ARCHIVE" --format tar.gz

echo "== Unpacking env =="
mkdir -p "${DIST}/linux/env"
tar -xzf "$ARCHIVE" -C "${DIST}/linux/env" --strip-components=0

# Create launcher script
cat > "${DIST}/linux/start.sh" << 'LAUNCHER'
#!/usr/bin/env bash
set -eo pipefail
# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 向上导航到 U 盘根目录（从 linux/ 到 U 盘根目录）
USB_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# U 盘模式：所有数据存储在 U 盘，不写入宿主机
export QWENPAW_WORKING_DIR="$USB_ROOT/data"
export QWENPAW_SECRET_DIR="$USB_ROOT/data/.secret"
export QWENPAW_BACKUP_DIR="$USB_ROOT/data/.backups"
LOG="$USB_ROOT/data/desktop.log"
mkdir -p "$USB_ROOT/data" "$USB_ROOT/data/.secret" "$USB_ROOT/data/.backups"

ENV_DIR="$SCRIPT_DIR/env"
unset PYTHONPATH
export PYTHONHOME="$ENV_DIR"
export PYTHONNOUSERSITE=1
export QWENPAW_DESKTOP_APP=1
export PATH="$ENV_DIR/bin:$PATH"

# Fix conda paths for portability (run once at new location)
CONDA_UNPACK_MARKER="$ENV_DIR/.conda-unpack-done"
if [ ! -f "$CONDA_UNPACK_MARKER" ] && [ -x "$ENV_DIR/bin/conda-unpack" ]; then
  (cd "$ENV_DIR" && ./bin/conda-unpack 2>/dev/null && touch "$CONDA_UNPACK_MARKER") || true
fi

# Set SSL certificate paths for packaged environment
if [ -x "$ENV_DIR/bin/python" ]; then
  CERT_FILE=$("$ENV_DIR/bin/python" -c \
    "import certifi; print(certifi.where())" 2>/dev/null)
  if [ -n "$CERT_FILE" ] && [ -f "$CERT_FILE" ]; then
    export SSL_CERT_FILE="$CERT_FILE"
    export REQUESTS_CA_BUNDLE="$CERT_FILE"
    export CURL_CA_BUNDLE="$CERT_FILE"
  fi
fi

# Log level: env var QWENPAW_LOG_LEVEL or default to "info"
LOG_LEVEL="${QWENPAW_LOG_LEVEL:-info}"

# 公共：Python 可执行文件检查
if [ ! -x "$ENV_DIR/bin/python" ]; then
  echo "ERROR: python not executable at $ENV_DIR/bin/python" >&2
  exit 1
fi

# 检查配置文件是否存在（U 盘模式）
CONFIG_FILE="$QWENPAW_WORKING_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
  "$ENV_DIR/bin/python" -u -m qwenpaw init --defaults --accept-security
fi

# 日志模式
if [ ! -t 2 ]; then
  mkdir -p "$(dirname "$LOG")"
  exec >> "$LOG" 2>&1
  { echo "=== $(date) QwenPaw starting ==="
    echo "ENV_DIR=$ENV_DIR"
    echo "USB_ROOT=$USB_ROOT"
    echo "QWENPAW_WORKING_DIR=${QWENPAW_WORKING_DIR:-not set (using default)}"
    echo "Python: $ENV_DIR/bin/python (exists=yes)"
    echo "PATH=$PATH"
    echo "LOG_LEVEL=$LOG_LEVEL"
    echo "SSL_CERT_FILE=${SSL_CERT_FILE:-not set}"
  }
  echo "Launching python with log-level=$LOG_LEVEL..."
  "$ENV_DIR/bin/python" -u -m qwenpaw desktop --log-level "$LOG_LEVEL"
  EXIT=$?
  if [ $EXIT -ge 128 ]; then
    SIG=$((EXIT - 128))
    echo "Exit code: $EXIT (killed by signal $SIG)"
  else
    echo "Exit code: $EXIT"
  fi
  # Safety net: kill orphaned backend processes.
  if pgrep -f "qwenpaw app" >/dev/null 2>&1; then
    "$ENV_DIR/bin/python" -u -m qwenpaw shutdown 2>/dev/null || true
  fi
  echo "--- Full log: $LOG ---"
  exit $EXIT
fi
# TTY mode — also add a safety net via trap
_cleanup_orphans() { "$ENV_DIR/bin/python" -u -m qwenpaw shutdown 2>/dev/null || true; }
trap _cleanup_orphans EXIT
exec "$ENV_DIR/bin/python" -u -m qwenpaw desktop --log-level "$LOG_LEVEL"
LAUNCHER
chmod +x "${DIST}/linux/start.sh"

echo "== Built Linux portable at ${DIST}/linux/ =="
