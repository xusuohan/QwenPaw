# QwenPaw U 盘便携版设计方案

## 概述

基于现有桌面应用改造，实现 QwenPaw 的 U 盘独立部署。所有运行环境和数据存储在 U 盘中，支持 macOS、Linux、Windows 全平台即插即用。

## 目标

- **独立运行**：不依赖宿主机的 Python 环境或任何组件
- **数据本地化**：所有配置、记忆、聊天记录存储在 U 盘
- **全平台支持**：macOS、Linux、Windows 三平台
- **即插即用**：插入 U 盘后双击即可启动

## 技术方案

### 方案选择：基于现有桌面应用改造

**理由**：
- 项目已有 conda-pack 打包机制，改动最小
- 已有 macOS 和 Windows 桌面应用基础
- 利用 `QWENPAW_WORKING_DIR` 环境变量实现数据目录重定向

### U 盘目录结构

```
QwenPaw-Portable/
├── macOS/
│   └── QwenPaw.app                # macOS 桌面应用
├── linux/
│   ├── start.sh                    # Linux 启动脚本
│   └── env/                        # Linux Python 环境
├── windows/
│   ├── QwenPaw Desktop.vbs         # Windows 启动脚本（无控制台）
│   ├── QwenPaw Desktop (Debug).bat # Windows 调试脚本
│   └── env/                        # Windows Python 环境
└── data/                           # 共享数据目录（所有平台共用）
    ├── config.json                 # 全局配置
    ├── workspaces/                 # 工作区数据
    │   └── default/
    │       ├── agent.json
    │       ├── memory/
    │       └── sessions/
    ├── memory/                     # 记忆数据
    ├── .secret/                    # 密钥存储
    └── .backups/                   # 备份数据
```

## 实现细节

### 1. macOS 启动脚本改造

修改 `scripts/pack/build_macos.sh` 中的 launcher 脚本：

```bash
#!/usr/bin/env bash
# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 向上导航到 U 盘根目录
USB_ROOT="$(cd "$SCRIPT_DIR/../../../../.." && pwd)"

# 设置数据目录为 U 盘上的 data 目录
export QWENPAW_WORKING_DIR="$USB_ROOT/data"
export QWENPAW_SECRET_DIR="$USB_ROOT/data/.secret"
export QWENPAW_BACKUP_DIR="$USB_ROOT/data/.backups"

ENV_DIR="$(cd "$(dirname "$0")/../Resources/env" && pwd)"
unset PYTHONPATH
export PYTHONHOME="$ENV_DIR"
export PYTHONNOUSERSITE=1
export QWENPAW_DESKTOP_APP=1
export PATH="$ENV_DIR/bin:$PATH"

# SSL 证书
if [ -x "$ENV_DIR/bin/python" ]; then
  CERT_FILE=$("$ENV_DIR/bin/python" -c \
    "import certifi; print(certifi.where())" 2>/dev/null)
  if [ -n "$CERT_FILE" ] && [ -f "$CERT_FILE" ]; then
    export SSL_CERT_FILE="$CERT_FILE"
    export REQUESTS_CA_BUNDLE="$CERT_FILE"
  fi
fi

# 首次运行初始化
if [ ! -f "$QWENPAW_WORKING_DIR/config.json" ]; then
  "$ENV_DIR/bin/python" -u -m qwenpaw init --defaults --accept-security
fi

exec "$ENV_DIR/bin/python" -u -m qwenpaw desktop
```

### 2. Windows 启动脚本改造

修改 `scripts/pack/build_win.ps1` 中的 launcher：

```bat
@echo off
cd /d "%~dp0"

REM 获取 U 盘根目录（从 windows\env 向上两级）
set "USB_ROOT=%~dp0..\.."

REM 设置数据目录
set "QWENPAW_WORKING_DIR=%USB_ROOT%\data"
set "QWENPAW_SECRET_DIR=%USB_ROOT%\data\.secret"
set "QWENPAW_BACKUP_DIR=%USB_ROOT%\data\.backups"

set "PYTHONNOUSERSITE=1"
set "PATH=%~dp0;%~dp0Scripts;%PATH%"

REM SSL 证书
set "CERT_TMP=%TEMP%\qwenpaw_cert_%RANDOM%.txt"
"%~dp0python.exe" -u -c "import certifi; print(certifi.where())" > "%CERT_TMP%" 2>nul
set /p CERT_FILE=<"%CERT_TMP%"
del "%CERT_TMP%" 2>nul
if defined CERT_FILE (
  if exist "%CERT_FILE%" (
    set "SSL_CERT_FILE=%CERT_FILE%"
  )
)

REM 首次运行初始化
if not exist "%QWENPAW_WORKING_DIR%\config.json" (
  "%~dp0python.exe" -u -m qwenpaw init --defaults --accept-security
)

"%~dp0python.exe" -u -m qwenpaw desktop
```

### 3. 新增 Linux 打包脚本

创建 `scripts/pack/build_linux.sh`：

