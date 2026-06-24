#!/usr/bin/env bash
# Build portable version for all platforms. Run from repo root.
set -eo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
DIST="${DIST:-dist}"
_TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
PORTABLE_DIR="${DIST}/QwenPaw-Portable_${_TIMESTAMP}"

echo "== Building portable version (timestamp: ${_TIMESTAMP}) =="

# 根据当前平台构建（必须先构建，因为 wheel_build.sh 会清理 dist/）
case "$(uname -s)" in
  Darwin*)
    echo "Building macOS version..."
    bash scripts/pack/build_macos.sh
    mkdir -p "${PORTABLE_DIR}/macOS"
    mv "${DIST}/QwenPaw.app" "${PORTABLE_DIR}/macOS/"
    ;;
  Linux*)
    echo "Building Linux version..."
    bash scripts/pack/build_linux.sh
    mkdir -p "${PORTABLE_DIR}/linux"
    mv "${DIST}/linux/"* "${PORTABLE_DIR}/linux/"
    ;;
  MINGW*|MSYS*|CYGWIN*)
    echo "Building Windows version..."
    powershell -ExecutionPolicy Bypass -File scripts/pack/build_win_portable.ps1
    # build_win_portable.ps1 creates the full portable structure, nothing else to do
    exit 0
    ;;
  *)
    echo "Unsupported platform: $(uname -s)"
    exit 1
    ;;
esac

# 创建 U 盘数据目录（构建完成后再创建，避免被 wheel_build.sh 清理）
mkdir -p "${PORTABLE_DIR}/data"

# 创建 README（根据实际构建平台生成）
PLATFORM_NAME="Unknown"
case "$(uname -s)" in
  Darwin*) PLATFORM_NAME="macOS" ;;
  Linux*)  PLATFORM_NAME="Linux" ;;
esac

cat > "${PORTABLE_DIR}/README.txt" << README
QwenPaw Portable
================

构建平台: ${PLATFORM_NAME}

使用说明：
1. 将此文件夹复制到 U 盘
2. 运行对应程序：
   - macOS: 运行 macOS/QwenPaw.app
   - Linux: 运行 linux/start.sh
   - Windows: 运行 windows/start.vbs（或 start.bat）

所有数据存储在 data/ 目录，可在不同电脑间携带。

目录结构：
├── ${PLATFORM_NAME}/  # 桌面应用
└── data/             # 共享数据目录
    ├── config.json
    ├── workspaces/
    ├── memory/
    └── .secret/

注意事项：
- 首次运行会自动初始化配置
- 建议 U 盘容量 8GB 或以上
- 需要在目标平台分别构建对应版本
README

# 将 profiling 报告移入便携版目录（清理前）
for _pf in "${DIST}/build_profiling.json" "${DIST}/build_common_profiling.json"; do
  if [[ -f "${_pf}" ]]; then
    mv "${_pf}" "${PORTABLE_DIR}/"
    echo "== Moved $(basename "${_pf}") into portable dir =="
  fi
done

# 清理本次构建的中间产物，保留已有的便携版目录
echo "== Cleaning build artifacts =="
# 删除 dist/ 下所有非 QwenPaw-Portable_* 目录的文件和目录
find "${DIST}" -maxdepth 1 \
  ! -name "$(basename "${DIST}")" \
  ! -name "QwenPaw-Portable_*" \
  -exec rm -rf {} + 2>/dev/null || true
# 清理便携版目录中的 .DS_Store 和 __pycache__
find "${PORTABLE_DIR}" -name ".DS_Store" -delete 2>/dev/null || true
find "${PORTABLE_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
# 清除扩展属性（避免复制到 exFAT U 盘后 com.apple.provenance 阻止删除/覆盖技能）
if command -v xattr &>/dev/null; then
  echo "== Stripping extended attributes =="
  xattr -cr "${PORTABLE_DIR}" 2>/dev/null || true
fi
echo "== dist/ cleaned =="

echo "== Portable version built at ${PORTABLE_DIR} =="
echo "== Copy this folder to your USB drive =="
