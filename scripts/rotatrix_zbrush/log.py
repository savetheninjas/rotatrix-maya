# SPDX-License-Identifier: GPL-3.0-or-later
"""Log helper for the Rotatrix ZBrush plugin.

Warnings print to the ZBrush Python console. Everything is appended to
%TEMP%\\RotatrixZBrush.log so a session can be diagnosed after the fact.
"""

from __future__ import annotations

import datetime
import tempfile
import threading
from pathlib import Path

LOG_FILE = Path(tempfile.gettempdir()) / "RotatrixZBrush.log"
_lock = threading.Lock()


def log(msg: str, level: str = "INFO") -> None:
    _write_file(msg, level)
    if level.upper() in ("WARN", "WARNING", "ERROR"):
        print(f"[Rotatrix] {msg}")


def _write_file(msg: str, level: str) -> None:
    try:
        now = datetime.datetime.now()
        ts = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        with _lock, open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"{ts} [{level}] {msg}\n")
    except Exception:
        pass
