# SPDX-License-Identifier: GPL-3.0-or-later
"""Map a ZBrush tool transform to the same camera Maya's pen navigator uses.

ZBrush has no camera object. Orbit, pan, and zoom move the current tool on
the canvas: rotation is the orbit, canvas position is the pan, and uniform
scale is the zoom. The stored angles are Euler values applied as

    M = Rx(x) * Ry(y) * Rz(z)

Pitch lives on X. ZBrush keeps X inside ±90° and, past that, reflects Y
(and Z) by 180°. Writing the other side of that fold is what makes the
model flip when a turn goes over the top or past a side view. Each sample
therefore picks the equivalent angles nearest the ones already on the tool,
with X kept inside ±90°.

Identity looks along the tool’s front. The top view is about (-90°, 0°, 0°),
looking along the tool’s +Y. The bottom view is the same X with Y and Z
turned by 180°, and it is a different orientation.

Canvas position uses the document origin at the upper left, so +Y is down
the screen. A pen stroke upward therefore decreases the tool’s Y position.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from openaxis.geometry import Vec3

from rotatrix_maya.pen_math import PAN_FRACTION_PER_PX, CameraFrame, roll, rotate_around_axis

# Fixed orbit radius for the virtual camera. Zoom is stored in the tool
# scale, so this distance only has to stay stable between samples.
CANONICAL_DISTANCE = 5.0
CANONICAL_ORTHO = 5.0


@dataclass
class ToolTransform:
    x: float
    y: float
    z: float
    scale_x: float
    scale_y: float
    scale_z: float
    rx: float
    ry: float
    rz: float

    @classmethod
    def from_values(cls, values: list[float]) -> ToolTransform:
        numbers = [float(value) for value in values]
        if len(numbers) < 9:
            raise ValueError(f"transform needs 9 values, got {len(numbers)}")
        return cls(
            numbers[0], numbers[1], numbers[2],
            numbers[3], numbers[4], numbers[5],
            numbers[6], numbers[7], numbers[8],
        )


def euler_to_matrix(rx: float, ry: float, rz: float) -> tuple[tuple[float, float, float], ...]:
    """Model rotation. Columns are the model's +X, +Y, and the eye direction."""
    return _mul(_rx(rx), _mul(_ry(ry), _rz(rz)))


def matrix_to_euler(
    matrix: tuple[tuple[float, float, float], ...],
    near: tuple[float, float, float] | None = None,
) -> tuple[float, float, float]:
    """Inverse of :func:`euler_to_matrix`.

    Two angle triples can be the same rotation. ZBrush displays the one
    whose X stays inside ±90°, so that triple is preferred. ``near`` is the
    tool's current angles; the closer legal triple is the one that continues
    the motion instead of flipping over the top or the side.
    """
    primary = _decompose(matrix)
    alternate = (
        _wrap(primary[0] + 180.0),
        _wrap(180.0 - primary[1]),
        _wrap(primary[2] + 180.0),
    )
    legal = [angles for angles in (primary, alternate) if abs(angles[0]) <= 90.0001]
    pool = legal or [primary, alternate]
    if near is None:
        return min(pool, key=lambda angles: abs(angles[0]) + abs(angles[1]) + abs(angles[2]))
    return min(pool, key=lambda angles: _gap(angles, near))


def tool_to_frame(tool: ToolTransform) -> CameraFrame:
    """Virtual camera looking at the tool origin from the current rotation."""
    matrix = euler_to_matrix(tool.rx, tool.ry, tool.rz)
    up = Vec3(matrix[0][1], matrix[1][1], matrix[2][1])
    backward = Vec3(matrix[0][2], matrix[1][2], matrix[2][2])
    if backward.length() < 1e-8:
        backward = Vec3(0, 0, 1)
    if up.length() < 1e-8:
        up = Vec3(0, 1, 0)
    eye = backward.normalized() * CANONICAL_DISTANCE
    return CameraFrame(eye, Vec3(0, 0, 0), up.normalized())


def frame_to_euler(
    frame: CameraFrame,
    near: tuple[float, float, float] | None = None,
) -> tuple[float, float, float]:
    """Tool Euler angles that show ``frame`` from a fixed canvas camera."""
    backward = frame.eye - frame.target
    if backward.length() < 1e-8:
        return 0.0, 0.0, 0.0
    backward = backward.normalized()
    up = frame.up.normalized()
    right = up.cross(backward)
    if right.length() < 1e-8:
        fallback = Vec3(1, 0, 0) if abs(up.y) > 0.9 else Vec3(0, 1, 0)
        right = fallback.cross(backward)
    right = right.normalized()
    up = backward.cross(right).normalized()
    matrix = (
        (right.x, up.x, backward.x),
        (right.y, up.y, backward.y),
        (right.z, up.z, backward.z),
    )
    return matrix_to_euler(matrix, near)


