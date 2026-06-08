# Build a full wheel package including the latest console frontend.
# Run from repo root: pwsh -File scripts/wheel_build.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = (Get-Item $PSScriptRoot).Parent.FullName
Set-Location $RepoRoot

$ConsoleDir = Join-Path $RepoRoot "console"
$ConsoleDest = Join-Path $RepoRoot "src\qwenpaw\console"

Write-Host "[wheel_build] Building console frontend..."
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
if (Test-Path $DistDir) {
  Remove-Item -Path (Join-Path $DistDir "*") -Force -ErrorAction SilentlyContinue
}
& $PythonCmd -m build --outdir dist .
if ($LASTEXITCODE -ne 0) { throw "$PythonCmd -m build failed with exit code $LASTEXITCODE" }

Write-Host "[wheel_build] Done. Wheel(s) in: $RepoRoot\dist\"
