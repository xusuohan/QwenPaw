#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pylint:disable=too-many-statements,too-many-branches
"""
Create a temporary conda env, install QwenPaw from a wheel, run conda-pack.
Used by build_macos.sh and build_win.ps1. Run from repo root.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import string
import subprocess
import sys
import time
from pathlib import Path

from build_profiler import BuildProfiler

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PREFIX = "qwenpaw_pack_"

# Packages affected by conda-unpack bug on Windows (conda-pack Issue #154)
# conda-unpack modifies Python source files to replace path prefixes, but uses
# simple byte replacement without considering Python syntax. This corrupts
# string literals containing backslash escapes, causing SyntaxError.
# Example: "\\\\?\\" (correct) -> "\\" (SyntaxError: unterminated string)
# Solution: After conda-unpack, reinstall these packages to restore correct files
# See: issue.md and https://github.com/conda/conda-pack/issues/154
CONDA_UNPACK_AFFECTED_PACKAGES = [
    "huggingface_hub",  # file_download.py, _local_folder.py use Windows long path prefix
    "discord.py",       # ARG_NAME_SUBREGEX contains \\?\* which gets corrupted
]


def _conda_exe() -> str:
    """Resolve conda executable (required on Windows where 'conda' is a batch)."""
    exe = os.environ.get("CONDA_EXE")
    if exe and Path(exe).exists():
        return exe
    # On Windows, try conda.exe first (avoids batch-file resolution issues)
    if sys.platform == "win32":
        import shutil
        for name in ("conda.exe", "conda.bat", "conda.cmd", "conda"):
            if shutil.which(name):
                return name
    return "conda"


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Run command with optional environment variable overrides."""
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    # Windows: conda/pip are batch/cmd files, need shell=True to resolve
    use_shell = sys.platform == "win32"
    subprocess.run(
        cmd, cwd=cwd or REPO_ROOT, env=run_env, check=True, shell=use_shell,
    )