def advance_camera(
    frame: CameraFrame,
    dx_px: float,
    dy_up: float,
    twist_degrees: float,
    yaw_per_px: float,
    pitch_per_px: float,
) -> CameraFrame:
    """Step the canvas camera on from a known front view.

    ``dx_px`` is to the right and ``dy_up`` is up, the same deltas the
    background drag receives. Twist is clockwise about the axis coming out
    of the monitor. The sliders are not read; this is only the path taken
    since the front.
    """
    moved = screen_orbit(
        frame,
        math.radians(-dx_px * yaw_per_px),
        math.radians(dy_up * pitch_per_px),
    )
    if abs(twist_degrees) < 1e-8:
        return moved
    # Clockwise on the barrel matches the front view's positive Z.
    return roll(moved, math.radians(-twist_degrees))


def rotation_for_camera(frame: CameraFrame) -> tuple[float, float, float]:
    """Slider angles for a camera stepped from the front view.

    X at exactly ±90° is pulled just inside so the write does not flip Y.
    """
    rx, ry, rz = frame_to_euler(frame)
    if abs(rx) >= 90.0:
        rx = math.copysign(89.999, rx if rx != 0.0 else 1.0)
    return rx, ry, rz


def zoom_factor(
    previous: CameraFrame,
    previous_ortho: float | None,
    current: CameraFrame,
    current_ortho: float | None,
) -> float:
    """How much the view pulled back. Less than 1 means zoomed in.

    Orbit sends the change on eye distance. An orthographic client may send
    it on ``ortho_extent`` instead. Whichever channel actually moved wins,
    so a server that updates both does not zoom twice.
    """
    old_distance = max((previous.eye - previous.target).length(), 1e-6)
    new_distance = max((current.eye - current.target).length(), 1e-6)
    distance_factor = new_distance / old_distance
    ortho_factor = 1.0
    if previous_ortho and current_ortho and previous_ortho > 1e-8 and current_ortho > 1e-8:
        ortho_factor = current_ortho / previous_ortho
    if abs(math.log(max(distance_factor, 1e-8))) >= abs(math.log(max(ortho_factor, 1e-8))):
        factor = distance_factor
    else:
        factor = ortho_factor
    return min(20.0, max(0.05, factor))


def pan_canvas_delta(previous: CameraFrame, current: CameraFrame) -> tuple[float, float]:
    """Canvas shift for a camera pan. +X is right, +Y is down the screen."""
    delta = current.target - previous.target
    if delta.length() < 1e-8:
        return 0.0, 0.0
    right, up, _forward = current.axes()
    units = max((current.eye - current.target).length(), 0.01) * PAN_FRACTION_PER_PX
    if units < 1e-8:
        return 0.0, 0.0
    return -delta.dot(right) / units, delta.dot(up) / units


def screen_orbit(frame: CameraFrame, yaw: float, pitch: float, roll: float = 0.0) -> CameraFrame:
    """Turn ``frame`` like a background drag. Angles are radians.

    Positive yaw swings the eye toward screen right. Positive pitch swings
    it toward screen up. Roll is around the view axis. The axes stay with
    the camera, so the turn does not flip when the eye passes over the top.
    """
    right, up, _forward = frame.axes()
    offset = frame.eye - frame.target
    tilted = frame.up
    if abs(yaw) > 1e-8:
        offset = rotate_around_axis(offset, up, yaw)
        tilted = rotate_around_axis(tilted, up, yaw)
    if abs(pitch) > 1e-8:
        offset = rotate_around_axis(offset, right, -pitch)
        tilted = rotate_around_axis(tilted, right, -pitch)
    if offset.length() < 1e-8:
        return frame
    if abs(roll) > 1e-8:
        tilted = rotate_around_axis(tilted, offset.normalized(), roll)
    if tilted.length() < 1e-8:
        tilted = frame.up
    return CameraFrame(frame.target + offset, frame.target, tilted.normalized())


def background_drag(
    previous: CameraFrame,
    current: CameraFrame,
    deg_per_px: float,
) -> tuple[float, float]:
    """Canvas drag, x right and y down, that aims the view at ``current``.

    This is the inverse of :func:`screen_orbit` for yaw and pitch. Roll is
    not part of a straight background drag.
    """
    scale = deg_per_px if abs(deg_per_px) > 1e-6 else 0.3
    backward = (previous.eye - previous.target).normalized()
    new_back = current.eye - current.target
    if new_back.length() < 1e-8 or backward.length() < 1e-8:
        return 0.0, 0.0
    new_back = new_back.normalized()
    right, up, _forward = previous.axes()
    yaw = math.degrees(math.atan2(new_back.dot(right), new_back.dot(backward)))
    pitch = math.degrees(math.atan2(new_back.dot(up), new_back.dot(backward)))
    # Eye moving right is a drag to the left: the model follows the cursor.
    # Eye moving up is a drag down the canvas. ZBrush's widget Y is flipped
    # from the on-screen drag, so positive canvas Y pitches the view up.
    return -yaw / scale, pitch / scale


