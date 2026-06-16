# One-click build: Windows portable (USB plug-and-play). Run from repo root.
# Requires: conda, node/npm (for console).
# Output: dist/QwenPaw-Portable/windows/

$ErrorActionPreference = "Stop"
$RepoRoot = (Get-Item $PSScriptRoot).Parent.Parent.FullName
Set-Location $RepoRoot
Write-Host "[build_win_portable] REPO_ROOT=$RepoRoot"
$PackDir = $PSScriptRoot

# --- Environment check ---
Write-Host "== Checking build environment =="
$_missing = 0

function Check-Cmd($cmd, $name, $hint) {
  try {
    $ver = & $cmd --version 2>&1 | Select-Object -First 1
    Write-Host "  [OK] $name`: $ver"
  } catch {
    Write-Host "  [MISSING] $name ('$cmd' not found in PATH)" -ForegroundColor Red
    if ($hint) { Write-Host "           -> $hint" -ForegroundColor Yellow }
    $script:_missing = 1
  }
}

Check-Cmd "node" "Node.js" "Install Node.js 18+: https://nodejs.org/"
Check-Cmd "npm" "npm" "Comes with Node.js. If missing, reinstall Node.js."
Check-Cmd "conda" "Conda" "Install Miniconda: https://docs.conda.io/en/latest/miniconda.html"

$script:PythonCmd = $null
foreach ($cmd in @("py", "python3", "python")) {
  try {
    $null = & $cmd --version 2>&1
    $script:PythonCmd = $cmd
    $ver = & $cmd --version 2>&1 | Select-Object -First 1
    Write-Host "  [OK] Python ($cmd)`: $ver"
    break
  } catch {}
}
if (-not $script:PythonCmd) {
  Write-Host "  [MISSING] Python ('py'/'python3'/'python' not found in PATH)" -ForegroundColor Red
  Write-Host "           -> Install Python 3.8+: https://www.python.org/downloads/" -ForegroundColor Yellow
  $script:_missing = 1
}

if ($script:_missing -ne 0) {
  throw "Missing required tools. Please install them before building."
}
Write-Host "== All build dependencies found =="

# --- Paths ---
$Dist = if ($env:DIST) { $env:DIST } else { "dist" }
$Archive = Join-Path $Dist "qwenpaw-env.zip"
$_Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$PortableRoot = Join-Path $Dist "QwenPaw-Portable_$_Timestamp"
$WinDir = Join-Path $PortableRoot "windows"
$EnvDir = Join-Path $WinDir "env"
$DataDir = Join-Path $PortableRoot "data"

New-Item -ItemType Directory -Force -Path $Dist | Out-Null

# --- Build wheel ---
Write-Host "== Building wheel (includes console frontend) =="
$VersionFile = Join-Path $RepoRoot "src\qwenpaw\__version__.py"
$CurrentVersion = ""
if (Test-Path $VersionFile) {
  $m = (Get-Content $VersionFile -Raw) -match '__version__\s*=\s*"([^"]+)"'
  if ($m) { $CurrentVersion = $Matches[1] }
}
$RunWheelBuild = $true
if ($CurrentVersion) {
  $wheelGlob = Join-Path $Dist "qwenpaw-$CurrentVersion-*.whl"
  $existingWheels = Get-ChildItem -Path $wheelGlob -ErrorAction SilentlyContinue
  if ($existingWheels.Count -gt 0) {
    Write-Host "dist/ already has wheel for version $CurrentVersion, skipping."
    $RunWheelBuild = $false
  } else {
    $oldWheels = Get-ChildItem -Path (Join-Path $Dist "qwenpaw-*.whl") -ErrorAction SilentlyContinue
    if ($oldWheels.Count -gt 0) {
      Write-Host "Removing old wheel files: $($oldWheels | ForEach-Object { $_.Name })"
      $oldWheels | Remove-Item -Force
    }
  }
}
if ($RunWheelBuild) {
  $WheelBuildScript = Join-Path $RepoRoot "scripts\wheel_build.ps1"
  if (-not (Test-Path $WheelBuildScript)) {
    throw "wheel_build.ps1 not found: $WheelBuildScript"
  }
  & $WheelBuildScript
  if ($LASTEXITCODE -ne 0) { throw "wheel_build.ps1 failed with exit code $LASTEXITCODE" }
}