```bash
#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
PACK_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST="${DIST:-dist}"
ARCHIVE="${DIST}/qwenpaw-env-linux.tar.gz"

echo "== Building wheel =="
bash scripts/wheel_build.sh

echo "== Building conda-packed env =="
python "${PACK_DIR}/build_common.py" --output "$ARCHIVE" --format tar.gz

echo "== Unpacking env =="
mkdir -p "${DIST}/linux/env"
tar -xzf "$ARCHIVE" -C "${DIST}/linux/env" --strip-components=0

if [[ -x "${DIST}/linux/env/bin/conda-unpack" ]]; then
  (cd "${DIST}/linux/env" && ./bin/conda-unpack)
fi

# 创建启动脚本
cat > "${DIST}/linux/start.sh" << 'LAUNCHER'
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
USB_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export QWENPAW_WORKING_DIR="$USB_ROOT/data"
export QWENPAW_SECRET_DIR="$USB_ROOT/data/.secret"
export QWENPAW_BACKUP_DIR="$USB_ROOT/data/.backups"

ENV_DIR="$SCRIPT_DIR/env"
unset PYTHONPATH
export PYTHONHOME="$ENV_DIR"
export PYTHONNOUSERSITE=1
export QWENPAW_DESKTOP_APP=1
export PATH="$ENV_DIR/bin:$PATH"

if [ -x "$ENV_DIR/bin/python" ]; then
  CERT_FILE=$("$ENV_DIR/bin/python" -c \
    "import certifi; print(certifi.where())" 2>/dev/null)
  if [ -n "$CERT_FILE" ] && [ -f "$CERT_FILE" ]; then
    export SSL_CERT_FILE="$CERT_FILE"
    export REQUESTS_CA_BUNDLE="$CERT_FILE"
  fi
fi

if [ ! -f "$QWENPAW_WORKING_DIR/config.json" ]; then
  "$ENV_DIR/bin/python" -u -m qwenpaw init --defaults --accept-security
fi

exec "$ENV_DIR/bin/python" -u -m qwenpaw desktop
LAUNCHER
chmod +x "${DIST}/linux/start.sh"

echo "== Built Linux portable at ${DIST}/linux/ =="
```

### 4. 统一构建脚本

创建 `scripts/pack/build_portable.sh`：

```bash
#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
DIST="${DIST:-dist}"
PORTABLE_DIR="${DIST}/QwenPaw-Portable"

echo "== Building portable version =="

mkdir -p "${PORTABLE_DIR}/data"

case "$(uname -s)" in
  Darwin*)
    echo "Building macOS version..."
    bash scripts/pack/build_macos.sh
    mkdir -p "${PORTABLE_DIR}/macOS"
    cp -R "${DIST}/QwenPaw.app" "${PORTABLE_DIR}/macOS/"
    ;;
  Linux*)
    echo "Building Linux version..."
    bash scripts/pack/build_linux.sh
    mkdir -p "${PORTABLE_DIR}/linux"
    cp -R "${DIST}/linux/"* "${PORTABLE_DIR}/linux/"
    ;;
  *)
    echo "Unsupported platform"
    exit 1
    ;;
esac

cat > "${PORTABLE_DIR}/README.txt" << 'EOF'
QwenPaw Portable
================

使用说明：
1. 将此文件夹复制到 U 盘
2. 根据操作系统运行对应程序：
   - macOS: 运行 macOS/QwenPaw.app
   - Linux: 运行 linux/start.sh
   - Windows: 运行 windows/QwenPaw Desktop.vbs

所有数据存储在 data/ 目录，可在不同电脑间携带。
EOF

echo "== Portable version built at ${PORTABLE_DIR} =="
```

## 关键技术点

| 特性 | 实现方式 |
|------|----------|
| **数据目录** | 通过 `QWENPAW_WORKING_DIR` 环境变量指向 U 盘 `data/` |
| **跨平台共用数据** | 所有平台共享同一个 `data/` 目录 |
| **首次运行** | 启动脚本检测 `config.json` 是否存在，不存在则运行 `init` |
| **SSL 证书** | 使用打包环境中的 certifi，不依赖系统证书 |
| **Python 环境隔离** | 设置 `PYTHONNOUSERSITE=1` 防止加载系统包 |

## 体积预估

| 平台 | 预估大小 |
|------|----------|
| macOS (Apple Silicon) | ~800MB |
| Windows | ~800MB |
| Linux | ~800MB |
| **三平台总计** | ~2.4GB + 数据 |
| **建议 U 盘容量** | 8GB 或以上 |

## 依赖要求

- **conda** (Miniconda/Anaconda) 用于打包
- **Node.js / npm** 用于构建前端
- **NSIS** (Windows) 用于创建安装程序

## 后续扩展

1. **跨平台构建**：在 CI 中为三个平台分别构建
2. **增量更新**：支持 U 盘版本的增量更新机制
3. **数据同步**：可选的数据同步到云端功能
