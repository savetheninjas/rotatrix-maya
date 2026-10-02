# SPDX-License-Identifier: GPL-3.0-or-later
"""Maya viewport read/write helpers.

All functions run on the main thread only.
"""

from __future__ import annotations

import math

import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui
import maya.cmds as cmds
from openaxis.geometry import Vec3, look_at_from_pose, pose_from_look_at
from openaxis.types import CameraPose

from .log import log
from .qtcompat import QtGui, QtWidgets, wrapInstance


def world_orientation() -> tuple[list[float], list[float]]:
    """Return (forward, up) for Maya's current world up-axis."""
    try:
        axis = cmds.upAxis(q=True, axis=True)
    except Exception:
        axis = "y"
    if axis == "z":
        return [0.0, -1.0, 0.0], [0.0, 0.0, 1.0]
    return [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]


def model_panels() -> list[str]:
    """Return every model panel, visible or not."""
    try:
        return [panel for panel in (cmds.getPanel(type="modelPanel") or []) if panel]
    except Exception:
        return []


def find_model_panel() -> str | None:
    """Return the model panel under the cursor, or the focused/visible fallback."""
    for getter in (
        lambda: cmds.getPanel(underPointer=True),
        lambda: cmds.getPanel(withFocus=True),
    ):
        try:
            panel = getter()
        except Exception:
            panel = None
        if panel and _is_model_panel(panel):
            return panel
    try:
        visible = cmds.getPanel(visiblePanels=True) or []
    except Exception:
        visible = []
    for panel in visible:
        if _is_model_panel(panel):
            return panel
    panels = cmds.getPanel(type="modelPanel") or []
    return panels[0] if panels else None


def _is_model_panel(panel: str) -> bool:
    try:
        return cmds.getPanel(typeOf=panel) == "modelPanel"
    except Exception:
        return False


def get_view(panel: str | None = None):
    """Return (view, panel, shape_path, transform_path) or Nones."""
    panel = panel or find_model_panel()
    if not panel:
        return None, None, None, None
    try:
        view = omui.M3dView.getM3dViewFromModelPanel(panel)
        shape_path = view.getCamera()
    except Exception:
        return None, panel, None, None
    xform_path = om.MDagPath(shape_path)
    try:
        if xform_path.apiType() == om.MFn.kCamera:
            xform_path.pop()
    except Exception:
        pass
    return view, panel, shape_path, xform_path


def read_viewport_pose():
    """Read the active camera and return (pose, target, backward, eye) or None."""
    view, _panel, shape_path, _xform = get_view()
    if view is None or shape_path is None:
        return None
    fn_cam = om.MFnCamera(shape_path)
    eye_p = fn_cam.eyePoint(om.MSpace.kWorld)
    target_p = fn_cam.centerOfInterestPoint(om.MSpace.kWorld)
    up_v = fn_cam.upDirection(om.MSpace.kWorld)
    view_v = fn_cam.viewDirection(om.MSpace.kWorld)

    eye = Vec3(eye_p.x, eye_p.y, eye_p.z)
    target = Vec3(target_p.x, target_p.y, target_p.z)
    up = Vec3(up_v.x, up_v.y, up_v.z)
    forward = Vec3(view_v.x, view_v.y, view_v.z)
    backward = Vec3(-forward.x, -forward.y, -forward.z)

    distance = (eye - target).length()
    if distance < 1e-6:
        target = Vec3(
            eye.x + forward.x,
            eye.y + forward.y,
            eye.z + forward.z,
        )

    fov = None
    ortho_extent = None
    if _cam_get(fn_cam, "isOrtho"):
        height = max(view.portHeight(), 1)
        width = max(view.portWidth(), 1)
        aspect = width / float(height)
        ortho_extent = _cam_get(fn_cam, "orthoWidth") / max(aspect, 1e-6)
    else:
        fov = float(_cam_get(fn_cam, "verticalFieldOfView"))

    pose = pose_from_look_at(
        eye, target, up, fov=fov, ortho_extent=ortho_extent,
    )
    return pose, (target.x, target.y, target.z), (backward.x, backward.y, backward.z), (eye.x, eye.y, eye.z)


