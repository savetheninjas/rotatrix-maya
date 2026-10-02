# SPDX-License-Identifier: GPL-3.0-or-later
"""Maya plugin entry point for Rotatrix OpenAxis navigation.

Maya exec()s Python plugins, so __file__ is not defined. Paths are
resolved from MFnPlugin.loadPath() and MAYA_PLUG_IN_PATH instead.

This file stays valid on Maya 2020's default Python 2.7 so it can report
that Python 3 mode is required. Maya 2020 with MAYA_PYTHON_VERSION=3,
and Maya 2022 through 2027, load the rest of the plugin.
"""

import os
import sys


def maya_useNewAPI():
    """Use Maya Python API 2.0."""


def _python_ready():
    return sys.version_info >= (3, 7)


def _warn_python():
    message = (
        "Rotatrix needs Python 3.7 or newer. "
        "Maya 2020: set MAYA_PYTHON_VERSION=3 and restart Maya. "
        "Maya 2022 through 2027 already include a new enough Python."
    )
    try:
        import maya.cmds as cmds
        cmds.warning(message)
    except Exception:
        sys.stderr.write(message + "\n")


def _add_search_paths(plugin_dir):
    import pathlib
    root = pathlib.Path(plugin_dir).resolve()
    if root.name.lower() == "plug-ins":
        root = root.parent
    for extra in (root / "lib", root / "scripts"):
        path = str(extra)
        if extra.is_dir() and path not in sys.path:
            sys.path.insert(0, path)


def _plugin_dir_from_env():
    import pathlib
    for folder in os.environ.get("MAYA_PLUG_IN_PATH", "").split(os.pathsep):
        if not folder:
            continue
        candidate = pathlib.Path(folder) / "rotatrixMaya.py"
        if candidate.is_file():
            return candidate.parent
    return None


def initializePlugin(mobject):
    if not _python_ready():
        _warn_python()
        return

    import pathlib
    import maya.api.OpenMaya as om

    plugin_fn = om.MFnPlugin(mobject, "Rotatrix", "0.1.0", "Any")
    plugin_dir = None
    try:
        loaded = plugin_fn.loadPath()
        if loaded:
            plugin_dir = pathlib.Path(loaded)
    except Exception:
        plugin_dir = None
    if plugin_dir is None:
        plugin_dir = _plugin_dir_from_env()
    if plugin_dir is None:
        plugin_dir = pathlib.Path(r"C:\Users\SeanHewitt\Documents\Rotatrix\plug-ins")
    _add_search_paths(plugin_dir)

    from rotatrix_maya.plugin import on_plugin_loaded
    on_plugin_loaded()


def uninitializePlugin(mobject):
    if not _python_ready():
        return
    import maya.api.OpenMaya as om
    from rotatrix_maya.plugin import on_plugin_unloaded

    on_plugin_unloaded()
    om.MFnPlugin(mobject)
