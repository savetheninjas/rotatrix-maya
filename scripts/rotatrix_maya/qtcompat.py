# SPDX-License-Identifier: GPL-3.0-or-later
"""PySide2 / PySide6 compatibility for Maya 2020–2027."""

from __future__ import annotations

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from shiboken6 import wrapInstance
except ImportError:  # Maya 2024 and earlier
    from PySide2 import QtCore, QtGui, QtWidgets
    from shiboken2 import wrapInstance


def qt_flag(*names: str):
    """Resolve Qt flags on PySide2 (Qt.Name) and PySide6 (Qt.WindowType.Name)."""
    qt = QtCore.Qt
    combined = None
    for name in names:
        value = getattr(qt, name, None)
        if value is None:
            for group_name in ("WindowType", "WindowState"):
                group = getattr(qt, group_name, None)
                if group is not None:
                    value = getattr(group, name, None)
                    if value is not None:
                        break
        if value is None:
            raise AttributeError(name)
        combined = value if combined is None else (combined | value)
    return combined


__all__ = ["QtCore", "QtGui", "QtWidgets", "wrapInstance", "qt_flag"]
