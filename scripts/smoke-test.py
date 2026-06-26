#!/usr/bin/env python3
"""Standalone portable package smoke test. No third-party dependencies."""
import argparse
import re
import subprocess
import sys
from pathlib import Path

PLATFORM_DEFS = [
    ("windows/env/python.exe", "windows"),
    ("macOS/env/bin/python3", "macOS"),
    ("linux/env/bin/python3", "linux"),
]

IMPORT_CHECKS = [
    "import qwenpaw",
    "from qwenpaw.__version__ import __version__",
    "import qwenpaw.desktop",
    "import qwenpaw.config",
]

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")


def find_latest_portable(dist_dir: Path) -> Path | None:
    candidates = sorted(dist_dir.glob("QwenPaw-Portable_*"), reverse=True)
    return candidates[0] if candidates else None


def detect_python(portable_dir: Path) -> tuple[Path, str] | None:
    for rel, platform in PLATFORM_DEFS:
        p = portable_dir / rel
        if p.exists():
            return p, platform
    return None


def run_check(python: Path, code: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0, result.stdout.strip() + result.stderr.strip()
    except Exception as e:
        return False, str(e)


def collect_diagnostics(python: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for label, code in [
        ("python_version", "import sys; print(sys.version)"),
        ("pip_list", "import subprocess; subprocess.run([sys.executable, '-m', 'pip', 'list'])"),
    ]:
        ok, out = run_check(python, code)
        (output_dir / f"{label}.txt").write_text(out or "(no output)", encoding="utf-8")
    (output_dir / "path.txt").write_text(str(Path.home()) + "\n" + str(python), encoding="utf-8")


def smoke_test(portable_dir: Path, verbose: bool = False) -> bool:
    detected = detect_python(portable_dir)
    if not detected:
        print(f"FAIL: No python executable found in {portable_dir}")
        return False
    python, platform = detected
    print(f"Platform: {platform}")
    print(f"Python:   {python}")

    for check in IMPORT_CHECKS:
        ok, out = run_check(python, check)
        if not ok:
            print(f"FAIL: {check}")
            if verbose:
                print(f"  {out}")
            diag_dir = portable_dir.parent / "diagnostics"
            collect_diagnostics(python, diag_dir)
            (diag_dir / "smoke_test_output.txt").write_text(
                f"Failed: {check}\n{out}", encoding="utf-8"
            )
            print(f"Diagnostics saved to {diag_dir}")
            return False

    ok, version = run_check(python, "from qwenpaw.__version__ import __version__; print(__version__)")
    if not ok or not SEMVER_RE.match(version):
        print(f"FAIL: Invalid version '{version}'")
        return False

    print(f"Version:  {version}")
    print("PASS: All smoke test checks passed")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Portable package smoke test")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--portable-dir", type=Path, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.portable_dir:
        portable = args.portable_dir
    else:
        portable = find_latest_portable(args.dist_dir)
        if not portable:
            print(f"FAIL: No QwenPaw-Portable_* found in {args.dist_dir}")
            return 1

    print(f"Testing:  {portable}")
    return 0 if smoke_test(portable, verbose=args.verbose) else 1


if __name__ == "__main__":
    sys.exit(main())
