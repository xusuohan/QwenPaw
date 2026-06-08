#!/usr/bin/env bash
# Build portable version for all platforms. Run from repo root.
set -eo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
DIST="${DIST:-dist}"
PORTABLE_DIR="${DIST}/QwenPaw-Portable"

echo "== Building portable version =="

# 创建 U 盘目录结构
mkdir -p "${PORTABLE_DIR}/data"

# 根据当前平台构建
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
  *)
    echo "Unsupported platform: $(uname -s)"
    echo "Please run this script on macOS or Linux"
    exit 1
    ;;
esac

# 创建 README
cat > "${PORTABLE_DIR}/README.txt" << 'README'
QwenPaw Portable
================

使用说明：
1. 将此文件夹复制到 U 盘
2. 根据操作系统运行对应程序：
   - macOS: 运行 macOS/QwenPaw.app
   - Linux: 运行 linux/start.sh
   - Windows: 运行 windows/QwenPaw Desktop.vbs

所有数据存储在 data/ 目录，可在不同电脑间携带。

目录结构：
├── macOS/          # macOS 桌面应用
├── linux/          # Linux 启动脚本和环境
├── windows/        # Windows 启动脚本和环境
└── data/           # 共享数据目录
    ├── config.json
    ├── workspaces/
    ├── memory/
    └── .secret/

注意事项：
- 首次运行会自动初始化配置
- 所有平台共享同一个 data/ 目录
- 建议 U 盘容量 8GB 或以上
README

# 清理 dist 中的中间产物，只保留便携版目录
echo "== Cleaning dist build artifacts =="
rm -f "${DIST}"/qwenpaw-env*.tar.gz
rm -f "${DIST}"/qwenpaw-*.whl
rm -f "${DIST}"/qwenpaw-*.tar.gz
rm -f "${DIST}"/.DS_Store
rm -rf "${DIST}/data"
echo "== dist/ cleaned =="

echo "== Portable version built at ${PORTABLE_DIR} =="
echo "== Copy this folder to your USB drive =="