# --- Build conda-packed env ---
Write-Host "== Building conda-packed env =="
$PythonCmd = $null
foreach ($cmd in @("py", "python3", "python")) {
  $null = & $cmd --version 2>&1
  if ($LASTEXITCODE -eq 0) { $PythonCmd = $cmd; break }
}
if (-not $PythonCmd) {
  throw "Python not found."
}
Write-Host "[build_win_portable] Using Python: $PythonCmd"

& $PythonCmd $PackDir\build_common.py --output $Archive --format zip --cache-wheels
if ($LASTEXITCODE -ne 0) {
  throw "build_common.py failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path $Archive)) {
  throw "Archive not created: $Archive"
}

# --- Unpack into portable directory ---
Write-Host "== Unpacking env into portable directory =="
if (Test-Path $WinDir) { Remove-Item -Recurse -Force $WinDir }
New-Item -ItemType Directory -Force -Path $WinDir | Out-Null

Write-Host "[build_win_portable] Extracting $Archive -> $WinDir"
$_7z = Get-Command 7z -ErrorAction SilentlyContinue
if ($_7z) {
  Write-Host "[build_win_portable] Using 7-Zip for fast extraction..."
  & 7z x $Archive -o"$WinDir" -y -aoa | Select-Object -Last 3
 } else {
   Write-Host "[build_win_portable] Using Python for extraction (MAX_PATH safe)..."
   & $PythonCmd $PackDir\extract_zip.py $Archive $WinDir
   if ($LASTEXITCODE -ne 0) { throw "extract_zip.py failed with exit code $LASTEXITCODE" }
}

# Find actual env root (archive may have a top-level directory)
$PythonExe = Get-ChildItem -Path $WinDir -Depth 2 -Filter "python.exe" |
  Where-Object { $_.DirectoryName -match "\\Scripts$" -or $_.DirectoryName -match "/Scripts$" } |
  Select-Object -First 1
if ($PythonExe) {
  $ActualEnvRoot = $PythonExe.Directory.Parent.FullName
  if ($ActualEnvRoot -ne $EnvDir) {
    Write-Host "[build_win_portable] Moving env content from $ActualEnvRoot -> $EnvDir"
    # Content is nested; move it up
    $NestedDir = $ActualEnvRoot
    $TempDir = Join-Path $Dist "_win_env_temp"
    Move-Item $NestedDir $TempDir
    # Remove the wrapper directory
    $ParentOfNested = Split-Path $NestedDir -Parent
    if (($ParentOfNested -ne $WinDir) -and (Test-Path $ParentOfNested)) {
      Remove-Item -Recurse -Force $ParentOfNested
    }
    Move-Item $TempDir $EnvDir
  }
} else {
  # No nested directory; env content is directly under $WinDir
  # Check if python.exe exists at $WinDir\python.exe
  $DirectPython = Join-Path $WinDir "python.exe"
  if (-not (Test-Path $DirectPython)) {
    # Look one level down
    $TopDir = Get-ChildItem -Path $WinDir -Directory | Select-Object -First 1
    if ($TopDir) {
      Write-Host "[build_win_portable] Moving env content from $($TopDir.FullName) -> $EnvDir"
      $TempDir = Join-Path $Dist "_win_env_temp"
      Move-Item $TopDir.FullName $TempDir
      Move-Item $TempDir $EnvDir
    }
  } else {
    # python.exe is directly in $WinDir, create env subdir
    Write-Host "[build_win_portable] Moving env content into env/ subdirectory"
    $TempDir = Join-Path $Dist "_win_env_temp"
    Move-Item $WinDir $TempDir
    New-Item -ItemType Directory -Force -Path $WinDir | Out-Null
    Move-Item $TempDir $EnvDir
  }
}

Write-Host "[build_win_portable] Env directory: $EnvDir"
$PythonExePath = Join-Path $EnvDir "python.exe"
if (-not (Test-Path $PythonExePath)) {
  throw "python.exe not found in env at $EnvDir"
}
Write-Host "[build_win_portable] python.exe found: $PythonExePath"

