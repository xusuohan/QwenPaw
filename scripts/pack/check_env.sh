#!/usr/bin/env bash
# Check build environment dependencies. Source this file or run directly.
# Returns 0 if all required tools are found, 1 otherwise.

_MISSING=0

_check() {
  local cmd="$1"
  local name="${2:-$1}"
  local hint="${3:-}"
  if command -v "$cmd" &>/dev/null; then
    local ver
    ver=$("$cmd" --version 2>/dev/null | head -1)
    echo "  [OK] $name: $ver"
  else
    echo "  [MISSING] $name ('$cmd' not found in PATH)"
    if [[ -n "$hint" ]]; then
      echo "           -> $hint"
    fi
    _MISSING=1
  fi
}

echo "== Checking build environment =="

_check python3 "Python 3" "Install Python 3.8+: https://www.python.org/downloads/"
_check conda "Conda" "Install Miniconda: https://docs.conda.io/en/latest/miniconda.html"
_check node "Node.js" "Install Node.js 18+: https://nodejs.org/"
_check npm "npm" "Comes with Node.js. If missing, reinstall Node.js."

if [[ "$_MISSING" -ne 0 ]]; then
  echo ""
  echo "ERROR: Missing required tools. Please install them before building."
  return 1 2>/dev/null || exit 1
fi

echo "== All build dependencies found =="
return 0 2>/dev/null || exit 0