def screen_roll_degrees(previous: CameraFrame, current: CameraFrame) -> float:
    """Clockwise screen roll of ``current`` relative to ``previous``, in degrees.

    Yaw and pitch are the same aim change a background drag would make, so a
    turn that does not twist the camera returns 0. Positive matches
    :func:`rotatrix_maya.pen_math.roll`.
    """
    old_back = previous.eye - previous.target
    new_back = current.eye - current.target
    if old_back.length() < 1e-8 or new_back.length() < 1e-8:
        return 0.0
    old_back = old_back.normalized()
    new_back = new_back.normalized()
    right, up, _forward = previous.axes()
    yaw = math.atan2(new_back.dot(right), new_back.dot(old_back))
    pitch = math.atan2(new_back.dot(up), new_back.dot(old_back))
    aimed = screen_orbit(previous, yaw, pitch)
    axis = aimed.eye - aimed.target
    if axis.length() < 1e-8:
        return 0.0
    axis = axis.normalized()
    aligned = _reject(aimed.up, axis)
    upright = _reject(current.up, axis)
    if aligned.length() < 1e-8 or upright.length() < 1e-8:
        return 0.0
    aligned = aligned.normalized()
    upright = upright.normalized()
    # roll() spins camera-up around the view direction. ``axis`` points from
    # the target back to the eye, so the cross product is negated.
    sine = -aligned.cross(upright).dot(axis)
    return math.degrees(math.atan2(sine, aligned.dot(upright)))


def _upright_euler(rx: float, ry: float, rz: float) -> tuple[float, float, float]:
    """Sliders to the orientation in front of the fixed camera.

    A sideways stroke that passes Y=90° stores Z at ±180. That 180° keeps
    the picture upright; it is not a twist. An up-and-down stroke that passes
    X=90° stores Y at ±180 and walks X back. The picture is the X that would
    have continued past 90°.
    """
    ry = _wrap(ry)
    rz = _wrap(rz)
    if abs(ry) >= 90.0 and abs(abs(rz) - 180.0) <= 90.0:
        sign = rz if rz != 0.0 else 1.0
        return rx, ry, _wrap(rz - math.copysign(180.0, sign))
    if abs(abs(ry) - 180.0) <= 5.0 and abs(rz) <= 45.0:
        return _wrap(180.0 - rx), 0.0, rz
    return rx, ry, rz


def _stroke_euler(rx: float, ry: float, rz: float) -> tuple[float, float, float]:
    """Angles ZBrush will keep, in the form a stroke would have stored.

    Writing X at exactly ±90° flips Y, so a result on that line is pulled
    just inside. Past a sideways Y of 90°, Z is written near ±180 so the
    picture stays upright.
    """
    if abs(rx) >= 90.0:
        rx = math.copysign(89.999, rx)
    ry = _wrap(ry)
    rz = _wrap(rz)
    if abs(ry) >= 90.0:
        sign = rz if abs(rz) > 1e-8 else 1.0
        rz = _wrap(rz + math.copysign(180.0, sign))
    return rx, ry, rz


def _transpose(matrix: tuple[tuple[float, float, float], ...]) -> tuple[tuple[float, float, float], ...]:
    return tuple(tuple(matrix[row][col] for row in range(3)) for col in range(3))


def _rotation_degrees(
    before: tuple[tuple[float, float, float], ...],
    after: tuple[tuple[float, float, float], ...],
) -> float:
    """Angle between two orientations, in degrees."""
    delta = _mul(after, _transpose(before))
    trace = delta[0][0] + delta[1][1] + delta[2][2]
    cosine = max(-1.0, min(1.0, (trace - 1.0) * 0.5))
    return math.degrees(math.acos(cosine))


def camera_twist(tool: ToolTransform, clockwise_degrees: float) -> ToolTransform:
    """Turn the model about the fixed view, wherever the model is aimed.

    The canvas camera stays put. ``model`` is how the tool is rotated in
    that canvas. The view's direction in the model's own axes is
    ``transpose(model)`` times the axis out of the monitor. A screen twist
    is that same turn written back onto the model:

        model_delta = transpose(model) * view_turn * model
        updated = model * model_delta

    which is the view turn applied in canvas space. The sliders written are
    the ones for ``updated``. A result that would swing the model by more
    than the twist is left alone, so a fold cannot throw it to another pose.
    """
    if abs(clockwise_degrees) < 1e-8:
        return tool
    vx, vy, vz = _upright_euler(tool.rx, tool.ry, tool.rz)
    model = euler_to_matrix(vx, vy, vz)
    view_turn = _rz(clockwise_degrees)
    model_delta = _mul(_transpose(model), _mul(view_turn, model))
    updated = _mul(model, model_delta)
    rx, ry, rz = matrix_to_euler(updated, near=(vx, vy, vz))
    if abs(rx) >= 90.0:
        rx = math.copysign(89.999, rx if rx != 0.0 else 1.0)
    shown = euler_to_matrix(rx, ry, rz)
    if _rotation_degrees(model, shown) > abs(clockwise_degrees) + 2.0:
        return tool
    return ToolTransform(
        tool.x, tool.y, tool.z,
        tool.scale_x, tool.scale_y, tool.scale_z,
        rx, ry, rz,
    )


