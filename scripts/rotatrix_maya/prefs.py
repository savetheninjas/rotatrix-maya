# SPDX-License-Identifier: GPL-3.0-or-later
"""Maya optionVar-backed preferences for Rotatrix."""

from __future__ import annotations

import maya.cmds as cmds

URL = "rotatrixUrl"
AUTO_CONNECT = "rotatrixAutoConnect"
NAV_MODE = "rotatrixNavMode"
PIVOT_MOUSE = "rotatrixPivotMouse"
PIVOT_SELECTION = "rotatrixPivotSelection"
PIVOT_CENTER = "rotatrixPivotCenter"
PIVOT_LAST_USED = "rotatrixPivotLastUsed"
PIVOT_FALLBACK = "rotatrixPivotFallback"
RECENTER_ON_PAN = "rotatrixRecenterOnPan"
LOCK_ROLL = "rotatrixLockRoll"
LOCK_PLANE = "rotatrixLockPlane"
WALK_SPEED = "rotatrixWalkSpeed"
DEBUG_LOG = "rotatrixDebugLog"
PEN_NAV = "rotatrixPenNav"
PEN_PROBE = "rotatrixPenProbe"

DEFAULTS = {
    URL: "ws://127.0.0.1:6607",
    AUTO_CONNECT: 1,
    NAV_MODE: "orbit",
    PIVOT_MOUSE: 1,
    PIVOT_SELECTION: 1,
    PIVOT_CENTER: 1,
    PIVOT_LAST_USED: 1,
    PIVOT_FALLBACK: "origin",
    RECENTER_ON_PAN: 0,
    LOCK_ROLL: 1,
    LOCK_PLANE: 1,
    WALK_SPEED: 5.0,
    DEBUG_LOG: 0,
    PEN_NAV: 1,
    PEN_PROBE: 0,
}


def _ensure(name: str) -> None:
    if cmds.optionVar(exists=name):
        return
    value = DEFAULTS[name]
    if isinstance(value, str):
        cmds.optionVar(sv=(name, value))
    elif isinstance(value, float):
        cmds.optionVar(fv=(name, value))
    else:
        cmds.optionVar(iv=(name, int(value)))


def get_str(name: str) -> str:
    _ensure(name)
    return str(cmds.optionVar(q=name))


def get_bool(name: str) -> bool:
    _ensure(name)
    return bool(cmds.optionVar(q=name))


def get_float(name: str) -> float:
    _ensure(name)
    return float(cmds.optionVar(q=name))


def set_str(name: str, value: str) -> None:
    cmds.optionVar(sv=(name, value))


def set_bool(name: str, value: bool) -> None:
    cmds.optionVar(iv=(name, 1 if value else 0))


def set_float(name: str, value: float) -> None:
    cmds.optionVar(fv=(name, float(value)))


def reset_visible() -> None:
    """Reset navigation/pivot prefs, leaving connection URL intact."""
    for name, value in DEFAULTS.items():
        if name in (URL, AUTO_CONNECT):
            continue
        if isinstance(value, str):
            set_str(name, value)
        elif isinstance(value, float):
            set_float(name, value)
        else:
            set_bool(name, bool(value))