# --- Copy cached wheels for runtime conda-unpack fix ---
$WheelsCache = Join-Path $RepoRoot ".cache\conda_unpack_wheels"
$PortableWheels = Join-Path $EnvDir ".portable_wheels"
if (Test-Path $WheelsCache) {
  Write-Host "[build_win_portable] Copying cached wheels for runtime conda-unpack fix..."
  Copy-Item -Path $WheelsCache -Destination $PortableWheels -Recurse -Force
  Write-Host "[build_win_portable] Wheels copied to $PortableWheels"
} else {
  Write-Host "[build_win_portable] WARN: No cached wheels found at $WheelsCache" -ForegroundColor Yellow
}

# NOTE: Do NOT run conda-unpack here. It will run at first launch on the target machine.
Write-Host "[build_win_portable] Skipping conda-unpack (deferred to first launch on target machine)"

# --- Pre-compile bytecode ---
Write-Host "== Pre-compiling Python bytecode for faster startup =="
$compileStart = Get-Date
& $PythonExePath -m compileall -q -j 0 $EnvDir
if ($LASTEXITCODE -eq 0) {
  $compileEnd = Get-Date
  $compileTime = ($compileEnd - $compileStart).TotalSeconds
  $pycCount = (Get-ChildItem -Path $EnvDir -Recurse -Filter "*.pyc").Count
  Write-Host "[build_win_portable] Compiled $pycCount .pyc files in $([math]::Round($compileTime, 1))s"
} else {
  Write-Host "[build_win_portable] WARN: compileall failed (exit $LASTEXITCODE), continuing..." -ForegroundColor Yellow
}

# --- Copy icon ---
$IconSrc = Join-Path $PackDir "assets\icon.ico"
if (Test-Path $IconSrc) {
  Copy-Item $IconSrc -Destination $WinDir -Force
  Write-Host "[build_win_portable] Copied icon.ico"
}

# --- Create portable launchers ---

# Packages affected by conda-unpack bug (must match build_common.py)
$CondaUnpackPkgs = "huggingface_hub", "discord.py"

# start.bat - main portable launcher
$StartBat = Join-Path $WinDir "start.bat"
$PkgReinstallLines = ($CondaUnpackPkgs | ForEach-Object {
  "    `"%~dp0env\python.exe`" -m pip install --force-reinstall --no-deps --find-links `"%~dp0env\.portable_wheels`" --no-index $_ 2>nul"
}) -join "`r`n"

@"
@echo off
cd /d "%~dp0"

REM === QwenPaw Portable Launcher ===

REM Resolve USB root (one level up from windows/)
set "USB_ROOT=%~dp0.."

REM Portable mode: all data stored on USB drive
set "QWENPAW_WORKING_DIR=%USB_ROOT%\data"
set "QWENPAW_SECRET_DIR=%USB_ROOT%\data\.secret"
set "QWENPAW_BACKUP_DIR=%USB_ROOT%\data\.backups"
set "QWENPAW_DESKTOP_APP=1"

REM Create data directories
if not exist "%USB_ROOT%\data" mkdir "%USB_ROOT%\data"
if not exist "%USB_ROOT%\data\.secret" mkdir "%USB_ROOT%\data\.secret"
if not exist "%USB_ROOT%\data\.backups" mkdir "%USB_ROOT%\data\.backups"

REM Isolate packaged Python
set "PYTHONNOUSERSITE=1"
set "PATH=%~dp0env;%~dp0env\Scripts;%PATH%"

REM Conda-unpack on first run (fixes paths for new location)
if not exist "%~dp0env\.conda-unpack-done" (
  if exist "%~dp0env\Scripts\conda-unpack.exe" (
    echo [Portable] First run: configuring environment...
    "%~dp0env\Scripts\conda-unpack.exe"
    if errorlevel 1 (
      echo [Portable] WARNING: conda-unpack failed
    ) else (
      type nul > "%~dp0env\.conda-unpack-done"
      REM Fix conda-unpack corruption by reinstalling affected packages
$PkgReinstallLines
    )
  ) else (
    type nul > "%~dp0env\.conda-unpack-done"
  )
)

