"""OpenAxis v0.2 protocol types."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import ClassVar

try:
    from typing import Literal
except ImportError:  # Python 3.7 (Maya 2022)
    class Literal:  # type: ignore[no-redef]
        def __class_getitem__(cls, _item):
            return cls

try:
    from typing import Self
except ImportError:  # Python < 3.11 (Maya 2024 and earlier)
    from typing import TypeVar
    Self = TypeVar("Self")

__all__ = [
    # Constants
    "PROTO_VERSION",
    "DEFAULT_URL",
    # Type aliases
    "PoseMode",
    "Handedness",
    "TranslationScaleMode",
    "NavigationMode",
    # Enums
    "ConnectionState",
    # Session-state objects
    "WorldOrientation",
    "TranslationScale",
    # Message base + dispatch
    "Msg",
    "MSG_TYPES",
    "unpack_msg",
    # Connection messages
    "Hello",
    "HelloAck",
    "Heartbeat",
    "Error",
    # Client setup
    "Tags",
    "Capabilities",
    "Subscribe",
    "WorldOrientationMsg",
    "TranslationScaleMsg",
    "NavigationPreferencesMsg",
    # Server responses
    "Axes",
    "Profile",
    "MotionStart",
    "MotionEnd",
    "Buttons",
    # Streaming
    "Frame",
    # Pose / pivot
    "CameraPose",
    "CameraPivot",
    "ObjectPose",
    "ObjectPivot",
    # Extension
    "Ext",
]

PROTO_VERSION = "openaxis/0.2"
DEFAULT_URL = "ws://127.0.0.1:6607"

# Protocol type aliases
PoseMode = Literal["absolute", "relative"]
Handedness = Literal["right", "left"]
TranslationScaleMode = Literal["viewport", "absolute"]
NavigationMode = Literal["orbit", "walk", "fly", "free_camera", "object"]


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"


# ---------------------------------------------------------------------------
# Session-state objects (mutable, stored on ClientSession)
# ---------------------------------------------------------------------------


@dataclass
class WorldOrientation:
    """Client-declared world coordinate convention."""

    forward: list[float] = field(default_factory=lambda: [0, 0, 1])
    up: list[float] = field(default_factory=lambda: [0, 1, 0])
    handedness: Handedness = "right"


@dataclass
class TranslationScale:
    """Client-declared translation scale preference."""

    mode: TranslationScaleMode = "viewport"
    translation_scale: float = 1.0


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _req(d: dict, key: str, type_name: str):
    """Get a required field, raising ValueError if missing."""
    if key not in d:
        raise ValueError(f"{type_name} missing required '{key}'")
    return d[key]


def _req3(d: dict, key: str, type_name: str) -> tuple[float, float, float]:
    """Get a required 3-element vector, raising ValueError if missing or wrong length."""
    v = _req(d, key, type_name)
    if len(v) != 3:
        raise ValueError(f"{type_name} '{key}' must have 3 elements, got {len(v)}")
    return (v[0], v[1], v[2])


# ---------------------------------------------------------------------------
# Protocol message base
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Msg:
    """Base for all protocol messages."""

    TYPE: ClassVar[str]

    def pack(self) -> dict:
        """Serialize to wire dict. Subclasses can override for custom logic."""
        d: dict = {"type": self.TYPE}
        for f in fields(self):
            v = getattr(self, f.name)
            if v is not None:
                d[f.name] = v
        return d

    @classmethod
    def unpack(cls, d: dict) -> Self:
        """Parse from wire dict. Subclasses override for validation."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Connection messages
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hello(Msg):
    TYPE: ClassVar[str] = "hello"
    proto: str = ""
    client_name: str = ""

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(
            proto=_req(d, "proto", "hello"),
            client_name=_req(d, "client_name", "hello"),
        )


@dataclass(frozen=True)
class HelloAck(Msg):
    TYPE: ClassVar[str] = "hello_ack"
    proto: str = ""
    server_name: str = ""

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(
            proto=_req(d, "proto", "hello_ack"),
            server_name=_req(d, "server_name", "hello_ack"),
        )