def apply_pose(
    pose: CameraPose,
    *,
    pivot: tuple[float, float, float] | None,
) -> None:
    """Apply a server-computed camera pose to the active Maya viewport."""
    view, _panel, shape_path, xform_path = get_view()
    if view is None or shape_path is None or xform_path is None:
        return

    eye, target_pt, up = look_at_from_pose(pose, pivot=pivot)
    eye_v = om.MVector(eye.x, eye.y, eye.z)
    target_v = om.MVector(target_pt.x, target_pt.y, target_pt.z)
    up_v = om.MVector(up.x, up.y, up.z)

    direction = eye_v - target_v
    distance = direction.length()
    if distance < 1e-12:
        return

    backward = direction.normal()
    right = up_v ^ backward
    if right.length() < 1e-6:
        fallback = om.MVector(1, 0, 0) if abs(up_v.y) > 0.9 else om.MVector(0, 1, 0)
        right = fallback ^ backward
    right = right.normal()
    up_ortho = backward ^ right
    up_ortho = up_ortho.normal()

    world = om.MMatrix((
        right.x, right.y, right.z, 0.0,
        up_ortho.x, up_ortho.y, up_ortho.z, 0.0,
        backward.x, backward.y, backward.z, 0.0,
        eye.x, eye.y, eye.z, 1.0,
    ))
    local = world * xform_path.exclusiveMatrixInverse()
    fn_xform = om.MFnTransform(xform_path)
    old = fn_xform.transformation()
    scale = old.scale(om.MSpace.kObject)
    shear = old.shear(om.MSpace.kObject)
    new = om.MTransformationMatrix(local)
    new.setScale(scale, om.MSpace.kObject)
    new.setShear(shear, om.MSpace.kObject)
    fn_xform.setTransformation(new)

    fn_cam = om.MFnCamera(shape_path)
    try:
        fn_cam.setCenterOfInterest(distance)
    except Exception:
        pass

    if pose.ortho_extent is not None and _cam_get(fn_cam, "isOrtho"):
        height = max(view.portHeight(), 1)
        width = max(view.portWidth(), 1)
        aspect = width / float(height)
        _cam_set(fn_cam, "setOrthoWidth", pose.ortho_extent * aspect)

    try:
        view.refresh(True, False)
    except Exception:
        pass


def tag_redraw() -> None:
    view, _panel, _shape, _xform = get_view()
    if view is None:
        return
    try:
        view.refresh(True, False)
    except Exception:
        pass


def cursor_view_pixels(panel: str | None = None) -> tuple[int, int, object] | None:
    """Return (view_x, view_y, view) in Maya bottom-left pixels, or None."""
    view, panel, _shape, _xform = get_view(panel)
    if view is None or panel is None:
        return None
    widget = _view_widget(panel)
    if widget is None:
        return None
    pos = widget.mapFromGlobal(QtGui.QCursor.pos())
    if not widget.rect().contains(pos):
        return None
    sx = view.portWidth() / float(max(widget.width(), 1))
    sy = view.portHeight() / float(max(widget.height(), 1))
    vx = int(pos.x() * sx)
    vy = int((widget.height() - pos.y()) * sy)
    return vx, vy, view


def view_center_pixels(panel: str | None = None) -> tuple[int, int, object] | None:
    view, _panel, _shape, _xform = get_view(panel)
    if view is None:
        return None
    return view.portWidth() // 2, view.portHeight() // 2, view


def _view_widget(panel: str):
    try:
        import maya.OpenMayaUI as omui1
        view = omui1.M3dView()
        omui1.M3dView.getM3dViewFromModelPanel(panel, view)
        ptr = view.widget()
        if not ptr:
            return None
        return wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
        return None


def ray_from_view(view, x: int, y: int) -> tuple[om.MPoint, om.MVector, float] | None:
    try:
        near = om.MPoint()
        far = om.MPoint()
        view.viewToWorld(int(x), int(y), near, far)
    except Exception as exc:
        log(f"viewToWorld failed: {exc!r}", "WARN")
        return None
    direction = om.MVector(far - near)
    length = direction.length()
    if length < 1e-12:
        return None
    return near, direction.normal(), length


def _cam_get(fn_cam, name: str):
    value = getattr(fn_cam, name)
    return value() if callable(value) else value


def _cam_set(fn_cam, name: str, value) -> None:
    try:
        getattr(fn_cam, name)(value)
    except Exception:
        pass