REM Set SSL certificate paths
set "CERT_TMP=%TEMP%\qwenpaw_cert_%RANDOM%.txt"
"%~dp0env\python.exe" -u -c "import ssl; ssl.create_default_context = lambda *a, **kw: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); import certifi; print(certifi.where())" > "%CERT_TMP%" 2>nul
set /p CERT_FILE=<"%CERT_TMP%"
del "%CERT_TMP%" 2>nul
if defined CERT_FILE (
  if exist "%CERT_FILE%" (
    set "SSL_CERT_FILE=%CERT_FILE%"
    set "REQUESTS_CA_BUNDLE=%CERT_FILE%"
    set "CURL_CA_BUNDLE=%CERT_FILE%"
  )
)

REM Log level
if not defined QWENPAW_LOG_LEVEL set "QWENPAW_LOG_LEVEL=info"

REM Auto-init config if not present
if not exist "%QWENPAW_WORKING_DIR%\config.json" (
  "%~dp0env\python.exe" -u -m qwenpaw init --defaults --accept-security
)

REM Rewrite stale paths from previous device
"%~dp0env\python.exe" -u -m qwenpaw fix-paths

REM Launch
"%~dp0env\python.exe" -u -m qwenpaw desktop --log-level %QWENPAW_LOG_LEVEL%

REM Cleanup orphan backend processes on exit
"%~dp0env\python.exe" -u -m qwenpaw shutdown 2>nul
"@ | Set-Content -Path $StartBat -Encoding ASCII

# start-debug.bat - debug launcher (shows console)
$DebugBat = Join-Path $WinDir "start-debug.bat"
@"
@echo off
cd /d "%~dp0"

REM === QwenPaw Portable Launcher (Debug Mode) ===

REM Resolve USB root (one level up from windows/)
set "USB_ROOT=%~dp0.."

REM Portable mode: all data stored on USB drive
set "QWENPAW_WORKING_DIR=%USB_ROOT%\data"
set "QWENPAW_SECRET_DIR=%USB_ROOT%\data\.secret"
set "QWENPAW_BACKUP_DIR=%USB_ROOT%\data\.backups"
set "QWENPAW_DESKTOP_APP=1"

REM Create data directories
if not exist "%USB_ROOT%\data" mkdir "%USB_ROOT%\data"
if not exist "%USB_ROOT%\data\.secret" mkdir "%USB_ROOT%\data\.secret"
if not exist "%USB_ROOT%\data\.backups" mkdir "%USB_ROOT%\data\.backups"

REM Isolate packaged Python
set "PYTHONNOUSERSITE=1"
set "PATH=%~dp0env;%~dp0env\Scripts;%PATH%"

REM Debug log level by default
if not defined QWENPAW_LOG_LEVEL set "QWENPAW_LOG_LEVEL=debug"

REM Conda-unpack on first run
if not exist "%~dp0env\.conda-unpack-done" (
  if exist "%~dp0env\Scripts\conda-unpack.exe" (
    echo [Portable] First run: configuring environment...
    "%~dp0env\Scripts\conda-unpack.exe"
    if errorlevel 1 (
      echo [Portable] WARNING: conda-unpack failed
    ) else (
      type nul > "%~dp0env\.conda-unpack-done"
      echo [Portable] Fixing conda-unpack corruption...
$PkgReinstallLines
    )
  ) else (
    type nul > "%~dp0env\.conda-unpack-done"
  )
)

REM Set SSL certificate paths
set "CERT_TMP=%TEMP%\qwenpaw_cert_%RANDOM%.txt"
"%~dp0env\python.exe" -u -c "import ssl; ssl.create_default_context = lambda *a, **kw: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); import certifi; print(certifi.where())" > "%CERT_TMP%" 2>nul
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
echo QwenPaw Portable - Debug Mode
echo ====================================
echo USB Root: %USB_ROOT%
echo Working Dir: %QWENPAW_WORKING_DIR%
echo Python: "%~dp0env\python.exe"
echo PATH: %PATH%
echo PYTHONNOUSERSITE: %PYTHONNOUSERSITE%
echo Log Level: %QWENPAW_LOG_LEVEL%
echo SSL_CERT_FILE: %SSL_CERT_FILE%
echo.

REM Auto-init config if not present
if not exist "%QWENPAW_WORKING_DIR%\config.json" (
  echo [Init] Creating config...
  "%~dp0env\python.exe" -u -m qwenpaw init --defaults --accept-security
)

REM Rewrite stale paths from previous device
"%~dp0env\python.exe" -u -m qwenpaw fix-paths

