# QwenPaw U 盘便携版实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 改造现有桌面应用，实现 U 盘独立部署，支持 macOS、Linux、Windows 全平台即插即用

**Architecture:** 基于现有 conda-pack 打包机制，修改启动脚本将数据目录重定向到 U 盘的 `data/` 目录，新增 Linux 打包支持

**Tech Stack:** conda-pack, bash, PowerShell, Python, pywebview

---

## 文件结构

| 文件 | 操作 | 说明 |
|------|------|------|
| `scripts/pack/build_macos.sh` | 修改 | 改造 launcher 脚本支持 U 盘数据目录 |
| `scripts/pack/build_win.ps1` | 修改 | 改造 Windows launcher 支持 U 盘数据目录 |
| `scripts/pack/build_linux.sh` | 新增 | Linux 便携版打包脚本 |
| `scripts/pack/build_portable.sh` | 新增 | 统一构建入口脚本 |
| `scripts/pack/README.md` | 修改 | 添加便携版构建说明 |

---

## Task 1: 改造 macOS 启动脚本

**Files:**
- Modify: `scripts/pack/build_macos.sh:60-131`

- [ ] **Step 1: 读取现有 launcher 脚本**

```bash
# 当前 launcher 脚本在 build_macos.sh 的第 60-131 行
# 数据目录默认指向 $HOME/.qwenpaw
# 需要修改为指向 U 盘的 data/ 目录
```

- [ ] **Step 2: 修改 launcher 脚本，添加 U 盘路径检测**

将 `scripts/pack/build_macos.sh` 中的 launcher 部分替换为：

```bash
cat > "${APP_DIR}/Contents/MacOS/${APP_NAME}" << 'LAUNCHER'
#!/usr/bin/env bash
# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 向上导航到 U 盘根目录（从 macOS/QwenPaw.app/Contents/MacOS 到 U 盘根目录）
USB_ROOT="$(cd "$SCRIPT_DIR/../../../../.." && pwd)"

# 检测是否在 U 盘环境中运行
if [ -d "$USB_ROOT/data" ]; then
  # U 盘模式：数据存储在 U 盘
  export QWENPAW_WORKING_DIR="$USB_ROOT/data"
  export QWENPAW_SECRET_DIR="$USB_ROOT/data/.secret"
  export QWENPAW_BACKUP_DIR="$USB_ROOT/data/.backups"
  LOG="$USB_ROOT/data/desktop.log"
else
  # 标准模式：数据存储在用户目录
  LOG="$HOME/.qwenpaw/desktop.log"
fi

ENV_DIR="$(cd "$(dirname "$0")/../Resources/env" && pwd)"
unset PYTHONPATH
export PYTHONHOME="$ENV_DIR"
export PYTHONNOUSERSITE=1
export QWENPAW_DESKTOP_APP=1

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

cd "$HOME" || true

# Log level: env var QWENPAW_LOG_LEVEL or default to "info"
LOG_LEVEL="${QWENPAW_LOG_LEVEL:-info}"

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
  if [ ! -x "$ENV_DIR/bin/python" ]; then
    echo "ERROR: python not executable at $ENV_DIR/bin/python"
    exit 1
  fi
  # 检查配置文件是否存在
  CONFIG_FILE="${QWENPAW_WORKING_DIR:-$HOME/.qwenpaw}/config.json"
  if [ ! -f "$CONFIG_FILE" ]; then
    "$ENV_DIR/bin/python" -u -m qwenpaw init --defaults --accept-security
  fi
  echo "Launching python with log-level=$LOG_LEVEL..."
  "$ENV_DIR/bin/python" -u -m qwenpaw desktop --log-level "$LOG_LEVEL"
  EXIT=$?
  if [ $EXIT -ge 128 ]; then
    SIG=$((EXIT - 128))
    echo "Exit code: $EXIT (killed by signal $SIG, e.g. 9=SIGKILL 15=SIGTERM)"
  else
    echo "Exit code: $EXIT"
  fi
  echo "--- Full log: $LOG (scroll up for Python traceback if app exited early) ---"
  exit $EXIT
fi
# 检查配置文件是否存在
CONFIG_FILE="${QWENPAW_WORKING_DIR:-$HOME/.qwenpaw}/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
  "$ENV_DIR/bin/python" -u -m qwenpaw init --defaults --accept-security
fi
exec "$ENV_DIR/bin/python" -u -m qwenpaw desktop --log-level "$LOG_LEVEL"
LAUNCHER
chmod +x "${APP_DIR}/Contents/MacOS/${APP_NAME}"
```