def _pick_wheel(wheel_arg: str | None) -> Path:
    if wheel_arg:
        wheel_path = Path(wheel_arg).expanduser()
        if not wheel_path.is_absolute():
            wheel_path = (REPO_ROOT / wheel_path).resolve()
        if not wheel_path.exists():
            raise FileNotFoundError(f"Wheel not found: {wheel_path}")
        return wheel_path

    wheels = sorted(
        (REPO_ROOT / "dist").glob("qwenpaw-*.whl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not wheels:
        raise FileNotFoundError(
            "No wheel found in dist/. Run: bash scripts/wheel_build.sh",
        )
    return wheels[0]


def _compute_env_hash(wheel_path: Path, python_version: str) -> str:
    """Compute a hash of wheel content + python version for caching."""
    h = hashlib.sha256()
    h.update(python_version.encode())
    h.update(wheel_path.read_bytes())
    return h.hexdigest()[:16]


def _find_cached_env(env_hash: str) -> str | None:
    """Find a cached conda env by hash. Returns env name or None."""
    cache_dir = REPO_ROOT / ".cache" / "conda_envs"
    marker = cache_dir / env_hash
    if marker.exists():
        env_name = marker.read_text().strip()
        # Verify env still exists
        result = subprocess.run(
            [_conda_exe(), "env", "list", "--json"],
            capture_output=True, text=True,
            check=False,
        )
        if result.returncode == 0:
            import json
            envs = json.loads(result.stdout).get("envs", [])
            for env_path in envs:
                if env_path.endswith(f"/{env_name}"):
                    return env_name
    return None


def _save_cached_env(env_hash: str, env_name: str) -> None:
    """Save env name to cache for future reuse."""
    cache_dir = REPO_ROOT / ".cache" / "conda_envs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / env_hash).write_text(env_name)


def _detect_platform() -> str:
    """Detect current platform for profiling metadata."""
    import platform as _platform
    system = _platform.system()
    machine = _platform.machine()
    if system == "Darwin":
        return f"macOS-{machine}"
    elif system == "Linux":
        return f"Linux-{machine}"
    elif system == "Windows":
        return f"Windows-{machine}"
    return f"{system}-{machine}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Conda-pack QwenPaw (temp env).",
    )
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Output archive path (e.g. .tar.gz)",
    )
    parser.add_argument(
        "--format",
        "-f",
        default="infer",
        choices=["infer", "zip", "tar.gz", "tgz"],
        help="Archive format (default: infer from --output extension)",
    )
    parser.add_argument(
        "--python",
        default="3.10",
        help="Python version for conda env (default: 3.10)",
    )
    parser.add_argument(
        "--wheel",
        default=None,
        help=(
            "Wheel path to install. If omitted, pick the newest "
            "dist/qwenpaw-*.whl."
        ),
    )
    parser.add_argument(
        "--cache-wheels",
        action="store_true",
        help=(
            "Download wheels for packages affected by conda-unpack bug. "
            "Cached to .cache/conda_unpack_wheels/ for later reinstall."
        ),
    )
    parser.add_argument(
        "--profiling-output",
        default=None,
        help="Path to write build profiling JSON report",
    )
    args = parser.parse_args()
    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wheel_path = _pick_wheel(args.wheel)
    wheel_uri = wheel_path.resolve().as_uri()

    conda = _conda_exe()

    # Check for cached environment
    env_hash = _compute_env_hash(wheel_path, args.python)
    cached_env = _find_cached_env(env_hash)
    use_cache = cached_env is not None and out_path.exists()

    if use_cache:
        assert cached_env is not None
        print(f"Using cached conda env: {cached_env}")
        env_name = cached_env
    else:
        env_name = (
            f"{ENV_PREFIX}{''.join(random.choices(string.ascii_lowercase, k=8))}"
        )

    profiler = BuildProfiler(
        platform=_detect_platform(),
        python_version=args.python,
        wheel_hash=env_hash,
        cache_hit=use_cache,
    )

    try:
        if not use_cache:
            with profiler.stage("conda_create"):
                create_env = {**os.environ, "CONDA_SOLVER": "libmamba"}
                _run(
                    [
                        conda,
                        "create",
                        "-n",
                        env_name,
                        f"python={args.python}",
                        "pip",
                        "-y",
                        "--no-default-packages",
                    ],
                    env=create_env,
                )
            # Install qwenpaw with all dependencies
            # Scope CMAKE_ARGS to this specific command to avoid affecting other
            # CMake-based packages. Only set if we need to compile from source.
            with profiler.stage("pip_install"):
                install_env = {}
                # Prevent pip from installing to user site-packages
                install_env["PYTHONNOUSERSITE"] = "1"

                pip_cmd = [
                    conda,
                    "run",
                    "-n",
                    env_name,
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--retries",
                    "3",
                    "--timeout",
                    "120",
                    f"qwenpaw[full] @ {wheel_uri}",
                ]
                _max_retries = 2
                for _attempt in range(_max_retries + 1):
                    try:
                        _run(pip_cmd, env=install_env)
                        break
                    except subprocess.CalledProcessError as e:
                        if _attempt < _max_retries:
                            print(
                                f"pip install failed (attempt {_attempt + 1}/"
                                f"{_max_retries + 1}), retrying..."
                            )
                            time.sleep(5)
                        else:
                            print(
                                f"ERROR: pip install failed after "
                                f"{_max_retries + 1} attempts. "
                                f"Exit code: {e.returncode}",
                                file=sys.stderr,
                            )
                            raise
            with profiler.stage("verify_certifi"):
                print("Verifying certifi is installed (required for SSL)...")
                _run(
                    [
                        conda,
                        "run",
                        "-n",
                        env_name,
                        "python",
                        "-c",
                        "import certifi; print(f'certifi OK: {certifi.where()}')",
                    ],
                )
            # Remove large transitive deps never imported by qwenpaw
            with profiler.stage("pip_uninstall"):
                _unused_packages = ["kubernetes", "sympy"]
                print(f"Removing unused packages: {_unused_packages}")
                _run(
                    [
                        conda,
                        "run",
                        "-n",
                        env_name,
                        "python",
                        "-m",
                        "pip",
                        "uninstall",
                        *_unused_packages,
                        "-y",
                    ],
                )
            # Clean pip cache to reduce packed size
            with profiler.stage("pip_cache_purge"):
                _run(
                    [
                        conda,
                        "run",
                        "-n",
                        env_name,
                        "python",
                        "-m",
                        "pip",
                        "cache",
                        "purge",
                    ],
                )
            if args.cache_wheels:
                # Store outside dist/ to avoid being deleted by wheel_build cleanup
                wheels_cache = REPO_ROOT / ".cache" / "conda_unpack_wheels"
                wheels_cache.mkdir(parents=True, exist_ok=True)
                print(
                    f"Caching wheels for conda-unpack bug workaround to "
                    f"{wheels_cache}",
                )
                _run(
                    [
                        conda,
                        "run",
                        "-n",
                        env_name,
                        "python",
                        "-m",
                        "pip",
                        "download",
                        *CONDA_UNPACK_AFFECTED_PACKAGES,
                        "-d",
                        str(wheels_cache),
                    ],
                )
            # pip may uninstall/reinstall files owned by conda while resolving
            # qwenpaw[full]. Restore conda-managed packaging tools before packing.
            with profiler.stage("conda_fix"):
                _run(
                    [
                        conda,
                        "run",
                        "-n",
                        env_name,
                        conda,
                        "install",
                        "-y",
                        "--force-reinstall",
                        "pip",
                        "setuptools",
                        "wheel",
                        "conda-pack",
                    ],
                )
            # Save to cache for future reuse
            _save_cached_env(env_hash, env_name)
        with profiler.stage("conda_pack"):
            if out_path.exists():
                out_path.unlink()
            pack_cmd = [
                conda,
                "run",
                "-n",
                env_name,
                "conda-pack",
                "-n",
                env_name,
                "-o",
                str(out_path),
                "-f",
            ]
            if args.format != "infer":
                pack_cmd.extend(["--format", args.format])
            pack_cmd.extend(["--compress-level", "4"])
            _run(pack_cmd)
            print(f"Packed to {out_path}")
    finally:
        # Save profiling report even when a build stage fails
        if args.profiling_output:
            profiler.save(args.profiling_output)
            print(f"Profiling report saved to {args.profiling_output}")
        # Only remove env if not cached
        if not use_cache:
            try:
                _run([conda, "env", "remove", "-n", env_name, "-y"])
            except Exception as e:
                print(f"Warning: Failed to remove temp env {env_name}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
