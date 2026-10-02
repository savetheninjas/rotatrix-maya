# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera math for Wacom pen navigation.

No Maya imports. X/Y are screen deltas (x right, y up, in pixels). Twist is
a signed degree delta from the pen's barrel rotation, after unwrapping.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from openaxis.geometry import Vec3

# Tuned so a short stroke on the Cintiq is a clear orbit, not a full spin.
ORBIT_DEG_PER_PX = 0.22
PAN_FRACTION_PER_PX = 0.0018
ZOOM_PER_DEG = 0.01

PIXEL_DEADZONE = 0.5
TWIST_DEADZONE_DEG = 0.15


@dataclass
class CameraFrame:
    eye: Vec3
    target: Vec3
    up: Vec3

    def axes(self) -> tuple[Vec3, Vec3, Vec3]:
        """Return (right, up, forward) as an orthonormal view basis."""
        forward = (self.target - self.eye).normalized()
        right = forward.cross(self.up).normalized()
        if right.length() < 1e-8:
            fallback = Vec3(1, 0, 0) if abs(self.up.y) > 0.9 else Vec3(0, 1, 0)
            right = forward.cross(fallback).normalized()
        up = right.cross(forward).normalized()
        return right, up, forward


def unwrap_degrees(previous: float, current: float) -> float:
    """Shortest signed step from one absolute pen angle to the next."""
    delta = current - previous
    return (delta + 180.0) % 360.0 - 180.0


def rotate_around_axis(vector: Vec3, axis: Vec3, radians: float) -> Vec3:
    """Rodrigues rotation of ``vector`` around ``axis`` through the origin."""
    axis_n = axis.normalized()
    if axis_n.length() < 1e-12 or abs(radians) < 1e-12:
        return vector
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return (
        vector * cosine
        + axis_n.cross(vector) * sine
        + axis_n * (axis_n.dot(vector) * (1.0 - cosine))
    )


def _horizon(back: Vec3, world_up: Vec3) -> tuple[Vec3, Vec3] | None:
    """Screen-right and level up for a camera whose backward axis is ``back``."""
    up_axis = world_up.normalized()
    level = up_axis - back * back.dot(up_axis)
    if level.length() < 1e-8:
        return None
    level = level.normalized()
    screen_right = level.cross(back).normalized()
    if screen_right.length() < 1e-8:
        return None
    return screen_right, level


def _roll_about(back: Vec3, world_up: Vec3, cam_up: Vec3) -> float:
    """Roll of ``cam_up`` off the horizon, about the pivot-to-eye axis."""
    basis = _horizon(back, world_up)
    if basis is None:
        return 0.0
    screen_right, level = basis
    return math.atan2(cam_up.dot(screen_right), cam_up.dot(level))


def _up_with_roll(back: Vec3, world_up: Vec3, roll: float) -> Vec3:
    basis = _horizon(back, world_up)
    if basis is None:
        return Vec3(1, 0, 0) if abs(back.y) > 0.9 else Vec3(0, 1, 0)
    screen_right, level = basis
    return (level * math.cos(roll) + screen_right * math.sin(roll)).normalized()


def orbit(
    frame: CameraFrame,
    pivot: Vec3,
    yaw_radians: float,
    pitch_radians: float,
    world_up: Vec3 | None = None,
    roll_radians: float = 0.0,
) -> CameraFrame:
    """Orbit around ``pivot`` and keep looking at it.

    Yaw spins around world up. Pitch tips around the level horizon axis.
    Neither one rolls the camera. ``roll_radians`` is the only roll input,
    added to whatever roll the camera already has.
    """
    up_axis = (world_up or Vec3(0, 1, 0)).normalized()
    offset = frame.eye - pivot
    distance = offset.length()
    if distance < 1e-8 or up_axis.length() < 1e-8:
        return frame

    back = offset.normalized()
    roll = _roll_about(back, up_axis, frame.up) + roll_radians

    offset = rotate_around_axis(offset, up_axis, yaw_radians)
    if offset.length() < 1e-8:
        offset = back * distance
    right = up_axis.cross(offset.normalized())
    if right.length() >= 1e-6:
        offset = rotate_around_axis(offset, right.normalized(), pitch_radians)
    if offset.length() < 1e-8:
        offset = back * distance

    eye = pivot + offset.normalized() * distance
    back = (eye - pivot).normalized()
    return CameraFrame(eye, pivot, _up_with_roll(back, up_axis, roll))


def roll(frame: CameraFrame, radians: float) -> CameraFrame:
    """Roll around the view axis. Positive radians roll clockwise on screen."""
    _right, _up, forward = frame.axes()
    return CameraFrame(
        frame.eye,
        frame.target,
        rotate_around_axis(frame.up, forward, radians),
    )


def pan(frame: CameraFrame, dx_px: float, dy_px: float, fraction_per_px: float) -> CameraFrame:
    """Slide eye and target together. ``dy_px`` is up-positive."""
    right, up, _forward = frame.axes()
    distance = max((frame.target - frame.eye).length(), 0.01)
    delta = (right * dx_px + up * dy_px) * (distance * fraction_per_px)
    return CameraFrame(frame.eye + delta, frame.target + delta, frame.up)


def dolly(frame: CameraFrame, twist_degrees: float, per_degree: float) -> tuple[CameraFrame, float]:
    """Move the eye toward the target. Positive twist zooms in.

    Returns the new frame and the scale applied to distance (and ortho size).
    """
    offset = frame.target - frame.eye
    distance = offset.length()
    if distance < 1e-8:
        return frame, 1.0
    factor = math.exp(-twist_degrees * per_degree)
    factor = min(20.0, max(0.05, factor))
    new_distance = min(1.0e7, max(0.01, distance * factor))
    eye = frame.target - offset.normalized() * new_distance
    applied = new_distance / distance
    return CameraFrame(eye, frame.target, frame.up), applied


def apply_pen_delta(
    frame: CameraFrame,
    pivot: Vec3,
    *,
    dx_px: float,
    dy_px: float,
    twist_degrees: float,
    orbiting: bool,
    ortho_extent: float | None,
    world_up: Vec3 | None = None,
) -> tuple[CameraFrame, float | None]:
    """Apply one pen sample. ``dy_px`` is up-positive.

    Returns the new frame and the new ortho extent (unchanged while orbiting).
    """
    if orbiting:
        yaw = math.radians(-dx_px * ORBIT_DEG_PER_PX)
        pitch = math.radians(dy_px * ORBIT_DEG_PER_PX)
        roll_delta = 0.0
        if abs(twist_degrees) >= TWIST_DEADZONE_DEG:
            roll_delta = math.radians(twist_degrees)
        frame = orbit(
            frame, pivot, yaw, pitch, world_up=world_up, roll_radians=roll_delta,
        )
        return frame, ortho_extent

    if abs(dx_px) >= PIXEL_DEADZONE or abs(dy_px) >= PIXEL_DEADZONE:
        frame = pan(frame, -dx_px, -dy_px, PAN_FRACTION_PER_PX)
    if abs(twist_degrees) >= TWIST_DEADZONE_DEG:
        frame, factor = dolly(frame, twist_degrees, ZOOM_PER_DEG)
        if ortho_extent is not None:
            ortho_extent = max(1.0e-4, ortho_extent * factor)
    return frame, ortho_extent
