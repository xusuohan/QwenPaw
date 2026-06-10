#!/usr/bin/env bash
# One-click build: console -> conda-pack -> QwenPaw.app. Run from repo root.
# Requires: conda, node/npm (for console). Optional: icon.icns in assets/.

set -eo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
PACK_DIR="$(cd "$(dirname "$0")" && pwd)"

# 检测构建环境
source "${PACK_DIR}/check_env.sh"
DIST="${DIST:-dist}"
ARCHIVE="${DIST}/qwenpaw-env.tar.gz"
APP_NAME="QwenPaw"
APP_DIR="${DIST}/${APP_NAME}.app"

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
  whls=("${REPO_ROOT}/dist/qwenpaw-${CURRENT_VERSION}-"*.whl)
  if [[ ${#whls[@]} -gt 0 ]]; then
    echo "dist/ already has wheel for version ${CURRENT_VERSION}, skipping."
  else
    # Clean up old wheels to avoid confusion
    old_whls=("${REPO_ROOT}/dist/qwenpaw-"*.whl)
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

echo "== Building .app bundle =="
rm -rf "$APP_DIR"
mkdir -p "${APP_DIR}/Contents/MacOS"
mkdir -p "${APP_DIR}/Contents/Resources"

# Unpack conda env into Resources/env
mkdir -p "${APP_DIR}/Contents/Resources/env"
tar -xzf "$ARCHIVE" -C "${APP_DIR}/Contents/Resources/env" --strip-components=0

# Launcher: force packed env; when no TTY log to ~/.qwenpaw/desktop.log (no exec so we see errors)
cat > "${APP_DIR}/Contents/MacOS/${APP_NAME}" << 'LAUNCHER'
#!/usr/bin/env bash
# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 向上导航到 QwenPaw-Portable 目录（从 macOS/QwenPaw.app/Contents/MacOS 向上 4 级）
USB_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

# U 盘模式：所有数据存储在 U 盘，不写入宿主机
export QWENPAW_WORKING_DIR="$USB_ROOT/data"
export QWENPAW_SECRET_DIR="$USB_ROOT/data/.secret"
export QWENPAW_BACKUP_DIR="$USB_ROOT/data/.backups"
LOG="$USB_ROOT/data/desktop.log"
mkdir -p "$USB_ROOT/data" "$USB_ROOT/data/.secret" "$USB_ROOT/data/.backups"

ENV_DIR="$(cd "$(dirname "$0")/../Resources/env" && pwd)" || {
  echo "ERROR: env directory not found. App may be corrupted." >&2; exit 1
}
unset PYTHONPATH
export PYTHONHOME="$ENV_DIR"
export PYTHONNOUSERSITE=1
export QWENPAW_DESKTOP_APP=1

# Fix conda paths for portability (run once at new location)
CONDA_UNPACK_MARKER="$ENV_DIR/.conda-unpack-done"
if [ ! -f "$CONDA_UNPACK_MARKER" ] && [ -x "$ENV_DIR/bin/conda-unpack" ]; then
  (cd "$ENV_DIR" && ./bin/conda-unpack 2>/dev/null && touch "$CONDA_UNPACK_MARKER") || true
fi

# Preserve system PATH for accessing system commands (e.g. imsg, brew)
# Prepend packaged env/bin so packaged Python takes precedence
export PATH="$ENV_DIR/bin:$PATH"

# Set SSL certificate paths for packaged environment
# Query certifi path from the packaged Python interpreter
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

# 校验 Python 可执行文件
if [ ! -x "$ENV_DIR/bin/python" ]; then
  echo "ERROR: python not executable at $ENV_DIR/bin/python" >&2
  exit 1
fi

# 检查配置文件是否存在（U 盘模式）
CONFIG_FILE="$QWENPAW_WORKING_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
  "$ENV_DIR/bin/python" -u -m qwenpaw init --defaults --accept-security
fi

# 非 TTY 模式：设置日志重定向
if [ ! -t 2 ]; then
  mkdir -p "$(dirname "$LOG")"
  { echo "=== $(date) QwenPaw starting ==="
    echo "ENV_DIR=$ENV_DIR"
    echo "USB_ROOT=$USB_ROOT"
    echo "QWENPAW_WORKING_DIR=${QWENPAW_WORKING_DIR:-not set (using default)}"
    echo "Python: $ENV_DIR/bin/python (exists=$([ -x "$ENV_DIR/bin/python" ] && echo yes || echo no))"
    echo "PATH=$PATH"
    echo "LOG_LEVEL=$LOG_LEVEL"
    echo "SSL_CERT_FILE=${SSL_CERT_FILE:-not set}"
    if [ -n "$SSL_CERT_FILE" ] && [ -f "$SSL_CERT_FILE" ]; then
      echo "SSL certificate file found at $SSL_CERT_FILE"
    elif [ -n "$SSL_CERT_FILE" ]; then
      echo "WARNING: SSL_CERT_FILE set but file does not exist: $SSL_CERT_FILE"
    else
      echo "WARNING: SSL_CERT_FILE not set, SSL connections may fail"
    fi
  } >> "$LOG"
  exec 2>> "$LOG"
  exec 1>> "$LOG"
fi

echo "Launching python with log-level=$LOG_LEVEL..."
"$ENV_DIR/bin/python" -u -m qwenpaw desktop --log-level "$LOG_LEVEL"
EXIT=$?

# Safety net: ensure no orphaned backend processes survive after exit.
# This handles edge cases where the desktop process was force-killed
# (SIGKILL) before its own cleanup could run.
if [ $EXIT -ne 0 ] || pgrep -f "qwenpaw app" >/dev/null 2>&1; then
  "$ENV_DIR/bin/python" -u -m qwenpaw shutdown 2>/dev/null || true
fi
exit $EXIT
LAUNCHER
chmod +x "${APP_DIR}/Contents/MacOS/${APP_NAME}"

# Icon: use pre-generated icon.icns
if [[ -f "${PACK_DIR}/assets/icon.icns" ]]; then
  echo "== Using pre-generated icon.icns =="
else
  echo "Warning: icon.icns not found at ${PACK_DIR}/assets/icon.icns"
  echo "Generate it first: bash scripts/pack/generate_icons.sh"
fi

# Info.plist (include icon key if icon.icns exists)
# Prioritize version from __version__.py to ensure accuracy
VERSION="${CURRENT_VERSION}"
if [[ -z "${VERSION}" ]]; then
  # Fallback: try to get version from packed env metadata
  VERSION="$("${APP_DIR}/Contents/Resources/env/bin/python" -c \
    "from importlib.metadata import version; print(version('qwenpaw'))" 2>/dev/null \
    || echo "0.0.0")"
  echo "Using version from packed env metadata: ${VERSION}"
else
  echo "Version determined from __version__.py: ${VERSION}"
fi
ICON_PLIST=""
if [[ -f "${PACK_DIR}/assets/icon.icns" ]]; then
  cp "${PACK_DIR}/assets/icon.icns" "${APP_DIR}/Contents/Resources/"
  ICON_PLIST="<key>CFBundleIconFile</key><string>icon.icns</string>
  "
fi
cat > "${APP_DIR}/Contents/Info.plist" << INFOPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>${APP_NAME}</string>
  <key>CFBundleIdentifier</key><string>com.qwenpaw.desktop</string>
  <key>CFBundleName</key><string>${APP_NAME}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  ${ICON_PLIST}<key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSDesktopFolderUsageDescription</key><string>QwenPaw may access files in your Desktop folder if you use file-related features. You can choose Don'\''t Allow; the app will still run with limited file access.</string>
</dict>
</plist>
INFOPLIST

echo "== Built ${APP_DIR} =="
# Optional: create zip for distribution (set CREATE_ZIP=1)
if [[ -n "${CREATE_ZIP}" ]]; then
  ZIP_NAME="${DIST}/QwenPaw-${VERSION}-macOS.zip"
  ditto -c -k --sequesterRsrc --keepParent "${APP_DIR}" "${ZIP_NAME}"
  echo "== Created ${ZIP_NAME} =="
fi