- [ ] **Step 3: 验证修改**

```bash
# 检查 launcher 脚本语法
bash -n scripts/pack/build_macos.sh
```

Expected: 无输出，语法正确

- [ ] **Step 4: Commit**

```bash
git add scripts/pack/build_macos.sh
git commit -m "feat(portable): 改造 macOS launcher 支持 U 盘数据目录"
```

---

## Task 2: 改造 Windows 启动脚本

**Files:**
- Modify: `scripts/pack/build_win.ps1:161-258`

- [ ] **Step 1: 读取现有 Windows launcher 脚本**

```bash
# 当前 launcher 脚本在 build_win.ps1 的第 161-258 行
# 数据目录默认指向 %USERPROFILE%\.qwenpaw
# 需要修改为指向 U 盘的 data/ 目录
```

- [ ] **Step 2: 修改 Windows launcher 脚本**

将 `scripts/pack/build_win.ps1` 中的 launcher 部分替换为：

```powershell
# Main launcher .bat (will be hidden by VBS)
$LauncherBat = Join-Path $EnvRoot "QwenPaw Desktop.bat"
@"
@echo off
cd /d "%~dp0"

REM 获取 U 盘根目录（从 windows\env 向上两级）
set "USB_ROOT=%~dp0..\.."

REM 检测是否在 U 盘环境中运行
if exist "%USB_ROOT%\data" (
    REM U 盘模式：数据存储在 U 盘
    set "QWENPAW_WORKING_DIR=%USB_ROOT%\data"
    set "QWENPAW_SECRET_DIR=%USB_ROOT%\data\.secret"
    set "QWENPAW_BACKUP_DIR=%USB_ROOT%\data\.backups"
)

REM Isolate packaged Python from user site-packages to prevent conflicts
set "PYTHONNOUSERSITE=1"

REM Preserve system PATH for accessing system commands
REM Prepend packaged env to PATH so packaged Python takes precedence
set "PATH=%~dp0;%~dp0Scripts;%PATH%"

REM Log level: env var QWENPAW_LOG_LEVEL or default to "info"
if not defined QWENPAW_LOG_LEVEL set "QWENPAW_LOG_LEVEL=info"

REM Set SSL certificate paths for packaged environment
REM Use temp file to avoid for /f blocking issue in bat scripts
set "CERT_TMP=%TEMP%\qwenpaw_cert_%RANDOM%.txt"
"%~dp0python.exe" -u -c "import certifi; print(certifi.where())" > "%CERT_TMP%" 2>nul
set /p CERT_FILE=<"%CERT_TMP%"
del "%CERT_TMP%" 2>nul
if defined CERT_FILE (
  if exist "%CERT_FILE%" (
    set "SSL_CERT_FILE=%CERT_FILE%"
    set "REQUESTS_CA_BUNDLE=%CERT_FILE%"
    set "CURL_CA_BUNDLE=%CERT_FILE%"
  )
)

REM 检查配置文件是否存在
set "CONFIG_FILE=%QWENPAW_WORKING_DIR%\config.json"
if not defined QWENPAW_WORKING_DIR set "CONFIG_FILE=%USERPROFILE%\.qwenpaw\config.json"
if not exist "%CONFIG_FILE%" (
  "%~dp0python.exe" -u -m qwenpaw init --defaults --accept-security
)
"%~dp0python.exe" -u -m qwenpaw desktop --log-level %QWENPAW_LOG_LEVEL%
"@ | Set-Content -Path $LauncherBat -Encoding ASCII

# Debug launcher .bat (shows console)
$DebugBat = Join-Path $EnvRoot "QwenPaw Desktop (Debug).bat"
@"
@echo off
cd /d "%~dp0"

REM 获取 U 盘根目录（从 windows\env 向上两级）
set "USB_ROOT=%~dp0..\.."

REM 检测是否在 U 盘环境中运行
if exist "%USB_ROOT%\data" (
    REM U 盘模式：数据存储在 U 盘
    set "QWENPAW_WORKING_DIR=%USB_ROOT%\data"
    set "QWENPAW_SECRET_DIR=%USB_ROOT%\data\.secret"
    set "QWENPAW_BACKUP_DIR=%USB_ROOT%\data\.backups"
)

REM Isolate packaged Python from user site-packages to prevent conflicts
set "PYTHONNOUSERSITE=1"

REM Preserve system PATH for accessing system commands
REM Prepend packaged env to PATH so packaged Python takes precedence
set "PATH=%~dp0;%~dp0Scripts;%PATH%"

REM Debug mode: use debug log level by default (can override with QWENPAW_LOG_LEVEL)
if not defined QWENPAW_LOG_LEVEL set "QWENPAW_LOG_LEVEL=debug"

REM Set SSL certificate paths for packaged environment
REM Use temp file to avoid for /f blocking issue in bat scripts
set "CERT_TMP=%TEMP%\qwenpaw_cert_%RANDOM%.txt"
"%~dp0python.exe" -u -c "import certifi; print(certifi.where())" > "%CERT_TMP%" 2>nul
set /p CERT_FILE=<"%CERT_TMP%"
del "%CERT_TMP%" 2>nul
if defined CERT_FILE (
  if exist "%CERT_FILE%" (
    set "SSL_CERT_FILE=%CERT_FILE%"
    set "REQUESTS_CA_BUNDLE=%CERT_FILE%"
    set "CURL_CA_BUNDLE=%CERT_FILE%"
  )
)

echo ====================================
echo QwenPaw Desktop - Debug Mode
echo ====================================
echo Working Directory: %cd%
echo USB_ROOT: %USB_ROOT%
echo QWENPAW_WORKING_DIR: %QWENPAW_WORKING_DIR%
echo Python: "%~dp0python.exe"
echo PATH: %PATH%
echo PYTHONNOUSERSITE: %PYTHONNOUSERSITE%
echo Log Level: %QWENPAW_LOG_LEVEL%
echo SSL_CERT_FILE: %SSL_CERT_FILE%
echo REQUESTS_CA_BUNDLE: %REQUESTS_CA_BUNDLE%
echo CURL_CA_BUNDLE: %CURL_CA_BUNDLE%
echo.

REM 检查配置文件是否存在
set "CONFIG_FILE=%QWENPAW_WORKING_DIR%\config.json"
if not defined QWENPAW_WORKING_DIR set "CONFIG_FILE=%USERPROFILE%\.qwenpaw\config.json"
if not exist "%CONFIG_FILE%" (
  echo [Init] Creating config...
  "%~dp0python.exe" -u -m qwenpaw init --defaults --accept-security
)
echo [Launch] Starting QwenPaw Desktop with log-level=%QWENPAW_LOG_LEVEL%...
echo Press Ctrl+C to stop
echo.
"%~dp0python.exe" -u -m qwenpaw desktop --log-level %QWENPAW_LOG_LEVEL%
echo.
echo [Exit] QwenPaw Desktop closed
pause
"@ | Set-Content -Path $DebugBat -Encoding ASCII

# VBScript launcher (no console window)
$LauncherVbs = Join-Path $EnvRoot "QwenPaw Desktop.vbs"
@"
Set WshShell = CreateObject("WScript.Shell")
batPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\QwenPaw Desktop.bat"
WshShell.Run Chr(34) & batPath & Chr(34), 0, False
Set WshShell = Nothing
"@ | Set-Content -Path $LauncherVbs -Encoding ASCII
```

