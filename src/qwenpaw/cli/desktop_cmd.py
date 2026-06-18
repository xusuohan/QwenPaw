# -*- coding: utf-8 -*-
"""CLI command: run QwenPaw app on a free port in a native webview window."""
# pylint:disable=too-many-branches,too-many-statements,consider-using-with
from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from typing import Any

import click

from ..constant import LOG_LEVEL_ENV
from ..utils.logging import setup_logger

try:
    import webview
except ImportError:
    webview = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class WebViewAPI:
    """API exposed to the webview for external links and file downloads."""

    def open_external_link(self, url: str) -> None:
        """Open URL in system's default browser."""
        if not url.startswith(("http://", "https://")):
            return
        webbrowser.open(url)

    def save_file(self, url: str, filename: str) -> bool:
        """Download a file from *url* and save it via a native save dialog.

        Shows the OS "Save As" dialog so the user can pick a destination,
        then downloads the file and writes it there.  This is the desktop
        equivalent of the browser's ``<a download>`` click pattern which
        pywebview/WebView2 does not support.

        Args:
            url: Full HTTP(S) URL of the file to download.
            filename: Default filename shown in the save dialog.

        Returns:
            True if the file was saved successfully, False if the user
            cancelled the dialog or an error occurred.
        """
        import re
        import shutil
        import urllib.request

        if not url.startswith(("http://", "https://")):
            return False

        # Sanitize filename: remove characters illegal on Windows
        # (< > : " / \ | ? *) and trim leading/trailing whitespace/dots.
        # Colons are common in backup names like "Backup 2026-04-22 17:36".
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", filename).strip(" .")

        try:
            # Show native OS save dialog via pywebview
            result = webview.windows[0].create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=safe_name,
            )
            if not result:
                return False  # user cancelled

            dest_path = result if isinstance(result, str) else result[0]

            # Download from the local backend and write to chosen path
            with urllib.request.urlopen(url) as response:
                with open(dest_path, "wb") as f:
                    shutil.copyfileobj(response, f)

            return True
        except Exception:
            logger.exception("save_file failed")
            return False


def _find_free_port(host: str = "127.0.0.1") -> int:
    """Bind to port 0 and return the OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.listen(1)
        return sock.getsockname()[1]


def _wait_for_http(host: str, port: int, timeout_sec: float = 300.0) -> bool:
    """Return True when something accepts TCP on host:port."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2.0)
                s.connect((host, port))
                return True
        except (OSError, socket.error):
            time.sleep(1)
    return False


def _stream_reader(in_stream, out_stream) -> None:
    """Read from in_stream line by line and write to out_stream.

    Used on Windows to prevent subprocess buffer blocking. Runs in a
    background thread to continuously drain the subprocess output.
    """
    try:
        for line in iter(in_stream.readline, ""):
            if not line:
                break
            out_stream.write(line)
            out_stream.flush()
    except Exception:
        pass
    finally:
        try:
            in_stream.close()
        except Exception:
            pass


# Module-level reference so the SIGTERM handler can reach the backend proc.
_backend_proc: subprocess.Popen | None = None
# Windows Job Object handle — closing it kills all assigned processes.
_win_job_handle: Any | None = None


