# SPDX-License-Identifier: GPL-3.0-or-later
"""ZBrush startup entry. ZBRUSH_PLUGIN_PATH points at this directory."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _folder in (_ROOT / "scripts", _ROOT / "lib"):
    _path = str(_folder)
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    from rotatrix_zbrush.plugin import start

    start()
except Exception as exc:
    print(f"[Rotatrix] ZBrush plugin failed to start: {exc!r}")
