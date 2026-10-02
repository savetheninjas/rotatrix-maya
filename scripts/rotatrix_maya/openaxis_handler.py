# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenAxis v0.2 handler for Maya — thin client.

The Rotatrix app owns all camera math (orbit, pan, dolly). This handler
shuttles viewport state between Maya and the local OpenAxis server.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time

from openaxis.types import DEFAULT_URL, CameraPose

# websockets 16 (pulled in by the OpenAxis client) needs Python 3.9+.
# Maya 2020 in Python 3 mode and Maya 2022 are 3.7, so the pen camera
# must still load when that import fails.
_client_import_error = None
try:
    from openaxis.client import OpenAxisClient, OpenAxisListener
except Exception as exc:
    OpenAxisClient = None
    _client_import_error = exc

    class OpenAxisListener(object):
        """Stand-in so the handler class can still be defined."""

from . import prefs, viewport
from .log import log
from .perf_stats import PerfStats
from .pivot import PivotEvaluator, evaluate_pivot
from .qtcompat import QtCore


class OpenAxisHandler(OpenAxisListener):
    """Manages the OpenAxis connection and applies server-computed poses."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._client: OpenAxisClient | None = None
        self.connection_state: str = "disconnected"
        self.pivot = PivotEvaluator()
        self._stats = PerfStats()

        self._pending: CameraPose | None = None
        self._needs_initial_pose = False
        self._current_need: list[str] = []
        self._motion_active = False
        self._motion_ended = False
        self._server_pivot: tuple[float, float, float] | None = None
        self._locked_pivot: tuple[float, float, float] | None = None
        self._recenter_on_pan: bool = False
        self._applying: bool = False
        self._last_sent_locks: tuple[bool, bool] | None = None
        self._last_sent_walk_speed: float | None = None
        self._navigation_mode: str = prefs.get_str(prefs.NAV_MODE)

        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._timer_tick)

    @property
    def has_locked_pivot(self) -> bool:
        return self._locked_pivot is not None

    @property
    def locked_pivot(self) -> tuple[float, float, float] | None:
        return self._locked_pivot

    @property
    def server_pivot(self) -> tuple[float, float, float] | None:
        return self._server_pivot

    @property
    def navigation_mode(self) -> str:
        return self._navigation_mode

    @property
    def lock_roll(self) -> bool:
        return prefs.get_bool(prefs.LOCK_ROLL)

    @property
    def lock_translation_plane(self) -> bool:
        return prefs.get_bool(prefs.LOCK_PLANE)

    @property
    def last_motion_stats(self) -> dict | None:
        return self._stats.last_report

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, url: str = DEFAULT_URL) -> None:
        if OpenAxisClient is None:
            log(
                "Rotatrix trackball connection needs Python 3.9 or newer "
                "(Maya 2023–2027). Pen camera still works. "
                f"({_client_import_error})",
                "WARN",
            )
            return
        if self.is_running():
            return
        log(f"starting — connecting to {url}")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, args=(url,), daemon=True,
        )
        self._thread.start()
        self._timer.start(2)

    def stop(self) -> None:
        log("stopping — disconnecting")
        self._timer.stop()
        self._stop_event.set()
        loop = self._loop
        client = self._client
        if loop and client:
            async def _shutdown():
                with contextlib.suppress(Exception):
                    await client.disconnect()
                loop.stop()
            loop.call_soon_threadsafe(loop.create_task, _shutdown())
        elif loop:
            loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.connection_state = "disconnected"

    def set_locked_pivot(self) -> bool:
        pt = PivotEvaluator.selection_center()
        if pt is None:
            return False
        self._locked_pivot = (pt.x, pt.y, pt.z)
        self._server_pivot = self._locked_pivot
        self._sync_recenter_on_pan()
        return True

    def clear_locked_pivot(self) -> None:
        self._locked_pivot = None
        self._sync_recenter_on_pan()

    def _sync_recenter_on_pan(self) -> None:
        value = False if self._locked_pivot is not None else self._recenter_on_pan
        client = self._client
        if client and self._loop:
            asyncio.run_coroutine_threadsafe(
                client.send_navigation_preferences(recenter_on_pan=value),
                self._loop,
            )

    def _build_tags(self) -> list[str]:
        return ["app.maya", self.navigation_mode]

    def set_navigation_mode(self, mode: str) -> None:
        if mode == self._navigation_mode:
            return
        self._navigation_mode = mode
        prefs.set_str(prefs.NAV_MODE, mode)
        client = self._client
        if client and self._loop:
            asyncio.run_coroutine_threadsafe(
                client.send_tags(self._build_tags()),
                self._loop,
            )

    def set_lock_preferences(self, lock_roll: bool, lock_translation_plane: bool) -> None:
        prefs.set_bool(prefs.LOCK_ROLL, lock_roll)
        prefs.set_bool(prefs.LOCK_PLANE, lock_translation_plane)
        self._send_lock_preferences()

    def _send_lock_preferences(self) -> None:
        is_free_camera = self._navigation_mode == "free_camera"
        lock_roll = self.lock_roll if is_free_camera else False
        lock_plane = self.lock_translation_plane if is_free_camera else False
        locks = (lock_roll, lock_plane)
        if locks == self._last_sent_locks:
            return
        self._last_sent_locks = locks
        client = self._client
        if client and self._loop:
            asyncio.run_coroutine_threadsafe(
                client.send_navigation_preferences(
                    recenter_on_pan=self._recenter_on_pan,
                    lock_roll=lock_roll,
                    lock_translation_plane=lock_plane,
                ),
                self._loop,
            )

    def _sync_walk_speed(self) -> None:
        client = self._client
        if client is None or self._loop is None:
            return
        if self._navigation_mode == "orbit":
            if self._last_sent_walk_speed is not None:
                self._last_sent_walk_speed = None
                asyncio.run_coroutine_threadsafe(
                    client.send_translation_scale(mode="viewport"),
                    self._loop,
                )
            return
        speed = prefs.get_float(prefs.WALK_SPEED)
        if speed != self._last_sent_walk_speed:
            self._last_sent_walk_speed = speed
            asyncio.run_coroutine_threadsafe(
                client.send_translation_scale(mode="absolute", translation_scale=speed),
                self._loop,
            )

    def _timer_tick(self) -> None:
        if self._stop_event.is_set():
            self._timer.stop()
            return

        self._sync_scene_settings()

        if self._applying and self.connection_state != "connected":
            self._motion_active = False
            self._motion_ended = False
            self._applying = False
            viewport.tag_redraw()
            return

        if self._needs_initial_pose:
            self._needs_initial_pose = False
            self._applying = True
            self._send_initial_pose()
            self._stats.reset()
            return

        if self._motion_ended:
            self._motion_ended = False
            self._applying = False
            self._stats.finalize()
            viewport.tag_redraw()
            return

        with self._lock:
            pose = self._pending
            self._pending = None

        if pose is not None and self._applying:
            pre = time.perf_counter()
            self._apply_pose(pose)
            self._stats.record_frame(time.perf_counter() - pre, pose.t_send)
            self._stats.maybe_push()

    def _sync_scene_settings(self) -> None:
        recenter = prefs.get_bool(prefs.RECENTER_ON_PAN)
        if recenter != self._recenter_on_pan:
            self._recenter_on_pan = recenter
            self._sync_recenter_on_pan()
        self._sync_walk_speed()
        self._send_lock_preferences()

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
                client = OpenAxisClient(
                    url=url,
                    client_name="Maya",
                    listener=self,
                )
                await client.connect()
                self.connection_state = "connected"
                logged_failure = False
                log(f"connected to {url}")

                forward, up = viewport.world_orientation()
                await client.send_tags(self._build_tags())
                await client.send_capabilities(["3d"])
                await client.send_world_orientation(
                    forward=forward, up=up, handedness="right",
                )
                await client.send_navigation_preferences(
                    recenter_on_pan=self._recenter_on_pan,
                )
                self._client = client

                while (
                    not self._stop_event.is_set()
                    and client.state.value == "connected"
                ):
                    await asyncio.sleep(0.1)

                await client.disconnect()
                self._client = None
                self.connection_state = "disconnected"
                if not self._stop_event.is_set():
                    log("connection lost — server closed; retrying", "WARN")
            except Exception as exc:
                self._client = None
                self.connection_state = "disconnected"
                if not logged_failure:
                    log(f"connection failed: {exc!r} — retrying every 2s", "WARN")
                    logged_failure = True
            if not self._stop_event.is_set():
                await asyncio.sleep(2.0)

    def on_profile(self, mode: str, active: list[str]) -> None:
        if mode in ("orbit", "free_camera") and mode != self._navigation_mode:
            self._navigation_mode = mode

    def on_motion_start(self, need: list[str]) -> None:
        log("motion start")
        self._motion_active = True
        self._current_need = need
        self._motion_ended = False
        self._needs_initial_pose = True
        with self._lock:
            self._pending = None

    def on_motion_end(self) -> None:
        log("motion end")
        self._motion_active = False
        self._motion_ended = True

    def on_camera_pivot(self, point: tuple[float, float, float]) -> None:
        self._server_pivot = point

    def on_camera_pose(self, pose: CameraPose) -> None:
        with self._lock:
            self._pending = pose

    def _send_initial_pose(self) -> None:
        client = self._client
        if client is None:
            return
        result = viewport.read_viewport_pose()
        if result is None:
            return
        pose, target, backward, eye = result

        need_pivot = "camera.pivot" in self._current_need
        pivot_tuple = None
        if need_pivot:
            settings = {
                "pivot_mouse": prefs.get_bool(prefs.PIVOT_MOUSE),
                "pivot_selection": prefs.get_bool(prefs.PIVOT_SELECTION),
                "pivot_center": prefs.get_bool(prefs.PIVOT_CENTER),
                "pivot_last_used": prefs.get_bool(prefs.PIVOT_LAST_USED),
                "pivot_fallback": prefs.get_str(prefs.PIVOT_FALLBACK),
            }
            pivot_tuple = evaluate_pivot(
                target, self._locked_pivot, self._server_pivot,
                self.pivot, settings,
            )
            px, py, pz = pivot_tuple
            fx, fy, fz = -backward[0], -backward[1], -backward[2]
            forward_dist = (
                (px - eye[0]) * fx + (py - eye[1]) * fy + (pz - eye[2]) * fz
            )
            if forward_dist <= 0:
                log(f"pivot behind eye, pushing forward (dist={forward_dist:.4f})")
                push = 0.001 - forward_dist
                pivot_tuple = (px + fx * push, py + fy * push, pz + fz * push)
            self._server_pivot = pivot_tuple

        recenter = False if self._locked_pivot is not None else self._recenter_on_pan
        is_free_camera = self._navigation_mode == "free_camera"
        walk_speed = prefs.get_float(prefs.WALK_SPEED) if is_free_camera else None
        lock_roll = self.lock_roll if is_free_camera else None
        lock_plane = self.lock_translation_plane if is_free_camera else None
        forward, up = viewport.world_orientation()

        async def _send():
            await client.send_world_orientation(
                forward=forward, up=up, handedness="right",
            )
            await client.send_navigation_preferences(
                recenter_on_pan=recenter,
                lock_roll=lock_roll,
                lock_translation_plane=lock_plane,
            )
            self._last_sent_locks = (lock_roll, lock_plane) if is_free_camera else None
            if walk_speed is not None:
                await client.send_translation_scale(
                    mode="absolute", translation_scale=walk_speed,
                )
                self._last_sent_walk_speed = walk_speed
            await client.send_camera_pose(
                t=pose.t, r=pose.r, mode="absolute",
                fov=pose.fov, ortho_extent=pose.ortho_extent,
            )
            if pivot_tuple is not None:
                await client.send_camera_pivot(pivot_tuple)

        if self._loop:
            asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def send_pose_correction(self) -> None:
        """Tell Rotatrix the viewport camera moved outside the trackball.

        Used after a pen gesture so the next ball motion continues from the
        camera the pen left behind. No-op unless the server is connected.
        """
        client = self._client
        loop = self._loop
        if client is None or loop is None or self.connection_state != "connected":
            return
        result = viewport.read_viewport_pose()
        if result is None:
            return
        pose = result[0]

        async def _send() -> None:
            await client.send_camera_pose(
                t=pose.t,
                r=pose.r,
                mode="absolute",
                fov=pose.fov,
                ortho_extent=pose.ortho_extent,
            )

        asyncio.run_coroutine_threadsafe(_send(), loop)

    def _apply_pose(self, pose: CameraPose) -> None:
        # A pen stroke owns the camera. Applying the trackball pose at the
        # same time shakes the view for the first moments of the stroke.
        from . import pen_nav
        if pen_nav.camera_is_owned():
            return
        pivot = self._server_pivot if self._navigation_mode == "orbit" else None
        viewport.apply_pose(pose, pivot=pivot)
        for listener in list(_pose_listeners):
            try:
                listener()
            except Exception:
                log("pose listener failed", "WARN")


_handler: OpenAxisHandler | None = None
_pose_listeners: list = []


def add_pose_listener(callback) -> None:
    """Notify ``callback`` after a Rotatrix pose is applied to the viewport."""
    if callback not in _pose_listeners:
        _pose_listeners.append(callback)


def remove_pose_listener(callback) -> None:
    try:
        _pose_listeners.remove(callback)
    except ValueError:
        pass


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