def with_screen_roll(tool: ToolTransform, clockwise_degrees: float) -> ToolTransform:
    """Spin the model in the canvas plane.

    ZBrush's camera is fixed, so this is a turn around the axis out of the
    screen. It is not a turn around the face that started as the front.
    Positive is the opposite of the previous front-axis twist: the model's
    up side moves toward screen left.
    """
    if abs(clockwise_degrees) < 1e-8:
        return tool
    matrix = euler_to_matrix(tool.rx, tool.ry, tool.rz)
    spun = _mul(_rz(clockwise_degrees), matrix)
    rx, ry, rz = matrix_to_euler(spun, near=(tool.rx, tool.ry, tool.rz))
    return ToolTransform(
        tool.x, tool.y, tool.z,
        tool.scale_x, tool.scale_y, tool.scale_z,
        rx, ry, rz,
    )


def apply_navigation(
    tool: ToolTransform,
    previous: CameraFrame,
    current: CameraFrame,
    previous_ortho: float | None,
    current_ortho: float | None,
    keep_rotation: bool = False,
) -> ToolTransform:
    """Write one navigation step back onto a tool transform."""
    if keep_rotation:
        rx, ry, rz = tool.rx, tool.ry, tool.rz
    else:
        rx, ry, rz = frame_to_euler(current, near=(tool.rx, tool.ry, tool.rz))
    zoom = zoom_factor(previous, previous_ortho, current, current_ortho)
    scale = 1.0 / zoom
    dx, dy = pan_canvas_delta(previous, current)
    return ToolTransform(
        tool.x + dx,
        tool.y + dy,
        tool.z,
        _clamp_scale(tool.scale_x * scale),
        _clamp_scale(tool.scale_y * scale),
        _clamp_scale(tool.scale_z * scale),
        rx,
        ry,
        rz,
    )


def _decompose(
    matrix: tuple[tuple[float, float, float], ...],
) -> tuple[float, float, float]:
    """One Euler solution for M = Rx * Ry * Rz. X is not yet folded."""
    sine_y = max(-1.0, min(1.0, matrix[0][2]))
    ry = math.degrees(math.asin(sine_y))
    cosine_y = math.cos(math.radians(ry))
    if abs(cosine_y) > 1e-6:
        rx = math.degrees(math.atan2(-matrix[1][2], matrix[2][2]))
        rz = math.degrees(math.atan2(-matrix[0][1], matrix[0][0]))
    else:
        sign = 1.0 if sine_y >= 0.0 else -1.0
        rx = math.degrees(math.atan2(sign * matrix[1][0], matrix[1][1]))
        rz = 0.0
    return _wrap(rx), _wrap(ry), _wrap(rz)


def _wrap(degrees: float) -> float:
    return (degrees + 180.0) % 360.0 - 180.0


def _gap(
    angles: tuple[float, float, float],
    near: tuple[float, float, float],
) -> float:
    return sum(abs(_wrap(angles[i] - near[i])) for i in range(3))


def _reject(vector: Vec3, axis: Vec3) -> Vec3:
    return vector - axis * vector.dot(axis)


def _clamp_scale(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return 1.0
    return min(1.0e7, max(1.0e-4, value))


def _rx(degrees: float) -> tuple[tuple[float, float, float], ...]:
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    return (
        (1.0, 0.0, 0.0),
        (0.0, cosine, -sine),
        (0.0, sine, cosine),
    )


def _ry(degrees: float) -> tuple[tuple[float, float, float], ...]:
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    return (
        (cosine, 0.0, sine),
        (0.0, 1.0, 0.0),
        (-sine, 0.0, cosine),
    )


def _rz(degrees: float) -> tuple[tuple[float, float, float], ...]:
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    return (
        (cosine, -sine, 0.0),
        (sine, cosine, 0.0),
        (0.0, 0.0, 1.0),
    )


def _mul(
    left: tuple[tuple[float, float, float], ...],
    right: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    return tuple(
        tuple(sum(left[row][k] * right[k][col] for k in range(3)) for col in range(3))
        for row in range(3)
    )