@dataclass(frozen=True)
class Heartbeat(Msg):
    TYPE: ClassVar[str] = "heartbeat"

    def pack(self) -> dict:
        return {"type": self.TYPE}

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls()


@dataclass(frozen=True)
class Error(Msg):
    TYPE: ClassVar[str] = "error"
    code: str = ""
    message: str = ""

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(
            code=_req(d, "code", "error"),
            message=_req(d, "message", "error"),
        )


# ---------------------------------------------------------------------------
# Client setup (client → server)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tags(Msg):
    TYPE: ClassVar[str] = "tags"
    tags: tuple[str, ...] = ()

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(tags=tuple(_req(d, "tags", "tags")))


@dataclass(frozen=True)
class Capabilities(Msg):
    TYPE: ClassVar[str] = "capabilities"
    capabilities: tuple[str, ...] = ()

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(capabilities=tuple(_req(d, "capabilities", "capabilities")))


@dataclass(frozen=True)
class Subscribe(Msg):
    TYPE: ClassVar[str] = "subscribe"
    axes: tuple[str, ...] = ()

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(axes=tuple(_req(d, "axes", "subscribe")))


@dataclass(frozen=True)
class WorldOrientationMsg(Msg):
    """Wire message wrapping a WorldOrientation."""

    TYPE: ClassVar[str] = "world_orientation"
    orientation: WorldOrientation = field(default_factory=WorldOrientation)

    def pack(self) -> dict:
        wo = self.orientation
        return {
            "type": self.TYPE,
            "forward": wo.forward,
            "up": wo.up,
            "handedness": wo.handedness,
        }

    @classmethod
    def unpack(cls, d: dict) -> Self:
        fwd = _req3(d, "forward", "world_orientation")
        up = _req3(d, "up", "world_orientation")
        return cls(orientation=WorldOrientation(
            forward=list(fwd),
            up=list(up),
            handedness=d.get("handedness", "right"),
        ))


@dataclass(frozen=True)
class TranslationScaleMsg(Msg):
    """Wire message wrapping a TranslationScale."""

    TYPE: ClassVar[str] = "translation_scale"
    scale: TranslationScale = field(default_factory=TranslationScale)

    def pack(self) -> dict:
        ts = self.scale
        return {
            "type": self.TYPE,
            "mode": ts.mode,
            "translation_scale": ts.translation_scale,
        }

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(scale=TranslationScale(
            mode=_req(d, "mode", "translation_scale"),
            translation_scale=d.get("translation_scale", 1.0),
        ))


# ---------------------------------------------------------------------------
# Server responses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Axes(Msg):
    TYPE: ClassVar[str] = "axes"
    axes: tuple[str, ...] = ()

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(axes=tuple(_req(d, "axes", "axes")))


@dataclass(frozen=True)
class Profile(Msg):
    TYPE: ClassVar[str] = "profile"
    mode: NavigationMode = "orbit"
    active: tuple[str, ...] = ()

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(
            mode=_req(d, "mode", "profile"),
            active=tuple(_req(d, "active", "profile")),
        )


@dataclass(frozen=True)
class MotionStart(Msg):
    TYPE: ClassVar[str] = "motion_start"
    need: tuple[str, ...] = ()

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(need=tuple(_req(d, "need", "motion_start")))


@dataclass(frozen=True)
class MotionEnd(Msg):
    TYPE: ClassVar[str] = "motion_end"

    def pack(self) -> dict:
        return {"type": self.TYPE}

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls()


@dataclass(frozen=True)
class Buttons(Msg):
    TYPE: ClassVar[str] = "buttons"
    buttons: int = 0

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(buttons=_req(d, "buttons", "buttons"))


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Frame(Msg):
    TYPE: ClassVar[str] = "frame"
    seq: int = 0
    t_us: int = 0
    values: tuple[float, ...] = ()

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(
            seq=_req(d, "seq", "frame"),
            t_us=_req(d, "t_us", "frame"),
            values=tuple(_req(d, "values", "frame")),
        )


