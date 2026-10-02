# SPDX-License-Identifier: GPL-3.0-or-later
"""Wacom pen camera navigation inside Maya.

Hold Win and touch the pen to a model panel:

- X/Y orbit around the current pivot, barrel twist rolls
- Win+Shift: X/Y pan, barrel twist zooms

Hover does not move the camera. The tip has to be down.

The Windows key is the same modifier Rotatrix already uses. While Rotatrix
is connected this hook only observes that key, so the trackball binding still
receives it. Pen motion and ball motion can take turns: each pen move sends
the resulting camera back to Rotatrix, and a later ball pose is left alone.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from openaxis.geometry import Vec3, pose_from_look_at

from . import openaxis_handler, prefs, viewport
from .log import log
from .pen_math import PIXEL_DEADZONE, TWIST_DEADZONE_DEG, CameraFrame, apply_pen_delta, unwrap_degrees
from .pivot import PivotEvaluator
from .qtcompat import QtCore, QtGui, QtWidgets

VK_LBUTTON = 0x01
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
# Qt pressure is 0 while the pen hovers. A light touch is already above this.
# Release is lower so a shaky light stroke does not flicker in and out of contact.
CONTACT_PRESSURE = 0.02
RELEASE_PRESSURE = 0.008
WH_KEYBOARD_LL = 13
HC_ACTION = 0
LLKHF_UP = 0x80

_navigator: PenNavigator | None = None
_hook_handle = None
_hook_proc = None
_user32 = None
# Updated on the Maya thread. The hook only reads them.
_pass_win_through = False
_arm_swallow = False
_capture_win = False


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


def _key_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def _win_down() -> bool:
    return _key_down(VK_LWIN) or _key_down(VK_RWIN)


def _user32_dll():
    global _user32
    if _user32 is None:
        library = ctypes.WinDLL("user32", use_last_error=True)
        library.SetWindowsHookExW.argtypes = [
            ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD,
        ]
        library.SetWindowsHookExW.restype = ctypes.c_void_p
        library.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        library.UnhookWindowsHookEx.restype = wintypes.BOOL
        library.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
        ]
        library.CallNextHookEx.restype = ctypes.c_ssize_t
        _user32 = library
    return _user32


def _low_level_key_proc(n_code, w_param, l_param):
    """Swallow Win over the viewport only when Rotatrix is not handling it.

    The key-down has to be eaten on the way in, or the Start menu opens
    before the next timer tick. Physical state still shows up in
    GetAsyncKeyState, which is what the navigator reads.
    """
    global _capture_win
    try:
        if n_code == HC_ACTION and not _pass_win_through:
            info = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            if info.vkCode in (VK_LWIN, VK_RWIN):
                is_up = bool(info.flags & LLKHF_UP)
                if not is_up and _arm_swallow:
                    _capture_win = True
                if _capture_win:
                    if is_up:
                        _capture_win = False
                    return 1
    except Exception:
        pass
    return _user32_dll().CallNextHookEx(None, n_code, w_param, l_param)


def _install_hook() -> None:
    global _hook_handle, _hook_proc
    if _hook_handle or sys.platform != "win32":
        return
    hook_proc = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    )
    _hook_proc = hook_proc(_low_level_key_proc)
    handle = _user32_dll().SetWindowsHookExW(
        WH_KEYBOARD_LL, ctypes.cast(_hook_proc, ctypes.c_void_p), None, 0,
    )
    if not handle:
        log(f"pen camera: keyboard hook failed (error {ctypes.get_last_error()})", "WARN")
        _hook_proc = None
        return
    _hook_handle = handle


def _remove_hook() -> None:
    global _hook_handle, _hook_proc, _arm_swallow, _pass_win_through, _capture_win
    _arm_swallow = False
    _pass_win_through = False
    _capture_win = False
    if _hook_handle:
        _user32_dll().UnhookWindowsHookEx(_hook_handle)
        _hook_handle = None
    _hook_proc = None


def _qt_event_ids(*names: str) -> set[int]:
    found: set[int] = set()
    for name in names:
        value = getattr(QtCore.QEvent, name, None)
        if value is None:
            group = getattr(QtCore.QEvent, "Type", None)
            value = getattr(group, name, None) if group is not None else None
        if value is not None:
            found.add(int(value))
    return found


def _event_is(event, name: str) -> bool:
    return int(event.type()) in _qt_event_ids(name)


def _left_button():
    qt = QtCore.Qt
    value = getattr(qt, "LeftButton", None)
    if value is None:
        group = getattr(qt, "MouseButton", None)
        value = getattr(group, "LeftButton", None) if group is not None else None
    return value


def _event_has_left(event, *, pressed: bool) -> bool:
    left = _left_button()
    if left is None:
        return False
    getter = getattr(event, "button" if pressed else "buttons", None)
    if not callable(getter):
        return False
    try:
        return bool(getter() & left)
    except Exception:
        return False


def _global_xy(event) -> tuple[float, float] | None:
    for name in ("globalPosition", "globalPosF", "globalPos"):
        getter = getattr(event, name, None)
        if getter is None:
            continue
        try:
            point = getter() if callable(getter) else getter
            return float(point.x()), float(point.y())
        except Exception:
            continue
    return None


def _has_barrel_rotation(event) -> bool:
    """True only for a rotation stylus such as the Art Pen.

    Pro Pen packets often carry azimuth in the rotation field. Using that as
    camera roll makes a sideways stroke tumble the view.
    """
    device_type = getattr(event, "deviceType", None)
    if callable(device_type):
        kind = device_type()
        label = str(kind)
        name = getattr(kind, "name", None)
        if callable(name):
            label = f"{label} {name()}"
        if "Rotation" in label:
            return True
    device = getattr(event, "device", None)
    if not callable(device):
        return False
    caps_fn = getattr(device(), "capabilities", None)
    if not callable(caps_fn):
        return False
    try:
        pointing = getattr(QtGui, "QPointingDevice", None)
        flag = getattr(getattr(pointing, "Capability", None), "Rotation", None)
        if flag is None:
            return False
        return bool(caps_fn() & flag)
    except Exception:
        return False


def _barrel_twist(event) -> float | None:
    if not _has_barrel_rotation(event):
        return None
    return _event_rotation(event)


def _event_rotation(event) -> float | None:
    getter = getattr(event, "rotation", None)
    if not callable(getter):
        return None
    try:
        return float(getter())
    except Exception:
        return None


def _event_pressure(event) -> float | None:
    getter = getattr(event, "pressure", None)
    if not callable(getter):
        return None
    try:
        return float(getter())
    except Exception:
        return None


class PenNavigator(QtCore.QObject):
    """Tracks Win/Shift and turns pen motion into camera moves."""

    def __init__(self) -> None:
        app = QtWidgets.QApplication.instance()
        super().__init__(app)
        self._tablet_types = _qt_event_ids(
            "TabletMove",
            "TabletPress",
            "TabletRelease",
            "TabletEnterProximity",
            "TabletLeaveProximity",
        )
        self._mouse_types = _qt_event_ids(
            "MouseButtonPress",
            "MouseButtonRelease",
            "MouseButtonDblClick",
            "MouseMove",
        )
        self._win = False
        self._contact = False
        self._ate_tip = False
        self._pressure = 0.0
        self._pressure_at = 0.0
        self._anchor: tuple[float, float] | None = None
        self._twist: float | None = None
        self._pivot: tuple[float, float, float] | None = None
        self._panning = False
        self._shift = False
        self._guard_pan_until = 0.0
        self._last_apply = 0.0
        self._stroke_dirty = False
        self._last_pointer = 0.0
        self._last_written: Vec3 | None = None
        self._last_pose = None
        self._last_target: tuple[float, float, float] | None = None
        self._correction_due: float | None = None
        self._writing = False
        self._last_probe = 0.0
        self._views: list = []
        self._views_at = 0.0
        self.status = "Hold Win and touch the viewport"
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)
        if app is not None:
            app.installEventFilter(self)
        openaxis_handler.add_pose_listener(self._on_server_pose)

    def shutdown(self) -> None:
        global _arm_swallow
        _arm_swallow = False
        self._timer.stop()
        self._flush_correction()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        openaxis_handler.remove_pose_listener(self._on_server_pose)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        try:
            kind = int(event.type())
            if kind in self._tablet_types:
                self._on_tablet(event)
                return False
            if kind in self._mouse_types:
                return self._on_mouse(obj, event)
        except Exception:
            pass
        return False

    def _on_tablet(self, event) -> None:
        self._note_tablet_contact(event)
        point = _global_xy(event)
        rotation = _barrel_twist(event)
        pressure = _event_pressure(event)
        if point is not None:
            self.status = self._format_status(point[0], point[1], rotation, "tablet")
            if self._contact:
                self._last_pointer = time.monotonic()
                self._consider_motion(point[0], point[1], rotation, "tablet")
        self._probe(point, _event_rotation(event), pressure, "tablet")

    def _note_tablet_contact(self, event) -> None:
        if _event_is(event, "TabletRelease") or _event_is(event, "TabletLeaveProximity"):
            self._pressure = 0.0
            self._pressure_at = time.monotonic()
            self._set_contact(False)
            return
        pressure = _event_pressure(event)
        if pressure is None:
            if _event_is(event, "TabletPress"):
                self._pressure = 1.0
                self._pressure_at = time.monotonic()
                self._set_contact(self._win)
            return
        self._pressure = pressure
        self._pressure_at = time.monotonic()
        if pressure >= CONTACT_PRESSURE:
            self._set_contact(self._win)
        elif pressure <= RELEASE_PRESSURE:
            self._set_contact(False)

    def _on_mouse(self, obj, event) -> bool:
        """Orbit on a tip-down drag, and keep that drag out of Maya's selection."""
        pressing = _event_is(event, "MouseButtonPress") or _event_is(event, "MouseButtonDblClick")
        moving = _event_is(event, "MouseMove")
        releasing = _event_is(event, "MouseButtonRelease")
        left_down = _event_has_left(event, pressed=pressing or releasing)
        if moving:
            left_down = _event_has_left(event, pressed=False)
        if not self._win or not (left_down or (releasing and self._ate_tip)):
            return False
        over_view = self._widget_in_view(obj) or self._pointer_over_view()
        if self._pressure_is_fresh():
            # Tablet pressure decides contact. Still swallow the click so Maya
            # does not select while the tip is down.
            if not self._contact or not (over_view or self._ate_tip):
                return False
            if pressing:
                self._ate_tip = True
            if releasing:
                self._ate_tip = False
            return True
        if (pressing or moving) and (over_view or self._contact):
            self._set_contact(True)
        elif releasing:
            self._set_contact(False)
        if not self._contact and not releasing:
            return False
        point = _global_xy(event)
        if point is not None and self._contact:
            self._last_pointer = time.monotonic()
            self._consider_motion(point[0], point[1], None, "pen")
        if pressing:
            self._ate_tip = True
        if releasing:
            self._ate_tip = False
        return True

    def _tick(self) -> None:
        global _pass_win_through, _arm_swallow
        handler = openaxis_handler.get_handler()
        connected = handler.connection_state == "connected"
        _pass_win_through = connected
        over_view = self._pointer_over_view()
        win = _win_down()
        _arm_swallow = over_view and not connected

        if win and not self._win:
            self._anchor = None
            self._pivot = None
            self._twist = None
            self._panning = False
            self._guard_pan_until = 0.0
        shift = _key_down(VK_SHIFT)
        if self._shift and not shift and win and self._panning:
            self._keep_panned_view()
        self._shift = shift
        if self._win and not win:
            self._flush_correction()
            self._set_contact(False)
            self._pivot = None
        self._win = win

        if not self._pressure_is_fresh():
            tip = _key_down(VK_LBUTTON) and (over_view or self._contact)
            self._set_contact(win and tip)
        elif not win:
            self._set_contact(False)

        if self._correction_due is not None and time.monotonic() >= self._correction_due:
            self._correction_due = None
            self._send_correction()

        if not win:
            return
        if not self._contact:
            if over_view:
                self.status = "Win held — touch the viewport"
            return
        if time.monotonic() - self._last_pointer < 0.05:
            return
        pos = QtGui.QCursor.pos()
        self._consider_motion(float(pos.x()), float(pos.y()), None, "cursor")
        self._probe((float(pos.x()), float(pos.y())), None, None, "cursor")

    def _consider_motion(
        self,
        x: float,
        y: float,
        rotation: float | None,
        source: str,
    ) -> None:
        if not self._win or not self._contact:
            return
        if rotation is not None and self._twist is None:
            self._twist = rotation
        if self._anchor is None:
            self._anchor = (x, y)
            if rotation is not None:
                self._twist = rotation
            self.status = self._format_status(x, y, rotation, source)
            return

        dx = x - self._anchor[0]
        dy = self._anchor[1] - y
        twist_delta = 0.0
        if rotation is not None and self._twist is not None:
            twist_delta = unwrap_degrees(self._twist, rotation)
            self._twist = rotation
        self._anchor = (x, y)
        self.status = self._format_status(x, y, rotation, source)

        moved = (
            abs(dx) >= PIXEL_DEADZONE
            or abs(dy) >= PIXEL_DEADZONE
            or abs(twist_delta) >= TWIST_DEADZONE_DEG
        )
        if not moved:
            return
        if self._apply(dx, dy, twist_delta):
            self._stroke_dirty = True
            self._last_apply = time.monotonic()

    def _apply(self, dx: float, dy: float, twist_delta: float) -> bool:
        if self._writing:
            return False
        result = viewport.read_viewport_pose()
        if result is None:
            return False
        pose, target, _backward, _eye = result
        frame = CameraFrame(
            Vec3(*pose.t),
            Vec3(*target),
            _up_from_pose(pose, target),
        )
        shift = _key_down(VK_SHIFT)
        if self._panning and not shift:
            # Orbit from where the pan left the view, not the pre-pan pivot.
            self._pivot = target
            self._panning = False
        if self._pivot is None:
            self._pivot = _resolve_pivot(target)
        self._writing = True
        try:
            frame, ortho = apply_pen_delta(
                frame,
                Vec3(*self._pivot),
                dx_px=dx,
                dy_px=dy,
                twist_degrees=twist_delta,
                orbiting=not shift,
                ortho_extent=pose.ortho_extent,
                world_up=_world_up_vec(),
            )
            updated = pose_from_look_at(
                frame.eye,
                frame.target,
                frame.up,
                fov=pose.fov,
                ortho_extent=ortho,
            )
            target_tuple = (frame.target.x, frame.target.y, frame.target.z)
            viewport.apply_pose(updated, pivot=target_tuple)
            self._last_written = frame.eye
            self._last_apply = time.monotonic()
            self._last_pose = updated
            self._last_target = target_tuple
            if shift:
                self._panning = True
        except Exception as exc:
            log(f"pen camera apply failed: {exc!r}", "WARN")
            return False
        finally:
            self._writing = False
        return True

    def _keep_panned_view(self) -> None:
        """Shift was released. Later orbit uses the camera where the pan ended."""
        result = viewport.read_viewport_pose()
        if result is not None:
            pose, target, _backward, _eye = result
            self._pivot = target
            self._last_pose = pose
            self._last_target = target
            self._last_written = Vec3(*pose.t)
        self._panning = False
        self._guard_pan_until = time.monotonic() + 0.5
        self._send_correction()

    def _restore_pen_camera(self) -> None:
        pose = self._last_pose
        target = self._last_target
        if pose is None or target is None:
            return
        self._writing = True
        try:
            viewport.apply_pose(pose, pivot=target)
        except Exception as exc:
            log(f"pen camera restore failed: {exc!r}", "WARN")
        finally:
            self._writing = False

    def _on_server_pose(self) -> None:
        """A Rotatrix pose arrived. Keep a just-finished pan if the server rewinds it."""
        if self._writing or self._last_written is None:
            return
        result = viewport.read_viewport_pose()
        if result is None:
            return
        eye = Vec3(*result[0].t)
        if (eye - self._last_written).length() <= 1e-3:
            return
        if time.monotonic() < self._guard_pan_until:
            self._restore_pen_camera()
            self._send_correction()
            return
        if self._correction_due is not None:
            self._correction_due = None
            self._anchor = None

    def _flush_correction(self) -> None:
        if self._correction_due is None:
            return
        self._correction_due = None
        self._send_correction()

    def _send_correction(self) -> None:
        try:
            openaxis_handler.get_handler().send_pose_correction()
        except Exception as exc:
            log(f"pen camera correction failed: {exc!r}", "WARN")

    def owns_camera(self) -> bool:
        """True while a pen stroke should be the only thing moving the camera."""
        if not self._win:
            return False
        if self._contact:
            return True
        return (time.monotonic() - self._last_apply) < 0.2

    def _set_contact(self, down: bool) -> None:
        down = bool(down)
        if down == self._contact:
            return
        self._contact = down
        self._anchor = None
        self._twist = None
        if not down and self._stroke_dirty:
            self._stroke_dirty = False
            self._guard_pan_until = time.monotonic() + 0.4
            self._send_correction()

    def _pressure_is_fresh(self) -> bool:
        return (time.monotonic() - self._pressure_at) < 0.15

    def _refresh_views(self) -> None:
        now = time.monotonic()
        if now - self._views_at <= 0.5:
            return
        self._views_at = now
        views = []
        for panel in viewport.model_panels():
            view = viewport._view_widget(panel)
            if view is not None:
                views.append(view)
        self._views = views

    def _widget_in_view(self, widget) -> bool:
        self._refresh_views()
        current = widget
        while current is not None:
            if current in self._views:
                return True
            parent = getattr(current, "parentWidget", None)
            current = parent() if callable(parent) else None
        return False

    def _pointer_over_view(self) -> bool:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return False
        widget = app.widgetAt(QtGui.QCursor.pos())
        if widget is None:
            return False
        self._refresh_views()
        current = widget
        while current is not None:
            if current in self._views:
                return True
            current = current.parentWidget()
        return False

    def _probe(self, point, rotation, pressure, source: str) -> None:
        if not prefs.get_bool(prefs.PEN_PROBE):
            return
        now = time.monotonic()
        if now - self._last_probe < 0.25:
            return
        self._last_probe = now
        twist = "n/a" if rotation is None else f"{rotation:.1f}"
        pressure_text = "n/a" if pressure is None else f"{pressure:.2f}"
        where = ""
        if point is not None:
            where = f" x={point[0]:.0f} y={point[1]:.0f}"
        print(f"[Rotatrix] pen {source}:{where} twist={twist} pressure={pressure_text}")

    def _format_status(self, x: float, y: float, rotation: float | None, source: str) -> str:
        twist = "n/a" if rotation is None else f"{rotation:.1f}°"
        mode = "pan" if _key_down(VK_SHIFT) else "orbit"
        return f"Pen {mode} ({source})  twist {twist}  {x:.0f},{y:.0f}"


