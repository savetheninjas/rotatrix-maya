# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenAxis client for ZBrush.

The Rotatrix app owns orbit, pan, and dolly. This handler sends the current
tool as a camera when a gesture starts and writes the poses that come back.
The socket runs on a background thread. ZBrush is only touched from the
UI-thread timer.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import threading

from openaxis.geometry import Vec3, look_at_from_pose, pose_from_look_at
from openaxis.types import DEFAULT_URL, CameraPose

try:
    from openaxis.client import OpenAxisClient, OpenAxisListener
except Exception as exc:
    OpenAxisClient = None
    _client_import_error = exc

    class OpenAxisListener(object):
        """Stand-in so the handler can still be defined."""

else:
    _client_import_error = None

from . import viewport
from .log import log
from .view import (
    CANONICAL_DISTANCE,
    CANONICAL_ORTHO,
    CameraFrame,
    advance_camera,
    apply_navigation,
    background_drag,
    screen_roll_degrees,
    tool_to_frame,
)

WORLD_FORWARD = [0.0, 0.0, -1.0]
WORLD_UP = [0.0, 1.0, 0.0]
PIVOT = (0.0, 0.0, 0.0)

_pen_owns = lambda: False


def set_camera_owner(predicate) -> None:
    """``predicate()`` true means a pen stroke currently owns the view."""
    global _pen_owns
    _pen_owns = predicate


def _transform_moved(before, after) -> bool:
    """True when pan or zoom changed. Rotation is applied by a drag, not here."""
    if abs(after.x - before.x) > 0.01 or abs(after.y - before.y) > 0.01:
        return True
    if abs(after.z - before.z) > 0.01:
        return True
    return (
        abs(after.scale_x - before.scale_x) > 1.0e-4
        or abs(after.scale_y - before.scale_y) > 1.0e-4
        or abs(after.scale_z - before.scale_z) > 1.0e-4
    )


