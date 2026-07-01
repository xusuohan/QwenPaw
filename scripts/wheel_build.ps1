# Build a full wheel package including the latest console frontend.
# Run from repo root: pwsh -File scripts/wheel_build.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = (Get-Item $PSScriptRoot).Parent.FullName
Set-Location $RepoRoot

# --- Environment check ---
Write-Host "== Checking build environment =="
$_missing = 0

# Node.js / npm
foreach ($pair in @(@("node","Node.js","Install Node.js 18+: https://nodejs.org/"), @("npm","npm","Comes with Node.js"))) {
  $cmd, $name, $hint = $pair
  try {
    $ver = & $cmd --version 2>&1 | Select-Object -First 1
    Write-Host "  [OK] $name`: $ver"
  } catch {
    Write-Host "  [MISSING] $name ('$cmd' not found)" -ForegroundColor Red
    Write-Host "           -> $hint" -ForegroundColor Yellow
    $_missing = 1
  }
}

# Python
$PythonCmd = $null
foreach ($cmd in @("py", "python3", "python")) {
  try {
    $null = & $cmd --version 2>&1
    $PythonCmd = $cmd
    $ver = & $cmd --version 2>&1 | Select-Object -First 1
    Write-Host "  [OK] Python ($cmd)`: $ver"
    break
  } catch {}
}
if (-not $PythonCmd) {
  Write-Host "  [MISSING] Python" -ForegroundColor Red
  Write-Host "           -> Install Python 3.8+: https://www.python.org/downloads/" -ForegroundColor Yellow
  $_missing = 1
}

if ($_missing -ne 0) { throw "Missing required tools. Please install them before building." }
Write-Host "== All build dependencies found =="
# --- End environment check ---

$ConsoleDir = Join-Path $RepoRoot "console"
$ConsoleDest = Join-Path $RepoRoot "src\qwenpaw\console"

# --- Source hash check: skip rebuild if source unchanged (combined hash) ---
# The bash version (wheel_build.sh) splits Python/console hashes to skip npm
# on Python-only edits; PowerShell uses a single combined hash for simplicity.
# Any error -> full rebuild (safe default). Skipping requires: marker exists,
# a wheel for the current version exists, AND the hash matches exactly.
$CacheDir = Join-Path $RepoRoot ".cache"
if (-not (Test-Path $CacheDir)) { New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null }
$HashMarker = Join-Path $CacheDir "wheel_source_hash"

function Get-StringHash {
  param([string]$Content)
  if ($null -eq $Content) { $Content = "" }
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($Content)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLower() }
  finally { $sha.Dispose() }
}

$SkipBuild = $false
$SourceHash = $null
try {
  $pyFiles = @(Get-ChildItem -Path (Join-Path $RepoRoot "src") -Recurse -Filter *.py -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '__pycache__' } | Sort-Object FullName)
  $pyContent = ($pyFiles | ForEach-Object { Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue }) -join "`n"
  $pyContent += "`n" + (Get-Content (Join-Path $RepoRoot "pyproject.toml") -Raw -ErrorAction SilentlyContinue)
  $consoleFiles = @(Get-ChildItem -Path (Join-Path $RepoRoot "console\src") -Recurse -Include *.ts,*.tsx -ErrorAction SilentlyContinue | Sort-Object FullName)
  $consoleContent = ($consoleFiles | ForEach-Object { Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue }) -join "`n"
  $consoleContent += "`n" + (Get-Content (Join-Path $RepoRoot "console\package-lock.json") -Raw -ErrorAction SilentlyContinue)
  $SourceHash = Get-StringHash ($pyContent + "`n|||`n" + $consoleContent)

  $CurrentVersion = ""
  $vf = Join-Path $RepoRoot "src\qwenpaw\__version__.py"
  if (Test-Path $vf) {
    if ((Get-Content $vf -Raw) -match '__version__\s*=\s*"([^"]+)"') { $CurrentVersion = $Matches[1] }
  }
  $existingWheels = @()
  if ($CurrentVersion) {
    $existingWheels = @(Get-ChildItem -Path (Join-Path $RepoRoot "dist\qwenpaw-$CurrentVersion-*.whl") -ErrorAction SilentlyContinue)
  }
  if ($CurrentVersion -and (Test-Path $HashMarker) -and $existingWheels.Count -gt 0) {
    $prevHash = (Get-Content $HashMarker -Raw -ErrorAction SilentlyContinue).Trim()
    if ($prevHash -eq $SourceHash) {
      Write-Host "[wheel_build] Source unchanged (hash: $($SourceHash.Substring(0,8))), skipping rebuild."
      $SkipBuild = $true
    }
  }
} catch {
  Write-Host "[wheel_build] Source hash check failed ($($_.Exception.Message)); forcing full rebuild."
  $SkipBuild = $false
  $SourceHash = $null
}

if ($SkipBuild) {
  Write-Host "[wheel_build] Done. Using existing wheel(s) in: $RepoRoot\dist\"
  exit 0
}

Write-Host "[wheel_build] Building console frontend..."
# vite build with ~15k modules exhausts Node's default ~2GB heap on Windows.
# Lift to 8GB so the production bundle doesn't OOM.
$env:NODE_OPTIONS = "--max-old-space-size=8192"
Push-Location $ConsoleDir
try {
  npm ci
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE" }
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "npm run build failed with exit code $LASTEXITCODE" }
} finally {
  Pop-Location
}

Write-Host "[wheel_build] Copying console/dist/* -> src/qwenpaw/console/..."
if (Test-Path $ConsoleDest) {
  Remove-Item -Path (Join-Path $ConsoleDest "*") -Recurse -Force -ErrorAction SilentlyContinue
} else {
  New-Item -ItemType Directory -Force -Path $ConsoleDest | Out-Null
}
$ConsoleDist = Join-Path $ConsoleDir "dist"
Copy-Item -Path (Join-Path $ConsoleDist "*") -Destination $ConsoleDest -Recurse -Force

Write-Host "[wheel_build] Building wheel + sdist..."

# Detect Python: prefer py launcher, then python3, then python
$PythonCmd = $null
foreach ($cmd in @("py", "python3", "python")) {
  $null = & $cmd --version 2>&1
  if ($LASTEXITCODE -eq 0) { $PythonCmd = $cmd; break }
}
if (-not $PythonCmd) {
  throw "Python not found. Install Python 3.8+ and ensure 'python' or 'py' is in PATH."
}
Write-Host "[wheel_build] Using Python: $PythonCmd"

& $PythonCmd -m pip install --quiet build
$DistDir = Join-Path $RepoRoot "dist"
if (-not (Test-Path $DistDir)) {
  New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
}
# Only remove prior qwenpaw wheel/sdist artifacts. Preserve QwenPaw-Portable_*
# directories and any other user content so historical portable builds survive.
Get-ChildItem -Path $DistDir -File -ErrorAction SilentlyContinue | Where-Object {
  $_.Name -match '^qwenpaw-.*\.(whl|tar\.gz|tgz|zip)$'
} | Remove-Item -Force -ErrorAction SilentlyContinue
& $PythonCmd -m build --outdir dist .
if ($LASTEXITCODE -ne 0) { throw "$PythonCmd -m build failed with exit code $LASTEXITCODE" }

# Save source hash for future cache checks
if ($SourceHash) { Set-Content -Path $HashMarker -Value $SourceHash -NoNewline }

Write-Host "[wheel_build] Done. Wheel(s) in: $RepoRoot\dist\"
