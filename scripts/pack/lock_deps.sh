#!/usr/bin/env bash
# Regenerate the dependency lock (requirements/pinned.txt) for the desktop/conda-pack build.
#
# Locks every dependency of qwenpaw[full] — direct AND transitive — to exact versions, so the
# packed desktop binaries are reproducible: two builds of the same source yield the same versions.
# Without this, `pip install qwenpaw[full] @ wheel` resolves the `>=` ranges in pyproject.toml
# against whatever is newest on PyPI at build time → version drift between builds.
#
# The lock is compiled from a *built wheel's* metadata rather than pyproject.toml directly,
# because the project version is dynamic (qwenpaw.__version__) and uv can't evaluate that
# statically; the wheel pins a concrete version. The root `qwenpaw==` line is then stripped —
# qwenpaw's own version comes from __version__.py and bumps independently on dev builds, so
# leaving it in the constraint would make pip refuse the locally-built wheel.
#
# When to re-run: after changing ANY dependency in pyproject.toml. The desktop build
# (build_common.py) fails fast if this file is missing or stale; set QWENPAW_PACK_NO_LOCK=1
# to bypass that check in an emergency (NOT recommended — it reintroduces drift).
#
# Run from repo root:  bash scripts/pack/lock_deps.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "[lock_deps] ERROR: uv not found in PATH." >&2
  echo "             Install uv: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

# 1. Ensure a wheel exists — the lock must match what we actually ship.
shopt -s nullglob
WHEELS=(dist/qwenpaw-*.whl)
shopt -u nullglob
if [ "${#WHEELS[@]}" -eq 0 ]; then
  echo "[lock_deps] No wheel in dist/; building one first via wheel_build.sh..."
  bash scripts/wheel_build.sh
fi
# Newest wheel by mtime (mirrors build_common.py::_pick_wheel).
WHEEL="$(ls -t dist/qwenpaw-*.whl | head -1)"
WHEEL_ABS="$(cd "$(dirname "$WHEEL")" && pwd)/$(basename "$WHEEL")"
WHEEL_URI="file://${WHEEL_ABS}"
echo "[lock_deps] Compiling lock from wheel: $(basename "$WHEEL")"

# 2. Compile the full dependency tree for Python 3.10 (matches the conda-pack build env).
#    uv reads the wheel's METADATA for qwenpaw[full]'s requirements, resolves the graph against
#    the index (default PyPI; the TUNA mirror the build uses mirrors it verbatim) and prints
#    pinned versions. --no-emit-package qwenpaw excludes the root package from the output
#    (its version comes from __version__.py and would otherwise fight the local wheel); uv pip
#    compile does not accept an inline requirement string as an argument, hence the temp .in.
TMP_IN="$(mktemp -t qwenpaw_lock.XXXXXX).in"
trap 'rm -f "$TMP_IN"' EXIT
echo "qwenpaw[full] @ ${WHEEL_URI}" > "$TMP_IN"

OUT="$REPO_ROOT/requirements/pinned.txt"
mkdir -p "$(dirname "$OUT")"

# Stable, hand-written header (this file is committed; --no-header avoids uv recording the
# throwaway temp .in path, and the command would differ across machines anyway).
{
  echo "# Dependency lock for the desktop/conda-pack build (scripts/pack/build_common.py)."
  echo "# Pins qwenpaw[full] -- direct + transitive -- to exact versions for reproducible binaries."
  echo "# qwenpaw itself is excluded: it is installed from a freshly built wheel, whose version"
  echo "# comes from src/qwenpaw/__version__.py and bumps independently of this lock."
  echo "#"
  echo "# Regenerate after changing any dependency in pyproject.toml:"
  echo "#     bash scripts/pack/lock_deps.sh        # or:  make lock-deps"
} > "$OUT"

uv pip compile \
  --no-header \
  --no-emit-package qwenpaw \
  --python-version 3.10 \
  "$TMP_IN" \
  >> "$OUT"

COUNT=$(grep -c -E '^[^#[:space:]]' "$OUT" || true)
echo "[lock_deps] Wrote $OUT ($COUNT packages pinned)."
echo "[lock_deps] Review the diff before committing:  git diff $OUT"
