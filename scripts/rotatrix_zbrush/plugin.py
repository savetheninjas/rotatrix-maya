# SPDX-License-Identifier: GPL-3.0-or-later
"""Startup for the Rotatrix ZBrush plugin."""

from __future__ import annotations

from . import host, openaxis_handler, pen_nav
from .log import log

_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    try:
        host.install()
        openaxis_handler.get_handler().start()
        pen_nav.start()
        _install_ui()
        print("[Rotatrix] ZBrush plugin loaded — hold Win and touch the canvas")
        log("plugin loaded")
    except Exception as exc:
        log(f"plugin start failed: {exc!r}", "WARN")


def status_line() -> str:
    handler = openaxis_handler.get_handler()
    return f"Trackball {handler.connection_state}. {pen_nav.status_text()}"


def _on_status(_sender: str = "") -> None:
    from zbrush import commands as zbc

    zbc.message_ok(status_line(), "Rotatrix")


def _on_free(*args) -> None:
    value = args[-1] if args else False
    mode = "free_camera" if value else "orbit"
    openaxis_handler.get_handler().set_navigation_mode(mode)


def _install_ui() -> None:
    try:
        from zbrush import commands as zbc
    except Exception as exc:
        log(f"ZBrush UI is unavailable: {exc!r}", "WARN")
        return
    palette = "ZPlugin:Rotatrix"
    try:
        if not zbc.exists("ZPlugin"):
            palette = "ZScript:Rotatrix"
    except Exception:
        palette = "ZScript:Rotatrix"
    try:
        if not zbc.exists(palette):
            zbc.add_subpalette(palette)
        if not zbc.exists(f"{palette}:Status"):
            zbc.add_button(
                f"{palette}:Status",
                "Show whether the trackball is connected.",
                _on_status,
            )
        if not zbc.exists(f"{palette}:Free Camera"):
            zbc.add_switch(
                f"{palette}:Free Camera",
                False,
                "Trackball walks while Win is held. The pen still orbits and pans.",
                _on_free,
            )
    except Exception as exc:
        log(f"could not add the Rotatrix palette: {exc!r}", "WARN")
