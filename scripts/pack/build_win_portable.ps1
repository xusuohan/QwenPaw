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
Start-ProfilStage "wheel_build"
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
Stop-ProfilStage "wheel_build"

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

# --- Profiling ---
$script:ProfilingState = Join-Path $Dist ".build_profiler_state.json"
$script:ProfilingOutput = Join-Path $Dist "build_profiling.json"

function Start-ProfilStage($name) {
  & $PythonCmd "$PackDir\build_profiler.py" start $name --state-file $script:ProfilingState --platform "Windows-$([System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture)" --python-version "3.10"
}

function Stop-ProfilStage($name) {
  & $PythonCmd "$PackDir\build_profiler.py" end $name --state-file $script:ProfilingState
}

function Save-ProfilReport() {
  & $PythonCmd "$PackDir\build_profiler.py" save $script:ProfilingOutput --state-file $script:ProfilingState
}

Start-ProfilStage "conda_pack_env"
& $PythonCmd $PackDir\build_common.py --output $Archive --format zip --profiling-output (Join-Path $Dist "build_common_profiling.json")
if ($LASTEXITCODE -ne 0) {
  throw "build_common.py failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path $Archive)) {
  throw "Archive not created: $Archive"
}
Stop-ProfilStage "conda_pack_env"

# --- Unpack into portable directory ---
Start-ProfilStage "unpack"
# Extract directly into env/ subdir to avoid a post-extraction Move-Item.
# Move-Item fails on Windows when individual file paths exceed MAX_PATH (260)
# — common in site-packages with deeply nested __pycache__/*.pyc files.
# 7z and extract_zip.py both handle long paths at write time, so writing
# straight to env/ sidesteps the limit entirely.
Write-Host "== Unpacking env into portable directory =="
if (Test-Path $WinDir) { Remove-Item -Recurse -Force $WinDir }
New-Item -ItemType Directory -Force -Path $WinDir | Out-Null
New-Item -ItemType Directory -Force -Path $EnvDir | Out-Null

Write-Host "[build_win_portable] Extracting $Archive -> $EnvDir"
$extractStart = Get-Date
$_7z = Get-Command 7z -ErrorAction SilentlyContinue
if ($_7z) {
  Write-Host "[build_win_portable] Using 7-Zip for fast extraction..."
  # -bso0 / -bse0 / -bsp0: silence stdout/stderr/progress streams.
  # Without these, 7z emits a per-byte progress bar that PowerShell's pipe
  # has to buffer — adding minutes to a 80k-file extraction on Windows.
  & 7z x $Archive -o"$EnvDir" -y -aoa -bso0 -bse0 -bsp0
  if ($LASTEXITCODE -ne 0) { throw "7z extraction failed with exit code $LASTEXITCODE" }
} else {
  Write-Host "[build_win_portable] 7-Zip not in PATH — using Python long-path extractor..." -ForegroundColor Yellow
  Write-Host "[build_win_portable] Hint: install 7-Zip (https://www.7-zip.org/) for ~10x faster extraction" -ForegroundColor Yellow
  & $PythonCmd $PackDir\extract_zip.py $Archive $EnvDir
  if ($LASTEXITCODE -ne 0) { throw "extract_zip.py failed with exit code $LASTEXITCODE" }
}
$extractEnd = Get-Date
$extractTime = ($extractEnd - $extractStart).TotalSeconds
Write-Host "[build_win_portable] Extraction done in $([math]::Round($extractTime, 1))s"
Stop-ProfilStage "unpack"

