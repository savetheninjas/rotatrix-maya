# SPDX-License-Identifier: GPL-3.0-or-later
"""Read and write the ZBrush tool transform that acts as the camera.

ZBrush calls must happen on the UI thread. Failures are logged once so a
missing tool does not flood the console.
"""

from __future__ import annotations

import math

from rotatrix_maya.pen_math import CameraFrame

from .log import log
from .view import ToolTransform, rotation_for_camera

_read_error = ""
_write_error = ""
_drag_error = ""
_drag_ready = False
_drag_via = ""
# Off the document, same trick Turntable uses so the drag rotates and cannot sculpt.
_DRAG_ORIGIN = -4096.0
_SPEED_PATH = "Preferences:Interface:Rotate Speed"
_SHIFT_SNAP = "Preferences:Utilities:Shift"
_SDIV = "Tool:Geometry:SDiv"
_QUICK_EDIT = "Preferences:Performance:QuickAndDirtyEdit"
_deg_per_px: float | None = None
# Measured on a snapped front: degrees the background drag actually turns per pixel.
_yaw_per_px: float | None = None
_pitch_per_px: float | None = None
_doc_center: tuple[float, float] | None = None
_saved_sdiv: float | None = None
_saved_quick_edit: float | None = None


def read_tool() -> ToolTransform | None:
    global _read_error
    try:
        from zbrush import commands as zbc

        tool = ToolTransform.from_values(list(zbc.get_transform()))
    except Exception as exc:
        message = repr(exc)
        if message != _read_error:
            _read_error = message
            log(f"could not read the tool transform: {message}", "WARN")
        return None
    _read_error = ""
    return tool


def write_tool(tool: ToolTransform) -> bool:
    """Push a tool transform and ask ZBrush to redraw it."""
    global _write_error
    try:
        from zbrush import commands as zbc

        zbc.set_transform(
            x_position=float(tool.x),
            y_position=float(tool.y),
            z_position=float(tool.z),
            x_scale=float(tool.scale_x),
            y_scale=float(tool.scale_y),
            z_scale=float(tool.scale_z),
            x_rotate=float(tool.rx),
            y_rotate=float(tool.ry),
            z_rotate=float(tool.rz),
        )
        # set_transform updates the values. update() is what refreshes the
        # canvas while we are inside a timer callback.
        zbc.update(redraw_ui=False)
    except Exception as exc:
        message = repr(exc)
        if message != _write_error:
            _write_error = message
            log(f"could not set the tool transform: {message}", "WARN")
        return False
    _write_error = ""
    return True


def degrees_per_drag_pixel() -> float:
    """How many degrees ZBrush's own background drag turns per pixel.

    A drag across half the canvas is treated as a quarter turn at rotate
    speed 1, then scaled by Preferences:Interface:Rotate Speed.
    """
    global _deg_per_px
    if _deg_per_px is not None:
        return _deg_per_px
    speed = 1.0
    half = 400.0
    try:
        from zbrush import commands as zbc

        raw_speed = float(zbc.get(_SPEED_PATH))
        if 0.05 <= raw_speed <= 4.0:
            speed = raw_speed
        width = float(zbc.get("Document:Width"))
        height = float(zbc.get("Document:Height"))
        half = max(min(width, height) * 0.5, 80.0)
    except Exception:
        speed = 1.0
        half = 400.0
    _deg_per_px = (90.0 / half) * speed
    log(f"orbit uses ZBrush drag ({_deg_per_px:.3f} deg/px, rotate speed {speed:g})")
    return _deg_per_px


def axis_degrees_per_pixel() -> tuple[float, float]:
    """Yaw and pitch degrees for one pixel of background drag.

    Until a snapped front has been measured, both axes use the shared estimate.
    """
    if _yaw_per_px is not None and _pitch_per_px is not None:
        return _yaw_per_px, _pitch_per_px
    shared = degrees_per_drag_pixel()
    return shared, shared


def _wrap_degrees(degrees: float) -> float:
    return (degrees + 180.0) % 360.0 - 180.0


def is_snapped_front(tool: ToolTransform) -> bool:
    """True when the sliders are the snapped front view."""
    return (
        abs(_wrap_degrees(tool.rx)) < 1.5
        and abs(_wrap_degrees(tool.ry)) < 1.5
        and abs(_wrap_degrees(tool.rz)) < 1.5
    )


def learn_drag_scale() -> None:
    """Measure how far one drag pixel turns, from a snapped front.

    Four pixels out and back, once. The model is put back on the front
    afterwards. Later orbit steps use this so the tracked camera matches
    the picture.
    """
    global _yaw_per_px, _pitch_per_px
    if _yaw_per_px is not None and _pitch_per_px is not None:
        return
    original = read_tool()
    if original is None or not is_snapped_front(original):
        return
    yaw = _measure_axis(original, 4.0, 0.0, yaw=True)
    pitch = _measure_axis(original, 0.0, 4.0, yaw=False)
    if yaw is None or pitch is None:
        return
    _yaw_per_px = yaw
    _pitch_per_px = pitch
    log(f"drag scale from the front: yaw {yaw:.3f} deg/px, pitch {pitch:.3f} deg/px")


def _measure_axis(
    original: ToolTransform, dx: float, dy: float, *, yaw: bool,
) -> float | None:
    if not orbit_drag(dx, dy):
        write_tool(original)
        return None
    turned = read_tool()
    write_tool(original)
    if turned is None:
        return None
    delta = _wrap_degrees(turned.ry - original.ry) if yaw else _wrap_degrees(turned.rx - original.rx)
    per_px = abs(delta) / 4.0
    if per_px < 0.02 or per_px > 5.0:
        return None
    return per_px


