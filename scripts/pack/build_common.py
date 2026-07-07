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


# --- Domestic (CN) mirror configuration -------------------------------------
# The build pulls conda packages (python, pip, conda-pack, ...) and many PyPI
# wheels. Default to Tsinghua TUNA mirrors so builds work reliably on domestic
# networks. Set QWENPAW_CN_MIRROR=0 to fall back to upstream conda/pypi.
# Override pip only via QWENPAW_PIP_INDEX_URL / QWENPAW_PIP_TRUSTED_HOST.
_CN_CONDARC = """\
channels:
  - defaults
show_channel_urls: true
default_channels:
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/msys2
custom_channels:
  conda-forge: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud
ssl_verify: true
"""
_CN_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"

# Resolved once in main(); _run() injects it as CONDARC for every command so
# conda create/install always use the domestic channels.
_CONDARC_PATH: Path | None = None


def _cn_mirror_enabled() -> bool:
    """Whether domestic mirrors are active (on by default)."""
    return os.environ.get("QWENPAW_CN_MIRROR", "1").lower() not in (
        "0",
        "false",
        "no",
        "",
    )


def _ensure_condarc() -> Path | None:
    """Write the CN .condarc into .cache; return its path (None if disabled).

    CONDARC is set to this file for every conda invocation, overriding the
    user's ~/.condarc so the build is self-contained and reproducible.
    """
    if not _cn_mirror_enabled():
        return None
    cache_dir = REPO_ROOT / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "condarc.cn.yml"
    path.write_text(_CN_CONDARC, encoding="utf-8")
    return path


def _pip_index_args() -> list[str]:
    """pip --index-url/--trusted-host args (TUNA by default, overridable)."""
    args: list[str] = []
    index_url = os.environ.get("QWENPAW_PIP_INDEX_URL")
    if _cn_mirror_enabled() and not index_url:
        index_url = _CN_PIP_INDEX_URL
    if index_url:
        args += ["--index-url", index_url]
    trusted_host = os.environ.get("QWENPAW_PIP_TRUSTED_HOST")
    if trusted_host:
        args += ["--trusted-host", trusted_host]
    return args


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
    # Force domestic conda channels for every invocation (set in main()).
    if _CONDARC_PATH is not None:
        run_env["CONDARC"] = str(_CONDARC_PATH)
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


def _compute_deps_hash(python_version: str) -> str:
    """Fingerprint of the dependency set + python version (not qwenpaw source).

    Keys the env cache so a cached env is reused across qwenpaw source edits
    as long as its dependencies are unchanged; qwenpaw itself is reinstalled
    into the reused env (see the cache-hit branch in main). Falls back to
    hashing the whole pyproject.toml when no TOML parser is available
    (correct, just over-invalidates on version-only bumps).
    """
    pyproject = REPO_ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    h = hashlib.sha256()
    h.update(python_version.encode())
    try:
        import tomllib

        proj = tomllib.loads(text).get("project", {})
        h.update(repr(sorted(proj.get("dependencies", []))).encode())
        extras = proj.get("optional-dependencies", {})
        for key in sorted(extras):
            h.update(f"{key}={sorted(extras[key])}".encode())
    except ModuleNotFoundError:
        h.update(text.encode())
    # Fold the dependency lock (requirements/pinned.txt) into the hash so regenerating it
    # (new pinned versions) invalidates the env cache. Without this a stale env holding the
    # old versions would be reused despite the new lock, defeating the lock entirely.
    # See scripts/pack/lock_deps.sh.
    lockfile = REPO_ROOT / "requirements" / "pinned.txt"
    if lockfile.exists():
        h.update(lockfile.read_bytes())
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

    # Resolve once so every _run() injects it as CONDARC. Also prints which
    # conda source is in effect for build transparency.
    global _CONDARC_PATH
    _CONDARC_PATH = _ensure_condarc()
    if _CONDARC_PATH is not None:
        print(
            f"Using domestic conda mirror (CONDARC={_CONDARC_PATH}). "
            f"Set QWENPAW_CN_MIRROR=0 to disable."
        )

    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wheel_path = _pick_wheel(args.wheel)
    wheel_uri = wheel_path.resolve().as_uri()

    conda = _conda_exe()

    # Check for a cached environment keyed on the dependency set (NOT the
    # qwenpaw wheel bytes), so the env is reused across source edits. On a hit
    # we reinstall only qwenpaw into the reused env; only deps changes force a
    # full rebuild. QWENPAW_PACK_NO_ENV_CACHE=1 forces a full rebuild.
    env_hash = _compute_deps_hash(args.python)
    cached_env = _find_cached_env(env_hash)
    use_cache = cached_env is not None
    if os.environ.get("QWENPAW_PACK_NO_ENV_CACHE", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        print("QWENPAW_PACK_NO_ENV_CACHE set; forcing full env rebuild.")
        use_cache = False

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

                # Domestic mirror by default (TUNA); overridable via
                # QWENPAW_PIP_INDEX_URL / QWENPAW_PIP_TRUSTED_HOST.
                extra_pip_args = _pip_index_args()
                if extra_pip_args:
                    print(f"Using pip mirror args: {extra_pip_args}")

                # Apply the dependency lock so every build resolves the exact same versions —
                # no drift from pip resolving the `>=` ranges in pyproject.toml at install time.
                # Fail fast if the lock is missing: a silent fallback to fresh resolution would
                # reintroduce the very drift the lock exists to prevent. Bypass with
                # QWENPAW_PACK_NO_LOCK=1 only in an emergency.
                lockfile = REPO_ROOT / "requirements" / "pinned.txt"
                if lockfile.exists():
                    constraint_args = ["--constraint", str(lockfile.resolve())]
                    print(f"Applying dependency lock: {lockfile}")
                elif os.environ.get("QWENPAW_PACK_NO_LOCK", "").lower() in (
                    "1",
                    "true",
                    "yes",
                ):
                    constraint_args = []
                    print(
                        "WARNING: QWENPAW_PACK_NO_LOCK set — building WITHOUT the dependency "
                        "lock; pip will resolve the latest compatible versions (version drift)."
                    )
                else:
                    raise FileNotFoundError(
                        "Dependency lock requirements/pinned.txt not found. The desktop build "
                        "refuses to run without it to prevent version drift. Regenerate with: "
                        "bash scripts/pack/lock_deps.sh  (or: make lock-deps). "
                        "Set QWENPAW_PACK_NO_LOCK=1 to bypass in an emergency."
                    )

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
                    *extra_pip_args,
                    *constraint_args,
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
                        *_pip_index_args(),
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
        else:
            # Cache hit (deps unchanged): reuse the env, only update qwenpaw to
            # the freshly built wheel. --no-deps is safe because the deps hash
            # matched; network-free and seconds-fast.
            with profiler.stage("pip_reinstall_qwenpaw"):
                print(
                    f"Reusing cached env '{env_name}' (deps unchanged); "
                    f"reinstalling only qwenpaw from {wheel_path.name}"
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
                        "install",
                        "--force-reinstall",
                        "--no-deps",
                        "--retries",
                        "3",
                        "--timeout",
                        "120",
                        *_pip_index_args(),
                        f"qwenpaw @ {wheel_uri}",
                    ],
                    env={"PYTHONNOUSERSITE": "1"},
                )
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
        # Intentionally NOT deleting the conda env here.
        # The env is cached for future builds (keyed by wheel content hash).
        # Deleting it would break the cache chain — every build would be a
        # cache miss, negating the 3-9 min savings on subsequent builds.
        # To clean up old cached envs: conda env remove -n <env_name> -y
    return 0


if __name__ == "__main__":
    sys.exit(main())