- [ ] **Step 3: 验证修改**

```powershell
# 检查 PowerShell 脚本语法
powershell -Command "Get-Command scripts/pack/build_win.ps1 -Syntax"
```

Expected: 显示脚本语法

- [ ] **Step 4: Commit**

```bash
git add scripts/pack/build_win.ps1
git commit -m "feat(portable): 改造 Windows launcher 支持 U 盘数据目录"
```

---

## Task 3: 新增 Linux 打包脚本

**Files:**
- Create: `scripts/pack/build_linux.sh`

- [ ] **Step 1: 创建 Linux 打包脚本**

```bash
cat > scripts/pack/build_linux.sh << 'EOF'
#!/usr/bin/env bash
# One-click build for Linux portable version. Run from repo root.
# Requires: conda, node/npm (for console).

set -e
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
PACK_DIR="$(cd "$(dirname "$0")" && pwd)"
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

echo "== Unpacking env =="
mkdir -p "${DIST}/linux/env"
tar -xzf "$ARCHIVE" -C "${DIST}/linux/env" --strip-components=0

# Fix paths for portability (required or app will crash on launch)
if [[ -x "${DIST}/linux/env/bin/conda-unpack" ]]; then
  (cd "${DIST}/linux/env" && ./bin/conda-unpack)
fi

# Create launcher script
cat > "${DIST}/linux/start.sh" << 'LAUNCHER'
#!/usr/bin/env bash
# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 向上导航到 U 盘根目录（从 linux/ 到 U 盘根目录）
USB_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 检测是否在 U 盘环境中运行
if [ -d "$USB_ROOT/data" ]; then
  # U 盘模式：数据存储在 U 盘
  export QWENPAW_WORKING_DIR="$USB_ROOT/data"
  export QWENPAW_SECRET_DIR="$USB_ROOT/data/.secret"
  export QWENPAW_BACKUP_DIR="$USB_ROOT/data/.backups"
  LOG="$USB_ROOT/data/desktop.log"
else
  # 标准模式：数据存储在用户目录
  LOG="$HOME/.qwenpaw/desktop.log"
fi

ENV_DIR="$SCRIPT_DIR/env"
unset PYTHONPATH
export PYTHONHOME="$ENV_DIR"
export PYTHONNOUSERSITE=1
export QWENPAW_DESKTOP_APP=1
export PATH="$ENV_DIR/bin:$PATH"

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
  if [ ! -x "$ENV_DIR/bin/python" ]; then
    echo "ERROR: python not executable at $ENV_DIR/bin/python"
    exit 1
  fi
  # 检查配置文件是否存在
  CONFIG_FILE="${QWENPAW_WORKING_DIR:-$HOME/.qwenpaw}/config.json"
  if [ ! -f "$CONFIG_FILE" ]; then
    "$ENV_DIR/bin/python" -u -m qwenpaw init --defaults --accept-security
  fi
  echo "Launching python with log-level=$LOG_LEVEL..."
  "$ENV_DIR/bin/python" -u -m qwenpaw desktop --log-level "$LOG_LEVEL"
  EXIT=$?
  if [ $EXIT -ge 128 ]; then
    SIG=$((EXIT - 128))
    echo "Exit code: $EXIT (killed by signal $SIG, e.g. 9=SIGKILL 15=SIGTERM)"
  else
    echo "Exit code: $EXIT"
  fi
  echo "--- Full log: $LOG (scroll up for Python traceback if app exited early) ---"
  exit $EXIT
fi
# 检查配置文件是否存在
CONFIG_FILE="${QWENPAW_WORKING_DIR:-$HOME/.qwenpaw}/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
  "$ENV_DIR/bin/python" -u -m qwenpaw init --defaults --accept-security
fi
exec "$ENV_DIR/bin/python" -u -m qwenpaw desktop --log-level "$LOG_LEVEL"
LAUNCHER
chmod +x "${DIST}/linux/start.sh"

echo "== Built Linux portable at ${DIST}/linux/ =="
EOF
chmod +x scripts/pack/build_linux.sh
```

