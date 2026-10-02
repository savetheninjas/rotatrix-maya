# SPDX-License-Identifier: GPL-3.0-or-later
"""Maya plugin lifecycle for Rotatrix."""

from __future__ import annotations

import maya.utils

from . import openaxis_handler, pen_nav, prefs, ui
from . import log as rotatrix_log
from .log import log
from .pivot import delete_legacy_marker


def on_plugin_loaded() -> None:
    maya.utils.executeDeferred(_deferred_load)


def on_plugin_unloaded() -> None:
    ui.close_panel()
    ui.delete_menu()
    pen_nav.shutdown()
    openaxis_handler.shutdown()
    log("plugin unloaded")


def _deferred_load() -> None:
    try:
        delete_legacy_marker()
        rotatrix_log.set_debug(prefs.get_bool(prefs.DEBUG_LOG))
        ui.create_menu()
        if prefs.get_bool(prefs.AUTO_CONNECT):
            openaxis_handler.get_handler().start(prefs.get_str(prefs.URL))
        if prefs.get_bool(prefs.PEN_NAV):
            pen_nav.start()
        log("plugin loaded — Rotatrix menu is in the Maya menu bar")
    except Exception as exc:
        log(f"deferred load failed: {exc!r}", "WARN")
