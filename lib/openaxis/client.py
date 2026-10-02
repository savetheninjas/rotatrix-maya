"""OpenAxis v0.2 WebSocket client."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

import msgpack
import websockets
from websockets.asyncio.client import ClientConnection

from .types import (
    DEFAULT_URL,
    PROTO_VERSION,
    Buttons,
    CameraPivot,
    CameraPose,
    Capabilities,
    ConnectionState,
    Error,
    Ext,
    Frame,
    Handedness,
    Heartbeat,
    Hello,
    HelloAck,
    MotionEnd,
    MotionStart,
    Msg,
    NavigationMode,
    NavigationPreferencesMsg,
    ObjectPivot,
    ObjectPose,
    PoseMode,
    Profile,
    Subscribe,
    Tags,
    TranslationScale,
    TranslationScaleMode,
    TranslationScaleMsg,
    WorldOrientation,
    WorldOrientationMsg,
    unpack_msg,
)
from .types import (
    Axes as AxesMsg,
)

__all__ = [
    "OpenAxisClient",
    "OpenAxisListener",
    "CallbackHandler",
    "add_log_callback",
]

logger = logging.getLogger("openaxis")


class CallbackHandler(logging.Handler):
    """Logging handler that forwards formatted records to a callback."""

    def __init__(self, callback: Callable[[str], None], level: int = logging.DEBUG):
        super().__init__(level)
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            self._callback(self.format(record))


def add_log_callback(callback: Callable[[str], None], level: int = logging.DEBUG) -> CallbackHandler:
    """Route the ``openaxis`` logger to a callback (e.g. Fusion's text palette).

    Returns the handler so callers can later ``logger.removeHandler(h)``
    if needed.
    """
    handler = CallbackHandler(callback, level)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(min(logger.level or logging.WARNING, level))
    return handler


class OpenAxisListener:
    """Base class for handling server messages. Override the methods you need."""

    def on_frame(self, frame: Frame) -> None: pass
    def on_buttons(self, value: int) -> None: pass
    def on_motion_start(self, need: list[str]) -> None: pass
    def on_motion_end(self) -> None: pass
    def on_profile(self, mode: NavigationMode, active: list[str]) -> None: pass
    def on_camera_pose(self, pose: CameraPose) -> None: pass
    def on_camera_pivot(self, point: tuple[float, float, float]) -> None: pass
    def on_object_pose(self, pose: ObjectPose) -> None: pass
    def on_object_pivot(self, point: tuple[float, float, float]) -> None: pass
    def on_axes(self, axes: list[str]) -> None: pass
    def on_ext(self, name: str, id: int, data: dict | None) -> None: pass
    def on_state_change(self, state: ConnectionState) -> None: pass
    def on_error(self, code: str, message: str) -> None: pass


class OpenAxisClient:
    """Async WebSocket client for the OpenAxis v0.2 protocol.

    All messages use msgpack binary framing. Supports 3D service capabilities
    where the server handles camera navigation.

    Basic usage::

        class MyListener(OpenAxisListener):
            def on_frame(self, frame):
                print(frame.values)

        client = OpenAxisClient(client_name="my-app", listener=MyListener())
        await client.connect()
        await client.send_tags(["app:myapp", "type:viewer"])
        await client.send_capabilities(["3d"])
        # ... later ...
        await client.disconnect()
    """

    def __init__(
        self,
        *,
        url: str = DEFAULT_URL,
        client_name: str,
        listener: OpenAxisListener | None = None,
    ):
        self._url = url
        self._client_name = client_name
        self._listener = listener or OpenAxisListener()

        self._state = ConnectionState.DISCONNECTED
        self._ws: ClientConnection | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._receive_task: asyncio.Task[None] | None = None
        # Serialize all writes. The connection has multiple concurrent writers
        # — the heartbeat loop and any task calling a send_* method (e.g. a
        # listener responding to motion_start / camera.pose). The underlying
        # websockets connection does not serialize writes, and two coroutines
        # draining the same connection at once trips an internal assertion.
        self._send_lock = asyncio.Lock()

    # -- Public properties --

    @property
    def state(self) -> ConnectionState:
        return self._state

    # -- Connection lifecycle --

    async def connect(self) -> None:
        """Connect to the OpenAxis server and complete the hello handshake."""
        if self._state != ConnectionState.DISCONNECTED:
            raise RuntimeError(f"Cannot connect from state {self._state.value}")

        self._set_state(ConnectionState.CONNECTING)
        try:
            # ping_interval=None disables the transport-level keepalive ping.
            # Its ping task writes to the connection from inside websockets,
            # outside our _send_lock, so it can drain concurrently with an
            # application write and trip the concurrent-drain assertion.
            # Liveness is covered by the protocol's own 1s app heartbeat (a
            # failed heartbeat write surfaces a dead connection promptly).
            self._ws = await websockets.connect(self._url, ping_interval=None)
        except Exception:
            self._set_state(ConnectionState.DISCONNECTED)
            raise

        # Hello handshake (msgpack)
        await self._send(Hello(proto=PROTO_VERSION, client_name=self._client_name).pack())

        raw = await self._ws.recv()
        if not isinstance(raw, bytes):
            await self._ws.close()
            self._set_state(ConnectionState.DISCONNECTED)
            raise RuntimeError("Expected binary frame for hello_ack")
        msg = self._unpack_bytes(raw)
        if not isinstance(msg, HelloAck):
            await self._ws.close()
            self._set_state(ConnectionState.DISCONNECTED)
            raise RuntimeError(f"Expected hello_ack, got {type(msg).__name__}")

        self._set_state(ConnectionState.CONNECTED)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self._state not in (ConnectionState.CONNECTED, ConnectionState.CONNECTING):
            return
        self._set_state(ConnectionState.DISCONNECTING)
        await self._cleanup()
        self._set_state(ConnectionState.DISCONNECTED)

    # -- v0.2 control messages --

    async def send_tags(self, tags: list[str]) -> None:
        """Send client tags for profile resolution."""
        await self._send(Tags(tags=tuple(tags)).pack())

    async def send_capabilities(self, capabilities: list[str]) -> None:
        """Declare client capabilities (e.g. ["3d"])."""
        await self._send(Capabilities(capabilities=tuple(capabilities)).pack())

    async def subscribe(self, axes: list[str]) -> None:
        """Subscribe to specific axes for frame streaming."""
        await self._send(Subscribe(axes=tuple(axes)).pack())

    async def send_world_orientation(
        self,
        forward: list[float],
        up: list[float],
        handedness: Handedness = "right",
    ) -> None:
        """Declare the client's coordinate system."""
        await self._send(WorldOrientationMsg(
            orientation=WorldOrientation(
                forward=list(forward),
                up=list(up),
                handedness=handedness,
            ),
        ).pack())

    async def send_translation_scale(
        self, mode: TranslationScaleMode = "viewport", translation_scale: float = 1.0,
    ) -> None:
        """Set translation scaling mode."""
        await self._send(TranslationScaleMsg(
            scale=TranslationScale(mode=mode, translation_scale=translation_scale),
        ).pack())

    async def send_navigation_preferences(
        self,
        recenter_on_pan: bool = False,
        lock_pitch: bool | None = None,
        lock_yaw: bool | None = None,
        lock_roll: bool | None = None,
        lock_translation_plane: bool | None = None,
    ) -> None:
        """Set navigation preferences."""
        await self._send(NavigationPreferencesMsg(
            recenter_on_pan=recenter_on_pan,
            lock_pitch=lock_pitch,
            lock_yaw=lock_yaw,
            lock_roll=lock_roll,
            lock_translation_plane=lock_translation_plane,
        ).pack())

    async def send_camera_pose(
        self,
        t: tuple[float, float, float],
        r: tuple[float, float, float],
        mode: PoseMode = "absolute",
        fov: float | None = None,
        ortho_extent: float | None = None,
    ) -> None:
        """Send initial or correction camera pose to the server."""
        if mode == "absolute" and fov is None and ortho_extent is None:
            raise ValueError("absolute camera.pose requires 'fov' or 'ortho_extent'")
        await self._send(CameraPose(
            t=t, r=r, mode=mode, fov=fov, ortho_extent=ortho_extent,
        ).pack())

    async def send_camera_pivot(self, point: tuple[float, float, float]) -> None:
        """Send orbit pivot point to the server."""
        await self._send(CameraPivot(point=point).pack())

    async def send_object_pose(
        self,
        t: tuple[float, float, float],
        r: tuple[float, float, float],
        mode: PoseMode = "absolute",
    ) -> None:
        """Send initial or correction object pose to the server."""
        await self._send(ObjectPose(t=t, r=r, mode=mode).pack())

    async def send_object_pivot(self, point: tuple[float, float, float]) -> None:
        """Send object manipulation pivot point to the server."""
        await self._send(ObjectPivot(point=point).pack())

    async def send_ext(self, name: str, id: int, data: dict | None = None) -> None:
        """Send an extension message."""
        await self._send(Ext(name=name, id=id, data=data).pack())

    # -- Internals --

    async def _send(self, msg: dict) -> None:
        ws = self._ws
        if ws is None:
            return
        # Hold the lock across the send so concurrent writers (heartbeat loop
        # vs. send_* callers) can't drain the connection simultaneously.
        async with self._send_lock:
            await ws.send(msgpack.packb(msg, use_bin_type=True))

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                await self._send(Heartbeat().pack())
        except asyncio.CancelledError:
            pass

    async def _receive_loop(self) -> None:
        try:
            async for message in self._ws:  # type: ignore[union-attr]
                if isinstance(message, bytes):
                    self._handle_message(message)
        except websockets.ConnectionClosed:
            pass
        except asyncio.CancelledError:
            pass
        finally:
            if self._state == ConnectionState.CONNECTED:
                self._set_state(ConnectionState.DISCONNECTED)
                await self._cleanup()

    @staticmethod
    def _unpack_bytes(data: bytes) -> Msg:
        """Deserialize msgpack bytes into a typed Msg."""
        return unpack_msg(msgpack.unpackb(data, raw=False))

    def _handle_message(self, data: bytes) -> None:
        """Handle a msgpack message from the server."""
        try:
            msg = self._unpack_bytes(data)
        except Exception:
            logger.exception("Error unpacking message")
            return

        try:
            self._dispatch_message(msg)
        except Exception:
            logger.exception("Error handling %s message", type(msg).__name__)

    def _dispatch_message(self, msg: Msg) -> None:
        L = self._listener

        if isinstance(msg, Frame):
            L.on_frame(msg)

        elif isinstance(msg, Buttons):
            L.on_buttons(msg.buttons)

        elif isinstance(msg, MotionStart):
            L.on_motion_start(list(msg.need))

        elif isinstance(msg, MotionEnd):
            L.on_motion_end()

        elif isinstance(msg, Profile):
            L.on_profile(msg.mode, list(msg.active))

        elif isinstance(msg, CameraPose):
            L.on_camera_pose(msg)

        elif isinstance(msg, CameraPivot):
            L.on_camera_pivot(msg.point)

        elif isinstance(msg, ObjectPose):
            L.on_object_pose(msg)

        elif isinstance(msg, ObjectPivot):
            L.on_object_pivot(msg.point)

        elif isinstance(msg, AxesMsg):
            L.on_axes(list(msg.axes))

        elif isinstance(msg, Ext):
            L.on_ext(msg.name, msg.id, msg.data)

        elif isinstance(msg, Error):
            L.on_error(msg.code, msg.message)

    def _set_state(self, state: ConnectionState) -> None:
        self._state = state
        self._listener.on_state_change(state)

    async def _cleanup(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