- [ ] **Step 2: 验证脚本语法**

```bash
bash -n scripts/pack/build_linux.sh
```

Expected: 无输出，语法正确

- [ ] **Step 3: Commit**

```bash
git add scripts/pack/build_linux.sh
git commit -m "feat(portable): 新增 Linux 便携版打包脚本"
```

---

## Task 4: 新增统一构建脚本

**Files:**
- Create: `scripts/pack/build_portable.sh`

- [ ] **Step 1: 创建统一构建脚本**

```bash
cat > scripts/pack/build_portable.sh << 'EOF'
#!/usr/bin/env bash
# Build portable version for all platforms. Run from repo root.
set -e

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
    cp -R "${DIST}/QwenPaw.app" "${PORTABLE_DIR}/macOS/"
    ;;
  Linux*)
    echo "Building Linux version..."
    bash scripts/pack/build_linux.sh
    mkdir -p "${PORTABLE_DIR}/linux"
    cp -R "${DIST}/linux/"* "${PORTABLE_DIR}/linux/"
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

echo "== Portable version built at ${PORTABLE_DIR} =="
echo "== Copy this folder to your USB drive =="
EOF
chmod +x scripts/pack/build_portable.sh
```

- [ ] **Step 2: 验证脚本语法**

```bash
bash -n scripts/pack/build_portable.sh
```

