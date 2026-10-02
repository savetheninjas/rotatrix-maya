# SPDX-License-Identifier: GPL-3.0-or-later
"""Wacom pen camera inside ZBrush.

Hold Win and touch the pen on the canvas:

- X/Y orbit, barrel twist rolls
- Win+Shift: X/Y pan, barrel twist zooms

The Windows key is the same one Rotatrix uses. While the trackball is
connected the key is left alone so that binding still receives it. A pen
stroke and a ball gesture take turns: the pen writes the tool, then sends
that camera back so the next ball motion continues from there.
"""

from __future__ import annotations

import math
import time

from openaxis.geometry import Vec3

from rotatrix_maya.pen_math import PIXEL_DEADZONE, TWIST_DEADZONE_DEG, apply_pen_delta, unwrap_degrees

from . import host, openaxis_handler, viewport
from .log import log
from .tablet import get_tablet
from .view import CANONICAL_ORTHO, apply_navigation, tool_to_frame

# Wintab pressure is an integer. Hover is 0. A light touch is already above this.
CONTACT_PRESSURE = 8

_navigator: PenNavigator | None = None


class PenNavigator:
    def __init__(self) -> None:
        self._win = False
        self._contact = False
        self._anchor: tuple[float, float] | None = None
        self._twist: float | None = None
        self._last_apply = 0.0
        self._stroke_dirty = False
        self._writing = False
        self.status = "Hold Win and touch the canvas"

    def tick(self) -> None:
        handler = openaxis_handler.get_handler()
        host.set_pass_win_through(handler.connection_state == "connected")
        foreground = host.zbrush_is_foreground()
        win = host.win_down() and foreground
        get_tablet().set_capture(host.main_hwnd(), win)

        if win and not self._win:
            self._anchor = None
            self._twist = None
            openaxis_handler.get_handler().adopt_front_if_snapped()
        if self._win and not win:
            self._end_stroke()
        self._win = win

        sample = get_tablet().sample
        if sample.fresh():
            tip = sample.pressure >= CONTACT_PRESSURE
            twist = sample.twist
        else:
            tip = host.tip_button_down() and foreground
            twist = None
        contact = win and tip
        if contact != self._contact:
            if not contact:
                self._end_stroke()
            self._contact = contact
            self._anchor = None
            self._twist = None
        if not contact:
            if win:
                self.status = "Win held — touch the canvas"
            else:
                self.status = "Hold Win and touch the canvas"
            return

        # The cursor stays put while the tip is down, so orbit and pan
        # have to follow the tablet packet. Twist still comes from the same packet.
        if sample.fresh() and sample.x is not None and sample.y is not None:
            x, y = sample.x, sample.y
        else:
            x, y = host.cursor_pos()
        self._consider(x, y, twist)

    def owns_camera(self) -> bool:
        if self._win and self._contact:
            return True
        return (time.monotonic() - self._last_apply) < 0.2

    def _consider(self, x: float, y: float, twist: float | None) -> None:
        if self._anchor is None:
            self._anchor = (x, y)
            self._twist = twist
            return
        dx = x - self._anchor[0]
        dy = self._anchor[1] - y
        twist_delta = 0.0
        if twist is not None and self._twist is not None:
            twist_delta = unwrap_degrees(self._twist, twist)
            self._twist = twist
        elif twist is not None:
            self._twist = twist
        self._anchor = (x, y)
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
            mode = "pan" if host.shift_down() else "orbit"
            shown = "n/a" if twist is None else f"{twist:.0f}°"
            self.status = f"Pen {mode}  twist {shown}"

    def _apply(self, dx: float, dy: float, twist_delta: float) -> bool:
        if self._writing:
            return False
        tool = viewport.read_tool()
        if tool is None:
            return False
        self._writing = True
        try:
            if not host.shift_down():
                return self._orbit(tool, dx, dy, twist_delta)
            previous = tool_to_frame(tool)
            current, ortho = apply_pen_delta(
                previous,
                Vec3(0.0, 0.0, 0.0),
                dx_px=dx,
                dy_px=dy,
                twist_degrees=twist_delta,
                orbiting=False,
                ortho_extent=CANONICAL_ORTHO,
                world_up=Vec3(0.0, 1.0, 0.0),
            )
            updated = apply_navigation(
                tool, previous, current, CANONICAL_ORTHO, ortho, keep_rotation=True,
            )
            return viewport.write_tool(updated)
        except Exception as exc:
            log(f"pen camera apply failed: {exc!r}", "WARN")
            return False
        finally:
            self._writing = False

    def _orbit(self, tool, dx: float, dy: float, twist_delta: float) -> bool:
        """Background drag for the orbit. Twist is about the fixed view.

        A drag only has X and Y. The barrel twists in place: the model's
        rotation says where the fixed view sits, and the twist is that
        view's turn written back onto the model. A scribble does not add it.
        """
        del tool
        yaw_per_px, pitch_per_px = viewport.axis_degrees_per_pixel()
        travel = math.hypot(dx * yaw_per_px, dy * pitch_per_px)
        twisting = viewport.twist_should_apply(travel, twist_delta)
        skip_orbit = twisting and abs(twist_delta) >= travel
        dragged = False
        twisted = False
        if not skip_orbit and (abs(dx) >= 0.4 or abs(dy) >= 0.4):
            dragged = viewport.orbit_drag(dx, dy)
        if twisting:
            twisted = viewport.roll_drag(twist_delta)
        if not dragged and not twisted:
            return False
        handler = openaxis_handler.get_handler()
        handler.remember_view(handler.look_after_pen(
            0.0 if skip_orbit or not dragged else dx,
            0.0 if skip_orbit or not dragged else dy,
            twist_delta if twisted else 0.0,
        ))
        return True

    def _end_stroke(self) -> None:
        self._contact = False
        self._anchor = None
        self._twist = None
        if not self._stroke_dirty:
            return
        self._stroke_dirty = False
        try:
            openaxis_handler.get_handler().send_pose_correction()
        except Exception as exc:
            log(f"pen camera correction failed: {exc!r}", "WARN")


def start() -> None:
    global _navigator
    if _navigator is not None:
        return
    _navigator = PenNavigator()
    openaxis_handler.set_camera_owner(camera_is_owned)
    log("pen camera ready — hold Win over the canvas")


def tick() -> None:
    if _navigator is not None:
        _navigator.tick()


def status_text() -> str:
    if _navigator is None:
        return "Pen camera off"
    return _navigator.status


def camera_is_owned() -> bool:
    nav = _navigator
    return nav is not None and nav.owns_camera()