# If the archive has a top-level wrapper directory (rare for conda-pack
# output, but possible), python.exe will be nested one level deeper than
# expected. Use robocopy (MAX_PATH-safe) to flatten it in place.
$DirectPython = Join-Path $EnvDir "python.exe"
if (-not (Test-Path $DirectPython)) {
  $PythonExe = Get-ChildItem -Path $EnvDir -Depth 2 -Filter "python.exe" |
    Where-Object { $_.DirectoryName -match "\\Scripts$" -or $_.DirectoryName -match "/Scripts$" } |
    Select-Object -First 1
  if ($PythonExe) {
    $NestedEnvRoot = $PythonExe.Directory.Parent.FullName
    Write-Host "[build_win_portable] Flattening nested env content from $NestedEnvRoot -> $EnvDir"
    # robocopy /E /MOVE handles >260-char paths and removes the source tree.
    # /NFL /NDL /NJH /NJS suppresses per-file logging; exit codes <8 are OK.
    $robocopyArgs = @($NestedEnvRoot, $EnvDir, "/E", "/MOVE", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    & robocopy @robocopyArgs | Out-Null
    if ($LASTEXITCODE -ge 8) {
      throw "robocopy flatten failed with exit code $LASTEXITCODE"
    }
    # Clean up the now-empty wrapper directory
    $Wrapper = Split-Path $NestedEnvRoot -Parent
    if (($Wrapper -ne $EnvDir) -and (Test-Path $Wrapper)) {
      Remove-Item -Recurse -Force $Wrapper -ErrorAction SilentlyContinue
    }
    $global:LASTEXITCODE = 0
  }
}

Write-Host "[build_win_portable] Env directory: $EnvDir"
$PythonExePath = Join-Path $EnvDir "python.exe"
if (-not (Test-Path $PythonExePath)) {
  throw "python.exe not found in env at $EnvDir"
}
Write-Host "[build_win_portable] python.exe found: $PythonExePath"

# NOTE: conda-unpack is NOT needed for the portable build.
# All launchers use `python.exe` and `python -m` which resolve paths
# dynamically from the exe location. Python's site.py derives sys.prefix
# at runtime, so site-packages discovery works without path rewriting.

# --- Pre-compile bytecode ---
Start-ProfilStage "compileall"
Write-Host "== Pre-compiling Python bytecode for faster startup =="
# Skip large indirect-dependency packages. Criteria: project source does not
# directly `import` them, OR compileall has previously failed on their files
# (Windows MAX_PATH / temp-file races). Python compiles lazily on first
# import at runtime, so skipping only delays first use of these packages,
# not core startup.
#   kubernetes (69MB) - indirect dep, never imported by qwenpaw
#   sympy       (48MB) - indirect dep (transformers), never imported
#   modelscope  (37MB) - imported lazily by local_models; had compile errors
#   twilio      (37MB) - voice channel only; had compile errors
#   lark_oapi   (41MB) - feishu channel only; had compile errors
$CompileSkipRegex = "kubernetes|sympy|modelscope|twilio|lark_oapi|transformers|onnxruntime|huggingface_hub|playwright|discord|matrix.nio|telegram|pillow"
$compileStart = Get-Date
$compileTimeoutSec = 600  # 10 minutes max for bytecode compilation
$compileJob = Start-Job -ScriptBlock {
  param($py, $skipRx, $dir)
  & $py -m compileall -q -j 0 --invalidation-mode checked-hash -x $skipRx $dir
  return $LASTEXITCODE
} -ArgumentList $PythonExePath, $CompileSkipRegex, $EnvDir
$compileResult = $compileJob | Wait-Job -Timeout $compileTimeoutSec
if ($null -eq $compileResult) {
  Write-Host "[build_win_portable] WARN: compileall timed out after ${compileTimeoutSec}s, stopping..." -ForegroundColor Yellow
  $compileJob | Stop-Job
  $compileExit = -1
} else {
  $compileExit = Receive-Job $compileJob
}
Remove-Job $compileJob -Force -ErrorAction SilentlyContinue
$compileEnd = Get-Date
$compileTime = ($compileEnd - $compileStart).TotalSeconds
$pycCount = (Get-ChildItem -Path $EnvDir -Recurse -Filter "*.pyc").Count
Write-Host "[build_win_portable] Compiled $pycCount .pyc files in $([math]::Round($compileTime, 1))s (skipped: $CompileSkipRegex)"
if ($compileExit -ne 0) {
  Write-Host "[build_win_portable] WARN: compileall exit $compileExit (some files skipped), continuing..." -ForegroundColor Yellow
}
Stop-ProfilStage "compileall"

# --- Strip debug symbols from DLLs (optional, requires MSVC toolchain) ---
Start-ProfilStage "strip"
Write-Host "== Stripping debug symbols from DLLs =="
$_editbin = Get-Command editbin -ErrorAction SilentlyContinue
if ($_editbin) {
  $stripStart = Get-Date
  $stripCount = 0
  $stripSaved = 0
  Get-ChildItem -Path $EnvDir -Recurse -Include "*.dll","*.pyd" -ErrorAction SilentlyContinue | ForEach-Object {
    $origSize = $_.Length
    & editbin /RELEASE $_.FullName 2>$null
    if ($LASTEXITCODE -eq 0) {
      $stripCount++
      $stripSaved += ($origSize - $_.Length)
    }
    $global:LASTEXITCODE = 0
  }
  $stripEnd = Get-Date
  $stripTime = ($stripEnd - $stripStart).TotalSeconds
  $savedMB = [math]::Round($stripSaved / 1MB, 1)
  Write-Host "[build_win_portable] Stripped $stripCount files, saved ${savedMB}MB in $([math]::Round($stripTime, 1))s"
} else {
  Write-Host "[build_win_portable] editbin not found — skipping DLL stripping (Windows DLLs are typically pre-stripped by MSVC)" -ForegroundColor Yellow
}
Stop-ProfilStage "strip"

# --- Copy icon ---
$IconSrc = Join-Path $PackDir "assets\icon.ico"
if (Test-Path $IconSrc) {
  Copy-Item $IconSrc -Destination $WinDir -Force
  Write-Host "[build_win_portable] Copied icon.ico"
}

# --- Pre-resolve certifi cacert.pem path (relative to env) ---
# Hardcoding this into the launchers lets start.bat skip a Python cold-start
# on every launch (saves ~0.5-1s). Falls back to dynamic lookup at runtime
# if the file has moved (e.g. certifi version mismatch).
$CertifiRel = $null
try {
  $certOut = & $PythonExePath -c "import certifi, os; print(os.path.relpath(certifi.where(), os.environ['VIRTUAL_ENV'] if 'VIRTUAL_ENV' in os.environ else os.path.dirname(os.path.dirname(os.__file__))))" 2>&1
  if ($LASTEXITCODE -eq 0 -and $certOut) {
    # Convert to env-root-relative using the actual EnvDir
    $certAbs = & $PythonExePath -c "import certifi; print(certifi.where())" 2>&1
    $certAbs = $certAbs.Trim()
    $envNorm = $EnvDir.TrimEnd('\', '/') + '\'
    if ($certAbs.StartsWith($envNorm)) {
      $CertifiRel = $certAbs.Substring($envNorm.Length)
    }
  }
} catch {}
if (-not $CertifiRel) {
  $CertifiRel = "Lib\site-packages\certifi\cacert.pem"
  Write-Host "[build_win_portable] Using default certifi path: $CertifiRel" -ForegroundColor Yellow
} else {
  Write-Host "[build_win_portable] certifi cacert.pem resolved to: $CertifiRel"
}

# --- Create portable launchers ---
Start-ProfilStage "platform_pack"

# start.bat - main portable launcher
$StartBat = Join-Path $WinDir "start.bat"

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

REM Isolate packaged Python
set "PYTHONNOUSERSITE=1"
set "PATH=%~dp0env;%~dp0env\Scripts;%PATH%"

REM Ensure data directories exist
mkdir "%USB_ROOT%\data" 2>nul
mkdir "%USB_ROOT%\data\.secret" 2>nul
mkdir "%USB_ROOT%\data\.backups" 2>nul

REM Set SSL certificate paths (pre-resolved at build time, Python fallback)
set "CERT_FILE=%~dp0env\$CertifiRel"
if not exist "%CERT_FILE%" (
  set "CERT_TMP=%TEMP%\qwenpaw_cert_%RANDOM%.txt"
  "%~dp0env\python.exe" -u -c "import ssl; ssl.create_default_context = lambda *a, **kw: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); import certifi; print(certifi.where())" > "%CERT_TMP%" 2>nul
  set /p CERT_FILE=<"%CERT_TMP%"
  del "%CERT_TMP%" 2>nul
)
if exist "%CERT_FILE%" (
  set "SSL_CERT_FILE=%CERT_FILE%"
  set "REQUESTS_CA_BUNDLE=%CERT_FILE%"
  set "CURL_CA_BUNDLE=%CERT_FILE%"
)

REM Log level
if not defined QWENPAW_LOG_LEVEL set "QWENPAW_LOG_LEVEL=info"

REM Launch (fix-paths runs in-process via --fix-paths flag)
"%~dp0env\python.exe" -u -m qwenpaw desktop --fix-paths --log-level %QWENPAW_LOG_LEVEL%

REM Cleanup handled by Windows Job Object (KILL_ON_JOB_CLOSE) in desktop_cmd.py
"@ | Set-Content -Path $StartBat -Encoding ASCII

# start-debug.bat - debug launcher (shows console, optimized)
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

REM Isolate packaged Python
set "PYTHONNOUSERSITE=1"
set "PATH=%~dp0env;%~dp0env\Scripts;%PATH%"

REM Ensure data directories exist
mkdir "%USB_ROOT%\data" 2>nul
mkdir "%USB_ROOT%\data\.secret" 2>nul
mkdir "%USB_ROOT%\data\.backups" 2>nul

REM Debug log level by default
if not defined QWENPAW_LOG_LEVEL set "QWENPAW_LOG_LEVEL=debug"

REM Set SSL certificate paths (pre-resolved at build time, Python fallback)
set "CERT_FILE=%~dp0env\$CertifiRel"
if not exist "%CERT_FILE%" (
  set "CERT_TMP=%TEMP%\qwenpaw_cert_%RANDOM%.txt"
  "%~dp0env\python.exe" -u -c "import ssl; ssl.create_default_context = lambda *a, **kw: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); import certifi; print(certifi.where())" > "%CERT_TMP%" 2>nul
  set /p CERT_FILE=<"%CERT_TMP%"
  del "%CERT_TMP%" 2>nul
)
if exist "%CERT_FILE%" (
  set "SSL_CERT_FILE=%CERT_FILE%"
  set "REQUESTS_CA_BUNDLE=%CERT_FILE%"
  set "CURL_CA_BUNDLE=%CERT_FILE%"
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

echo [Launch] Starting QwenPaw Desktop with log-level=%QWENPAW_LOG_LEVEL%...
echo Press Ctrl+C to stop
echo.
"%~dp0env\python.exe" -u -m qwenpaw desktop --fix-paths --log-level %QWENPAW_LOG_LEVEL%
echo.
echo [Exit] QwenPaw Desktop closed

REM Cleanup handled by Windows Job Object (KILL_ON_JOB_CLOSE) in desktop_cmd.py
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
Stop-ProfilStage "platform_pack"

# --- Save profiling report ---
Save-ProfilReport

# --- Smoke test ---
Write-Host "== Running smoke test =="
$smokeStart = Get-Date
$smokeScript = Join-Path $RepoRoot "scripts\smoke-test.py"
if (Test-Path $smokeScript) {
  try {
    & $PythonExePath $smokeScript --portable-dir $PortableRoot --verbose
    if ($LASTEXITCODE -ne 0) {
      Write-Host "[build_win_portable] Smoke test FAILED" -ForegroundColor Red
    }
  } catch {
    Write-Host "[build_win_portable] Smoke test threw exception: $_" -ForegroundColor Red
    Write-Host "[build_win_portable] Falling back to inline check..." -ForegroundColor Yellow
    $smokeOut = & $PythonExePath -c "from qwenpaw.__version__ import __version__; print(__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) {
      Write-Host "[build_win_portable] Inline smoke test PASSED: $smokeOut" -ForegroundColor Green
    } else {
      Write-Host "[build_win_portable] Inline smoke test FAILED (exit code $LASTEXITCODE)" -ForegroundColor Red
    }
  }
} else {
  Write-Host "[build_win_portable] WARN: smoke-test.py not found, running inline check" -ForegroundColor Yellow
  $smokeOut = & $PythonExePath -c "from qwenpaw.__version__ import __version__; print(__version__)" 2>&1
  if ($LASTEXITCODE -eq 0) {
    Write-Host "[build_win_portable] Smoke test PASSED: $smokeOut" -ForegroundColor Green
  } else {
    Write-Host "[build_win_portable] Smoke test FAILED (exit code $LASTEXITCODE)" -ForegroundColor Red
  }
}
$smokeEnd = Get-Date
Write-Host "[build_win_portable] Smoke test took $([math]::Round(($smokeEnd - $smokeStart).TotalSeconds, 1))s"

# --- Optional: Create ZIP ---
if ($env:CREATE_ZIP -eq "1") {
  Write-Host "== Creating ZIP archive =="
  $ZipName = "QwenPaw-Portable-Windows-${Version}-${_Timestamp}.zip"
  $ZipPath = Join-Path $Dist $ZipName
  if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
  Compress-Archive -Path $PortableRoot -DestinationPath $ZipPath -Force
  Write-Host "[build_win_portable] ZIP created: $ZipPath"
}

# --- Clean intermediate artifacts (keep ALL QwenPaw-Portable_* directories) ---
Write-Host "== Cleaning build artifacts =="
# Remove everything in dist/ except historical portable packages and cache files.
# This matches build_portable.sh: preserves QwenPaw-Portable_* dirs,
# qwenpaw-*.whl, qwenpaw-*.tar.gz (cache chain), and profiling output.
if (Test-Path $Dist) {
  Get-ChildItem -Path $Dist -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -notmatch '^QwenPaw-Portable_' -and
    $_.Name -notmatch '^qwenpaw-.*\.whl$' -and
    $_.Name -notmatch '^qwenpaw-.*\.tar\.gz$' -and
    $_.Name -ne 'build_profiling.json' -and
    $_.Name -ne 'build_common_profiling.json'
  } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
# Strip .DS_Store droppings (cross-platform copy from macOS can leave these).
# Intentionally NOT removing __pycache__: those hold the compileall .pyc files
# we just built for faster startup.
Get-ChildItem -Path $PortableRoot -Recurse -Force -ErrorAction SilentlyContinue -File |
  Where-Object { $_.Name -eq ".DS_Store" } |
  Remove-Item -Force -ErrorAction SilentlyContinue
Write-Host "[build_win_portable] Cleaned up intermediate files (all QwenPaw-Portable_* dirs kept)"

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