# ---------------------------------------------------------------------------
# Pose / pivot (bidirectional)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CameraPose(Msg):
    TYPE: ClassVar[str] = "camera.pose"
    t: tuple[float, float, float] = (0.0, 0.0, 0.0)
    r: tuple[float, float, float] = (0.0, 0.0, 0.0)
    mode: PoseMode = "absolute"
    fov: float | None = None
    ortho_extent: float | None = None
    t_send: float | None = None  # wall-clock timestamp (time.time()) from server

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(
            t=_req3(d, "t", "camera.pose"),
            r=_req3(d, "r", "camera.pose"),
            mode=d.get("mode", "absolute"),
            fov=d.get("fov"),
            ortho_extent=d.get("ortho_extent"),
            t_send=d.get("t_send"),
        )


@dataclass(frozen=True)
class CameraPivot(Msg):
    TYPE: ClassVar[str] = "camera.pivot"
    point: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(point=_req3(d, "point", "camera.pivot"))


@dataclass(frozen=True)
class ObjectPose(Msg):
    TYPE: ClassVar[str] = "object.pose"
    t: tuple[float, float, float] = (0.0, 0.0, 0.0)
    r: tuple[float, float, float] = (0.0, 0.0, 0.0)
    mode: PoseMode = "absolute"

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(
            t=_req3(d, "t", "object.pose"),
            r=_req3(d, "r", "object.pose"),
            mode=d.get("mode", "absolute"),
        )


@dataclass(frozen=True)
class ObjectPivot(Msg):
    TYPE: ClassVar[str] = "object.pivot"
    point: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(point=_req3(d, "point", "object.pivot"))


# ---------------------------------------------------------------------------
# Navigation preferences (client → server)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NavigationPreferencesMsg(Msg):
    """Client-declared navigation preferences."""

    TYPE: ClassVar[str] = "navigation_preferences"
    recenter_on_pan: bool = False
    lock_pitch: bool | None = None
    lock_yaw: bool | None = None
    lock_roll: bool | None = None
    lock_translation_plane: bool | None = None

    def pack(self) -> dict:
        d: dict = {
            "type": self.TYPE,
            "recenter_on_pan": self.recenter_on_pan,
        }
        if self.lock_pitch is not None:
            d["lock_pitch"] = self.lock_pitch
        if self.lock_yaw is not None:
            d["lock_yaw"] = self.lock_yaw
        if self.lock_roll is not None:
            d["lock_roll"] = self.lock_roll
        if self.lock_translation_plane is not None:
            d["lock_translation_plane"] = self.lock_translation_plane
        return d

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(
            recenter_on_pan=d.get("recenter_on_pan", False),
            lock_pitch=d.get("lock_pitch"),
            lock_yaw=d.get("lock_yaw"),
            lock_roll=d.get("lock_roll"),
            lock_translation_plane=d.get("lock_translation_plane"),
        )


# ---------------------------------------------------------------------------
# Extension (generic, bidirectional)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ext(Msg):
    """Generic extension message for out-of-band exchanges."""

    TYPE: ClassVar[str] = "ext"
    name: str = ""
    id: int = 0
    data: dict | None = None

    def pack(self) -> dict:
        d: dict = {"type": self.TYPE, "name": self.name, "id": self.id}
        if self.data is not None:
            d["data"] = self.data
        return d

    @classmethod
    def unpack(cls, d: dict) -> Self:
        return cls(
            name=_req(d, "name", "ext"),
            id=d.get("id", 0),
            data=d.get("data"),
        )


# ---------------------------------------------------------------------------
# Dispatch registry
# ---------------------------------------------------------------------------

MSG_TYPES: dict[str, type[Msg]] = {cls.TYPE: cls for cls in [
    Hello, HelloAck, Heartbeat, Error,
    Tags, Capabilities, Subscribe, WorldOrientationMsg, TranslationScaleMsg,
    NavigationPreferencesMsg,
    Axes, Profile, MotionStart, MotionEnd, Frame, Buttons,
    CameraPose, CameraPivot, ObjectPose, ObjectPivot,
    Ext,
]}


def unpack_msg(d: dict) -> Msg:
    """Unpack a wire dict into the appropriate Msg subclass."""
    msg_type = d.get("type")
    cls = MSG_TYPES.get(msg_type)
    if cls is None:
        raise ValueError(f"Unknown message type: {msg_type}")
    return cls.unpack(d)