Expected: 无输出，语法正确

- [ ] **Step 3: Commit**

```bash
git add scripts/pack/build_portable.sh
git commit -m "feat(portable): 新增统一构建脚本"
```

---

## Task 5: 更新文档

**Files:**
- Modify: `scripts/pack/README.md`

- [ ] **Step 1: 读取现有文档**

```bash
cat scripts/pack/README.md
```

- [ ] **Step 2: 添加便携版构建说明**

在 `scripts/pack/README.md` 的末尾添加：

```markdown
---

## 构建便携版（U 盘版）

便携版可以将 QwenPaw 完整运行环境打包到 U 盘，支持即插即用。

### 一键构建

从仓库根目录运行：

**macOS / Linux:**
```bash
bash ./scripts/pack/build_portable.sh
# 输出: dist/QwenPaw-Portable/
```

### 便携版目录结构

```
QwenPaw-Portable/
├── macOS/          # macOS 桌面应用
├── linux/          # Linux 启动脚本和环境
├── windows/        # Windows 启动脚本和环境
└── data/           # 共享数据目录（所有平台共用）
    ├── config.json
    ├── workspaces/
    ├── memory/
    └── .secret/
```

### 使用说明

1. 将 `dist/QwenPaw-Portable/` 文件夹复制到 U 盘
2. 在目标电脑上插入 U 盘
3. 根据操作系统运行对应程序：
   - **macOS**: 运行 `macOS/QwenPaw.app`
   - **Linux**: 运行 `linux/start.sh`
   - **Windows**: 运行 `windows/QwenPaw Desktop.vbs`

所有数据（配置、记忆、聊天记录）存储在 `data/` 目录，可在不同电脑间携带。

### 注意事项

- 首次运行会自动初始化配置
- 所有平台共享同一个 `data/` 目录
- 建议 U 盘容量 8GB 或以上
- 需要 conda 和 Node.js/npm 环境来构建
```

- [ ] **Step 3: Commit**

```bash
git add scripts/pack/README.md
git commit -m "docs(portable): 添加便携版构建和使用说明"
```

---

## Task 6: 测试验证

- [ ] **Step 1: 在 macOS 上测试构建**

```bash
bash ./scripts/pack/build_portable.sh
```

Expected: 成功构建，生成 `dist/QwenPaw-Portable/` 目录

- [ ] **Step 2: 验证目录结构**

```bash
ls -la dist/QwenPaw-Portable/
ls -la dist/QwenPaw-Portable/macOS/
ls -la dist/QwenPaw-Portable/data/
```

Expected: 目录结构符合设计

- [ ] **Step 3: 测试启动**

```bash
# 复制到临时 U 盘目录测试
cp -R dist/QwenPaw-Portable /tmp/test-usb/
/tmp/test-usb/macOS/QwenPaw.app/Contents/MacOS/QwenPaw
```

Expected: 应用启动，数据存储在 `/tmp/test-usb/data/`

- [ ] **Step 4: Commit 最终版本**

```bash
git add -A
git commit -m "feat(portable): 完成 U 盘便携版实现"
```

---

## 执行选项

**Plan complete and saved to `docs/superpowers/plans/2026-06-06-usb-portable-implementation.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - 我为每个任务分发一个新的 subagent，任务间进行审查，快速迭代

**2. Inline Execution** - 在当前会话中使用 executing-plans 执行任务，批量执行并设置检查点

**选择哪种方式？**
