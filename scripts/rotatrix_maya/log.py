# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared logging helper for the Rotatrix Maya plugin.

WARN/ERROR always go to the Script Editor. INFO (motion, pivot, connect)
is silent unless Script Editor debug logging is enabled in the panel.
When debug is on, lines are also appended to <temp>/RotatrixMaya.log.
Writes are best-effort and never raise.
"""

from __future__ import annotations

import datetime
import tempfile
import threading
from pathlib import Path

LOG_FILE = Path(tempfile.gettempdir()) / "RotatrixMaya.log"
_lock = threading.Lock()
_debug = False


def set_debug(enabled: bool) -> None:
    global _debug
    _debug = bool(enabled)


def is_debug() -> bool:
    return _debug


def _write_file(msg: str, level: str) -> None:
    try:
        now = datetime.datetime.now()
        ts = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        with _lock, open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"{ts} [{level}] {msg}\n")
    except Exception:
        pass


def log(msg: str, level: str = "INFO") -> None:
    noisy = level.upper() not in ("WARN", "WARNING", "ERROR")
    if noisy and not _debug:
        return
    _write_file(msg, level)
    print(f"[Rotatrix] {msg}")
