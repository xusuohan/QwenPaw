# -*- coding: utf-8 -*-
import logging
import os
import sys
import time

# Windows portable: some systems have corrupted certificates in the Windows
# certificate store that cause ssl.create_default_context() to raise
# [ASN1: NOT_ENOUGH_DATA].  Patch early so that aiohttp / discord.py imports
# don't crash at module level.
if sys.platform == "win32":
    import ssl as _ssl

    _orig_create_ctx = _ssl.create_default_context

    def _safe_create_default_context(*args, **kwargs):
        try:
            return _orig_create_ctx(*args, **kwargs)
        except _ssl.SSLError:
            ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
            try:
                import certifi

                ctx.load_verify_locations(certifi.where())
            except Exception:
                pass
            return ctx

    _ssl.create_default_context = _safe_create_default_context

# pylint: disable=wrong-import-position
from .utils.logging import setup_logger  # noqa: E402

# Fallback before we can safely read canonical constant definitions.
LOG_LEVEL_ENV = "QWENPAW_LOG_LEVEL"

_bootstrap_err: Exception | None = None
try:
    # Load persisted env vars before importing modules that read env-backed
    # constants at import time (e.g., WORKING_DIR).
    from .envs import load_envs_into_environ

    load_envs_into_environ()
except Exception as exc:
    # Best effort: package import should not fail if env bootstrap fails.
    _bootstrap_err = exc

_t0 = time.perf_counter()
setup_logger(os.environ.get(LOG_LEVEL_ENV, "info"))
if _bootstrap_err is not None:
    logging.getLogger(__name__).warning(
        "qwenpaw: failed to load persisted envs on init: %s",
        _bootstrap_err,
    )
logging.getLogger(__name__).debug(
    "%.3fs package init",
    time.perf_counter() - _t0,
)