def _create_win_job_object() -> Any | None:
    """Create a Windows Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.

    When the handle is closed (process exit, even crash), Windows
    automatically terminates every process assigned to the job.  Returns
    the ctypes handle on success, or ``None`` on non-Windows / failure.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        # CreateJobObjectW
        CreateJobObjectW = kernel32.CreateJobObjectW
        CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        CreateJobObjectW.restype = wintypes.HANDLE

        # SetInformationJobObject
        SetInformationJobObject = kernel32.SetInformationJobObject
        SetInformationJobObject.argtypes = [
            wintypes.HANDLE,  # hJob
            wintypes.DWORD,  # JobObjectInfoClass
            wintypes.LPVOID,  # lpJobObjectInfo
            wintypes.DWORD,  # cbJobObjectInfoLength
        ]
        SetInformationJobObject.restype = wintypes.BOOL

        # AssignProcessToJobObject
        AssignProcessToJobObject = kernel32.AssignProcessToJobObject
        AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,  # hJob
            wintypes.HANDLE,  # hProcess
        ]
        AssignProcessToJobObject.restype = wintypes.BOOL

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", wintypes.ULONGLONG),
                ("WriteOperationCount", wintypes.ULONGLONG),
                ("OtherOperationCount", wintypes.ULONGLONG),
                ("ReadTransferCount", wintypes.ULONGLONG),
                ("WriteTransferCount", wintypes.ULONGLONG),
                ("OtherTransferCount", wintypes.ULONGLONG),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job_handle = CreateJobObjectW(None, None)
        if not job_handle:
            logger.warning("CreateJobObjectW failed")
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not SetInformationJobObject(
            job_handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            logger.warning("SetInformationJobObject failed")
            kernel32.CloseHandle(job_handle)
            return None

        logger.info("Windows Job Object created with KILL_ON_JOB_CLOSE")
        return job_handle
    except Exception:
        logger.warning("Failed to create Windows Job Object", exc_info=True)
        return None


def _assign_process_to_win_job(
    job_handle: Any,
    proc: subprocess.Popen,
) -> bool:
    """Assign a subprocess to the Job Object so it is killed on close."""
    if not job_handle or sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        # OpenProcess to get a handle from PID
        PROCESS_ALL_ACCESS = 0x1F0FFF
        proc_handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, proc.pid)
        if not proc_handle:
            logger.warning("OpenProcess failed for PID %s", proc.pid)
            return False

        AssignProcessToJobObject = kernel32.AssignProcessToJobObject
        AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        AssignProcessToJobObject.restype = wintypes.BOOL

        result = AssignProcessToJobObject(job_handle, proc_handle)
        kernel32.CloseHandle(proc_handle)
        if result:
            logger.info(
                "Backend PID %s assigned to Job Object",
                proc.pid,
            )
        else:
            logger.warning(
                "AssignProcessToJobObject failed for PID %s",
                proc.pid,
            )
        return bool(result)
    except Exception:
        logger.warning(
            "Failed to assign process to Job Object",
            exc_info=True,
        )
        return False


def _kill_process_tree(proc, log) -> None:
    """Kill the backend process and its entire descendant tree.

    On Unix the child is started with ``start_new_session=True`` so that it
    and every descendant share a dedicated process group (PGID == child PID).
    ``os.killpg`` signals the whole group at once.

    On Windows we rely exclusively on ``taskkill /T`` (tree kill).  Crucially
    we must **not** call ``proc.terminate()`` or ``proc.kill()`` because those
    only terminate the single backend process — the direct child — and leave
    every grandchild (llama.cpp, MCP servers, browser, shell) orphaned.  Once
    the backend PID disappears, ``taskkill /T /F`` can no longer traverse the
    tree, so the descendants survive as zombies.

    Graceful shutdown (SIGTERM / ``taskkill /T``) is tried first; if the
    process does not exit within 5 seconds the whole tree is force-killed
    (SIGKILL / ``taskkill /F /T``).
    """
    if not proc or proc.poll() is not None:
        if proc:
            log.info("Backend already exited with code %s", proc.returncode)
        return

    is_win = sys.platform == "win32"
    log.info("Terminating backend server process tree...")

    # --- Step 1: graceful termination (SIGTERM / taskkill) ---
    if is_win:
        # taskkill /T without /F sends WM_CLOSE to every process in the
        # tree.  Console-only processes (Python, llama.cpp) ignore WM_CLOSE,
        # but GUI sub-processes (Chromium) may honour it.
        try:
            subprocess.run(
                ["taskkill", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        # NOTE: Do NOT call proc.terminate() here!  On Windows,
        # Popen.terminate() -> TerminateProcess() kills only the target PID,
        # not its children.  This breaks the parent-child chain so the
        # subsequent taskkill /T /F cannot walk the tree any more.
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, OSError) as exc:
            log.debug("killpg(SIGTERM): %s", exc)

    # --- Step 2: wait for graceful exit ---
    try:
        proc.wait(timeout=5.0)
        log.info("Backend server terminated cleanly.")
        return
    except subprocess.TimeoutExpired:
        log.warning(
            "Backend did not exit in 5 s, force killing process tree...",
        )

    # --- Step 3: force kill (SIGKILL / taskkill /F) ---
    if is_win:
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        # Same as above: no proc.kill() — taskkill /T /F already handled
        # the entire tree.
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError) as exc:
            log.debug("killpg(SIGKILL): %s", exc)

    # --- Step 4: final reap ---
    try:
        proc.wait(timeout=2.0)
        log.info("Backend server force killed.")
    except subprocess.TimeoutExpired:
        log.error("Backend process did not exit after SIGKILL!")
    except (ProcessLookupError, OSError):
        pass


def _sigterm_handler(signum, _frame):
    """On SIGTERM force-kill the backend tree immediately, then exit."""
    global _backend_proc  # noqa: PLW0603
    proc = _backend_proc
    if proc and proc.poll() is None:
        logger.warning("SIGTERM received — force killing backend tree...")
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
    # Re-raise so `finally` blocks and `atexit` handlers still run.
    raise SystemExit(128 + signum)


@click.command("desktop")
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Bind host for the app server.",
)
@click.option(
    "--log-level",
    default="info",
    type=click.Choice(
        ["critical", "error", "warning", "info", "debug", "trace"],
        case_sensitive=False,
    ),
    show_default=True,
    help="Log level for the app process.",
)
@click.option(
    "--fix-paths",
    is_flag=True,
    default=False,
    help="Rewrite stale absolute paths before starting (for portable builds).",
)
def desktop_cmd(
    host: str,
    log_level: str,
    fix_paths: bool,
) -> None:
    """Run QwenPaw app on an auto-selected free port in a webview window.

    Starts the FastAPI app in a subprocess on a free port, then opens a
    native webview window loading that URL. Use for a dedicated desktop
    window without conflicting with an existing QwenPaw app instance.
    """
    global _backend_proc, _win_job_handle  # noqa: PLW0603
    # Setup logger for desktop command (separate from backend subprocess)
    setup_logger(log_level)

    if fix_paths:
        from ..config.utils import (
            rewrite_stale_agent_json_on_disk,
            rewrite_stale_paths_on_disk,
        )

        rewrite_stale_paths_on_disk()
        rewrite_stale_agent_json_on_disk()

    port = _find_free_port(host)
    url = f"http://{host}:{port}"
    click.echo(f"Starting QwenPaw app on {url} (port {port})")
    logger.info("Server subprocess starting...")

    env = os.environ.copy()
    env[LOG_LEVEL_ENV] = log_level

    if "SSL_CERT_FILE" in env:
        cert_file = env["SSL_CERT_FILE"]
        if os.path.exists(cert_file):
            logger.info(f"SSL certificate: {cert_file}")
        else:
            logger.warning(
                f"SSL_CERT_FILE set but not found: {cert_file}",
            )
    else:
        logger.warning("SSL_CERT_FILE not set on environment")

    is_windows = sys.platform == "win32"
    proc = None
    manually_terminated = (
        False  # Track if we intentionally terminated the process
    )

    # Create a Windows Job Object so that all descendant processes are
    # automatically killed when this (parent) process exits — even on
    # crash or force-kill.  The KILL_ON_JOB_CLOSE flag makes Windows
    # terminate every process in the job the moment the job handle is
    # closed.
    if is_windows:
        _win_job_handle = _create_win_job_object()

    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "qwenpaw",
                "app",
                "--host",
                host,
                "--port",
                str(port),
                "--log-level",
                log_level,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if is_windows else sys.stdout,
            stderr=subprocess.PIPE if is_windows else sys.stderr,
            env=env,
            bufsize=1,
            universal_newlines=True,
            # Create a dedicated process group on Unix so that os.killpg
            # can terminate the *entire* descendant tree (backend + MCP
            # servers, llama.cpp, browser, etc.) in one call.
            start_new_session=not is_windows,
        )
        # Expose to SIGTERM handler
        _backend_proc = proc
        signal.signal(signal.SIGTERM, _sigterm_handler)

        # Assign backend to Windows Job Object (safety net: all descendants
        # are killed when this process exits).
        if is_windows and _win_job_handle:
            _assign_process_to_win_job(_win_job_handle, proc)
        try:
            if is_windows:
                stdout_thread = threading.Thread(
                    target=_stream_reader,
                    args=(proc.stdout, sys.stdout),
                    daemon=True,
                )
                stderr_thread = threading.Thread(
                    target=_stream_reader,
                    args=(proc.stderr, sys.stderr),
                    daemon=True,
                )
                stdout_thread.start()
                stderr_thread.start()
            logger.info("Waiting for HTTP ready...")
            if _wait_for_http(host, port):
                logger.info("HTTP ready, creating webview window...")
                api = WebViewAPI()
                webview.create_window(
                    "QwenPaw Desktop",
                    url,
                    width=1280,
                    height=800,
                    text_select=True,
                    js_api=api,
                )
                logger.info(
                    "Calling webview.start() (blocks until closed)...",
                )
                webview.start(
                    private_mode=False,
                )  # blocks until user closes the window
                logger.info("webview.start() returned (window closed).")
            else:
                logger.error("Server did not become ready in time.")
                click.echo(
                    "Server did not become ready in time; open manually: "
                    + url,
                    err=True,
                )
                try:
                    proc.wait()
                except KeyboardInterrupt:
                    pass  # will be handled in finally
        finally:
            # Ensure backend process tree is always cleaned up.
            # Restore default signal handler first to avoid re-entrancy.
            _backend_proc = None
            signal.signal(signal.SIGTERM, signal.SIG_DFL)

            if proc and proc.poll() is None:
                manually_terminated = True
            _kill_process_tree(proc, logger)

        # Only report errors if process exited unexpectedly
        # (not manually terminated)
        # On Windows, terminate() doesn't use signals so exit codes vary
        # (1, 259, etc.)
        # On Unix/Linux/macOS, terminate() sends SIGTERM (exit code -15)
        # Using a flag is more reliable than checking specific exit codes
        if proc and proc.returncode != 0 and not manually_terminated:
            logger.error(
                f"Backend process exited unexpectedly with code "
                f"{proc.returncode}",
            )
            # Follow POSIX convention for exit codes:
            # - Negative (signal): 128 + signal_number
            # - Positive (normal): use as-is
            # Example: -15 (SIGTERM) -> 143 (128+15), -11 (SIGSEGV) ->
            # 139 (128+11)
            if proc.returncode < 0:
                sys.exit(128 + abs(proc.returncode))
            else:
                sys.exit(proc.returncode or 1)
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt in main, cleaning up...")
        raise
    except Exception as e:
        logger.error(f"Exception: {e!r}")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        raise