def write_camera_rotation(frame: CameraFrame) -> bool:
    """Write the rotation of a camera that was stepped from the front."""
    tool = read_tool()
    if tool is None:
        return False
    rx, ry, rz = rotation_for_camera(frame)
    return write_tool(ToolTransform(
        tool.x, tool.y, tool.z,
        tool.scale_x, tool.scale_y, tool.scale_z,
        rx, ry, rz,
    ))


def hold_navigation_detail(active: bool) -> None:
    """Keep the low subdivision while Win is held, and restore it on release.

    A normal drag stays low until the mouse button comes up. Our strokes
    press and release every sample, so ZBrush pops the high subdivision
    back on between them. Quick-and-dirty edit is that pop, so it stays off
    until Win is released.
    """
    global _saved_sdiv, _saved_quick_edit
    try:
        from zbrush import commands as zbc
    except Exception:
        return
    if not active:
        if _saved_sdiv is not None:
            try:
                zbc.set(_SDIV, _saved_sdiv)
            except Exception:
                pass
            _saved_sdiv = None
        if _saved_quick_edit is not None:
            try:
                zbc.set(_QUICK_EDIT, _saved_quick_edit)
            except Exception:
                pass
            _saved_quick_edit = None
        return
    try:
        if _saved_quick_edit is None and zbc.exists(_QUICK_EDIT):
            _saved_quick_edit = float(zbc.get(_QUICK_EDIT))
            zbc.set(_QUICK_EDIT, 0)
        if not zbc.exists(_SDIV):
            return
        level = float(zbc.get(_SDIV))
        if _saved_sdiv is None:
            _saved_sdiv = level
        if level > 1.5:
            zbc.set(_SDIV, 1)
    except Exception:
        return


def _shift_snap_pressed(zbc) -> bool:
    try:
        return bool(zbc.get(_SHIFT_SNAP))
    except Exception:
        return False


def orbit_segments(
    dx_px: float, dy_px: float, center_x: float, center_y: float,
) -> list[tuple[float, float, float, float]]:
    """Axis-aligned drags, the same two axes as a background scribble.

    A horizontal stroke turns around Y, the up axis. A vertical stroke
    turns around X, which is the up-and-down orbit. Side-to-side stays off
    the left edge, through the vertical center. Up-and-down stays above
    the document, through the horizontal center.
    """
    dx_px = max(-2000.0, min(2000.0, dx_px))
    dy_px = max(-2000.0, min(2000.0, dy_px))
    segments: list[tuple[float, float, float, float]] = []
    if abs(dx_px) >= 0.4:
        segments.append((_DRAG_ORIGIN, center_y, _DRAG_ORIGIN + dx_px, center_y))
    if abs(dy_px) >= 0.4:
        segments.append((center_x, _DRAG_ORIGIN, center_x, _DRAG_ORIGIN + dy_px))
    return segments


def twist_should_apply(travel_degrees: float, twist_degrees: float) -> bool:
    """Apply twist only when the pen or ball is twisting in place.

    A scribble orbit stays on X and Y, the way dragging the empty canvas
    does. Barrel motion during that scribble is not a third axis.
    """
    if abs(twist_degrees) < 0.5:
        return False
    if travel_degrees >= 1.0:
        return False
    return True


def orbit_drag(dx_px: float, dy_px: float) -> bool:
    """Rotate with ZBrush's own background drag. ``dy_px`` pitches the view up."""
    return _drag_segments(lambda center: orbit_segments(dx_px, dy_px, center[0], center[1]))


def roll_drag(clockwise_degrees: float) -> bool:
    """Twist about the camera axis after the orbit stroke.

    The stroke owns sideways and up-and-down. Twist reads the pose the way
    it appears in front of a fixed camera, spins about that axis, and writes
    the sliders back. X at exactly ±90° is pulled just inside so the write
    does not flip Y.
    """
    if abs(clockwise_degrees) < 0.05:
        return False
    tool = read_tool()
    if tool is None:
        return False
    from .view import camera_twist

    return write_tool(camera_twist(tool, clockwise_degrees))


def _drag_segments(build) -> bool:
    global _drag_error, _drag_ready
    try:
        from zbrush import commands as zbc

        center = _document_center(zbc)
        segments = build(center)
        if not segments:
            return False
        snapped = _shift_snap_pressed(zbc)
        if snapped:
            zbc.set(_SHIFT_SNAP, 0)
        try:
            for x0, y0, x1, y1 in segments:
                _drag_stroke(zbc, x0, y0, x1, y1)
            zbc.update(redraw_ui=False)
        finally:
            if snapped:
                zbc.set(_SHIFT_SNAP, 1)
    except Exception as exc:
        message = repr(exc)
        if message != _drag_error:
            _drag_error = message
            log(f"could not drag the view: {message}", "WARN")
        return False
    if not _drag_ready:
        _drag_ready = True
        log("orbit is using the background drag")
    _drag_error = ""
    return True


def _document_center(zbc) -> tuple[float, float]:
    global _doc_center
    if _doc_center is not None:
        return _doc_center
    width = float(zbc.get("Document:Width"))
    height = float(zbc.get("Document:Height"))
    if width < 16.0 or height < 16.0:
        raise RuntimeError(f"document is {width:g} by {height:g}")
    _doc_center = (width * 0.5, height * 0.5)
    return _doc_center


def _drag_stroke(zbc, *coords: float) -> None:
    global _drag_via
    # Item 1004 is the canvas widget. The stroke stays off the document so
    # it rotates and cannot draw on the mesh.
    if _drag_via != "canvas":
        try:
            if _drag_via == "widget" or zbc.exists("1004"):
                zbc.click("1004", *coords)
                _drag_via = "widget"
                return
        except Exception:
            _drag_via = ""
        _drag_via = "canvas"
    zbc.canvas_click(*coords)