def _up_from_pose(pose, target: tuple[float, float, float]) -> Vec3:
    from openaxis.geometry import look_at_from_pose

    _eye, _target, up = look_at_from_pose(
        pose, pivot=target,
    )
    return up


def _world_up_vec() -> Vec3:
    _forward, up = viewport.world_orientation()
    return Vec3(up[0], up[1], up[2])


def _resolve_pivot(target: tuple[float, float, float]) -> tuple[float, float, float]:
    """Orbit the framed subject, not the surface point under the pen.

    A hit on the cube's face makes a sideways stroke swing the whole object
    around that face. Selection center, then the current center of interest,
    keeps the cube in the middle of the view.
    """
    handler = openaxis_handler.get_handler()
    if handler.locked_pivot is not None:
        return handler.locked_pivot
    if prefs.get_bool(prefs.PIVOT_SELECTION):
        center = PivotEvaluator.selection_center()
        if center is not None:
            return (center.x, center.y, center.z)
    return target


def start() -> None:
    global _navigator
    if sys.platform != "win32":
        log("pen camera is Windows-only", "WARN")
        return
    if _navigator is not None:
        return
    _install_hook()
    _navigator = PenNavigator()
    log("pen camera ready — hold Win over the viewport")


def shutdown() -> None:
    global _navigator
    if _navigator is not None:
        _navigator.shutdown()
        _navigator.deleteLater()
        _navigator = None
    _remove_hook()


def status_text() -> str:
    if _navigator is None:
        return "Pen camera off"
    return _navigator.status


def is_running() -> bool:
    return _navigator is not None


def camera_is_owned() -> bool:
    """Pen stroke in progress. Rotatrix poses should not be applied."""
    nav = _navigator
    return nav is not None and nav.owns_camera()