class OpenAxisHandler(OpenAxisListener):
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._client: OpenAxisClient | None = None
        self.connection_state = "disconnected"
        self._navigation_mode = "orbit"

        self._pending: CameraPose | None = None
        self._needs_initial_pose = False
        self._current_need: list[str] = []
        self._applying = False
        self._motion_ended = False
        self._baseline: CameraFrame | None = None
        self._baseline_ortho = CANONICAL_ORTHO
        # Orientation we last showed. Re-reading ZBrush's angles does not
        # match the picture, so the next gesture continues from this instead.
        self._view: CameraFrame | None = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, url: str = DEFAULT_URL) -> None:
        if OpenAxisClient is None:
            log(
                "Rotatrix trackball needs the bundled OpenAxis client "
                f"({_client_import_error}). Pen camera still works.",
                "WARN",
            )
            return
        if self.is_running():
            return
        log(f"starting — connecting to {url}")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, args=(url,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        loop = self._loop
        client = self._client
        if loop is not None and client is not None:
            async def _shutdown():
                with contextlib.suppress(Exception):
                    await client.disconnect()
                loop.stop()

            loop.call_soon_threadsafe(loop.create_task, _shutdown())
        elif loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.connection_state = "disconnected"

    def set_navigation_mode(self, mode: str) -> None:
        if mode == self._navigation_mode:
            return
        self._navigation_mode = mode
        client = self._client
        if client is not None and self._loop is not None:
            asyncio.run_coroutine_threadsafe(client.send_tags(self._tags()), self._loop)

    def tick(self) -> None:
        try:
            self._tick()
        except Exception as exc:
            log(f"trackball tick failed: {exc!r}", "WARN")

    def send_pose_correction(self) -> None:
        """Tell Rotatrix the pen moved the view, so the ball continues from it."""
        client = self._client
        loop = self._loop
        if client is None or loop is None or self.connection_state != "connected":
            return
        tool = viewport.read_tool()
        if tool is None:
            return
        frame = self._view if self._view is not None else tool_to_frame(tool)
        pose = pose_from_look_at(
            frame.eye, frame.target, frame.up, ortho_extent=CANONICAL_ORTHO,
        )
        self._baseline = frame
        self._baseline_ortho = CANONICAL_ORTHO

        async def _send() -> None:
            await client.send_camera_pose(
                t=pose.t, r=pose.r, mode="absolute", ortho_extent=pose.ortho_extent,
            )

        asyncio.run_coroutine_threadsafe(_send(), loop)

    def on_motion_start(self, need: list[str]) -> None:
        self.adopt_front_if_snapped()
        self._current_need = list(need)
        self._motion_ended = False
        self._needs_initial_pose = True
        self._applying = True
        with self._lock:
            self._pending = None

    def on_motion_end(self) -> None:
        self._motion_ended = True

    def on_camera_pose(self, pose: CameraPose) -> None:
        with self._lock:
            self._pending = pose

    def on_profile(self, mode: str, active: list[str]) -> None:
        if mode in ("orbit", "free_camera"):
            self._navigation_mode = mode

    def _tags(self) -> list[str]:
        return ["app.zbrush", self._navigation_mode]

    def _tick(self) -> None:
        if self._needs_initial_pose:
            if self._send_initial_pose():
                self._needs_initial_pose = False
            return
        if self._motion_ended:
            self._motion_ended = False
            self._applying = False
            self._baseline = None
            return
        if _pen_owns():
            with self._lock:
                self._pending = None
            return
        with self._lock:
            pose = self._pending
            self._pending = None
        if pose is not None and self._applying:
            self._apply_pose(pose)

    def _send_initial_pose(self) -> bool:
        client = self._client
        loop = self._loop
        if client is None or loop is None:
            return False
        tool = viewport.read_tool()
        if tool is None:
            return False
        frame = self._view if self._view is not None else tool_to_frame(tool)
        self._view = frame
        pose = pose_from_look_at(
            frame.eye, frame.target, frame.up, ortho_extent=CANONICAL_ORTHO,
        )
        self._baseline = frame
        self._baseline_ortho = CANONICAL_ORTHO
        need_pivot = "camera.pivot" in self._current_need

        async def _send() -> None:
            await client.send_world_orientation(
                forward=WORLD_FORWARD, up=WORLD_UP, handedness="right",
            )
            await client.send_navigation_preferences(recenter_on_pan=False)
            await client.send_camera_pose(
                t=pose.t, r=pose.r, mode="absolute", ortho_extent=pose.ortho_extent,
            )
            if need_pivot:
                await client.send_camera_pivot(PIVOT)

        asyncio.run_coroutine_threadsafe(_send(), loop)
        return True

    def _apply_pose(self, pose: CameraPose) -> None:
        tool = viewport.read_tool()
        baseline = self._baseline
        if tool is None or baseline is None:
            return
        eye, target, up = look_at_from_pose(pose, pivot=PIVOT)
        current = CameraFrame(eye, target, up)
        updated = apply_navigation(
            tool, baseline, current, self._baseline_ortho, pose.ortho_extent,
            keep_rotation=True,
        )
        degrees = viewport.degrees_per_drag_pixel()
        dx, dy = background_drag(baseline, current, degrees)
        roll_deg = screen_roll_degrees(baseline, current)
        travel = math.hypot(dx, dy) * degrees
        twisting = viewport.twist_should_apply(travel, roll_deg)
        # A twist that only leaks a little aim would drag the model in
        # whatever direction the current view makes of that leak.
        skip_orbit = twisting and abs(roll_deg) >= travel
        turning = (abs(dx) >= 0.4 or abs(dy) >= 0.4) and not skip_orbit
        # Pan is written only when this sample does not turn, which is
        # also why the pen stays put.
        if not turning and not twisting and _transform_moved(tool, updated):
            if not viewport.write_tool(updated):
                return
        if turning and not viewport.orbit_drag(dx, dy):
            return
        if twisting and not viewport.roll_drag(roll_deg):
            return
        yaw_per_px, pitch_per_px = viewport.axis_degrees_per_pixel()
        self._baseline = current
        self._view = advance_camera(
            self._view if self._view is not None else _front_view(),
            dx if turning else 0.0,
            dy if turning else 0.0,
            roll_deg if twisting else 0.0,
            yaw_per_px,
            pitch_per_px,
        )
        if pose.ortho_extent:
            self._baseline_ortho = pose.ortho_extent

    def adopt_front_if_snapped(self) -> None:
        """A snapped front is the origin of the tracked camera."""
        tool = viewport.read_tool()
        if tool is None or not viewport.is_snapped_front(tool):
            return
        self._view = _front_view()
        self._baseline = self._view

    def look_after_pen(self, dx_px: float, dy_up: float, twist_degrees: float) -> CameraFrame:
        """Camera after this pen sample, stepped from the front view."""
        yaw_per_px, pitch_per_px = viewport.axis_degrees_per_pixel()
        frame = self._view if self._view is not None else _front_view()
        return advance_camera(frame, dx_px, dy_up, twist_degrees, yaw_per_px, pitch_per_px)

    def remember_view(self, frame: CameraFrame) -> None:
        self._view = frame

    def _run_loop(self, url: str) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        with contextlib.suppress(RuntimeError):
            self._loop.run_until_complete(self._connect_loop(url))

    async def _connect_loop(self, url: str) -> None:
        logged_failure = False
        while not self._stop_event.is_set():
            try:
                self.connection_state = "connecting"
                client = OpenAxisClient(url=url, client_name="ZBrush", listener=self)
                await client.connect()
                self.connection_state = "connected"
                logged_failure = False
                log(f"connected to {url}")
                await client.send_tags(self._tags())
                await client.send_capabilities(["3d"])
                await client.send_world_orientation(
                    forward=WORLD_FORWARD, up=WORLD_UP, handedness="right",
                )
                await client.send_navigation_preferences(recenter_on_pan=False)
                self._client = client
                while not self._stop_event.is_set() and client.state.value == "connected":
                    await asyncio.sleep(0.1)
                await client.disconnect()
                self._client = None
                self.connection_state = "disconnected"
                if not self._stop_event.is_set():
                    log("connection lost — retrying", "WARN")
            except Exception as exc:
                self._client = None
                self.connection_state = "disconnected"
                if not logged_failure:
                    log(f"connection failed: {exc!r} — retrying every 2s", "WARN")
                    logged_failure = True
            if not self._stop_event.is_set():
                await asyncio.sleep(2.0)


_handler: OpenAxisHandler | None = None


def _front_view() -> CameraFrame:
    return CameraFrame(Vec3(0.0, 0.0, CANONICAL_DISTANCE), Vec3(0.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0))


def get_handler() -> OpenAxisHandler:
    global _handler
    if _handler is None:
        _handler = OpenAxisHandler()
    return _handler


def shutdown() -> None:
    global _handler
    if _handler is not None:
        with contextlib.suppress(Exception):
            _handler.stop()
        _handler = None
