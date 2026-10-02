try:
    import maya.utils as _maya_utils

    def _rotatrix_boot():
        try:
            import maya.cmds as cmds
            loaded = cmds.pluginInfo("rotatrixMaya.py", q=True, loaded=True)
            if not loaded:
                cmds.loadPlugin("rotatrixMaya.py", quiet=True)
        except Exception as exc:
            print("[Rotatrix] autoload failed:", exc)

    _maya_utils.executeDeferred(_rotatrix_boot)
except Exception:
    pass