echo [Launch] Starting QwenPaw Desktop with log-level=%QWENPAW_LOG_LEVEL%...
echo Press Ctrl+C to stop
echo.
"%~dp0env\python.exe" -u -m qwenpaw desktop --log-level %QWENPAW_LOG_LEVEL%
echo.
echo [Exit] QwenPaw Desktop closed

REM Cleanup orphan backend processes
"%~dp0env\python.exe" -u -m qwenpaw shutdown 2>nul
pause
"@ | Set-Content -Path $DebugBat -Encoding ASCII

# start.vbs - no console window
$StartVbs = Join-Path $WinDir "start.vbs"
@"
Set WshShell = CreateObject("WScript.Shell")
batPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\start.bat"
WshShell.Run Chr(34) & batPath & Chr(34), 0, False
Set WshShell = Nothing
"@ | Set-Content -Path $StartVbs -Encoding ASCII

# qwenpaw.cmd - CLI wrapper
$QwenpawCmd = Join-Path $WinDir "qwenpaw.cmd"
@"
@echo off
set "PYTHONNOUSERSITE=1"
set "USB_ROOT=%~dp0.."
set "QWENPAW_WORKING_DIR=%USB_ROOT%\data"
set "QWENPAW_SECRET_DIR=%USB_ROOT%\data\.secret"
set "QWENPAW_BACKUP_DIR=%USB_ROOT%\data\.backups"
"%~dp0env\python.exe" -u -m qwenpaw %*
"@ | Set-Content -Path $QwenpawCmd -Encoding ASCII

Write-Host "[build_win_portable] Launchers created"

# --- Create data directory ---
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Write-Host "[build_win_portable] Data directory: $DataDir"

# --- Generate README ---
$Version = $CurrentVersion
if (-not $Version) {
  try {
    $Version = (& $PythonExePath -c "from importlib.metadata import version; print(version('qwenpaw'))" 2>&1) -replace '\s+$', ''
  } catch {}
}
if (-not $Version) { $Version = "0.0.0" }

$ReadmePath = Join-Path $PortableRoot "README.txt"
@"
QwenPaw Portable
================

版本: $Version
构建平台: Windows

使用说明：
1. 将此文件夹复制到 U 盘（建议 8GB 或以上）
2. 运行程序：
   - 双击 windows\start.vbs （无控制台窗口）
   - 或双击 windows\start-debug.bat （显示控制台，用于调试）
   - 或双击 windows\start.bat （显示控制台）

所有数据存储在 data\ 目录，可在不同电脑间携带。

目录结构：
├── windows\           # Windows 桌面应用
│   ├── start.vbs     # 双击启动（推荐）
│   ├── start.bat     # 命令行启动
│   ├── start-debug.bat  # 调试模式
│   ├── qwenpaw.cmd   # CLI 入口
│   └── env\          # Python 运行环境
└── data\             # 共享数据目录
    ├── config.json
    ├── workspaces\
    ├── memory\
    ├── .secret\
    └── .backups\

注意事项：
- 首次运行会自动初始化配置（约 30-60 秒）
- 数据目录 data\ 可与 macOS/Linux 版本共享
- 请勿删除 env\ 目录中的任何文件
"@ | Set-Content -Path $ReadmePath -Encoding UTF8

Write-Host "[build_win_portable] README.txt created"

# --- Optional: Create ZIP ---
if ($env:CREATE_ZIP -eq "1") {
  Write-Host "== Creating ZIP archive =="
  $ZipName = "QwenPaw-Portable-Windows-${Version}-${_Timestamp}.zip"
  $ZipPath = Join-Path $Dist $ZipName
  if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
  Compress-Archive -Path $PortableRoot -DestinationPath $ZipPath -Force
  Write-Host "[build_win_portable] ZIP created: $ZipPath"
}

# --- Clean intermediate artifacts (keep existing portable directories) ---
Write-Host "== Cleaning build artifacts =="
if (Test-Path $Archive) { Remove-Item $Archive -Force }
Write-Host "[build_win_portable] Cleaned up intermediate files"

# --- Summary ---
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Windows portable build complete!" -ForegroundColor Green
Write-Host "  Output: $PortableRoot" -ForegroundColor Green
Write-Host "  Version: $Version" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Copy $PortableRoot to your USB drive."
Write-Host "Users can launch by double-clicking windows\start.vbs"
