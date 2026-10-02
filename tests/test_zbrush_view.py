# SPDX-License-Identifier: GPL-3.0-or-later
"""ZBrush tool-transform math. No ZBrush."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from openaxis.geometry import Vec3  # noqa: E402
from rotatrix_maya.pen_math import CameraFrame, apply_pen_delta, roll as roll_frame  # noqa: E402
from rotatrix_zbrush.view import (  # noqa: E402
    CANONICAL_DISTANCE,
    CANONICAL_ORTHO,
    ToolTransform,
    apply_navigation,
    background_drag,
    euler_to_matrix,
    matrix_to_euler,
    screen_orbit,
    screen_roll_degrees,
    tool_to_frame,
    with_screen_roll,
)


def _tool(**overrides: float) -> ToolTransform:
    values = dict(x=400, y=300, z=0, scale_x=100, scale_y=100, scale_z=100, rx=0, ry=0, rz=0)
    values.update(overrides)
    return ToolTransform(**values)


class EulerTests(unittest.TestCase):
    def test_identity_and_top_view_roundtrip(self) -> None:
        for angles in ((0, 0, 0), (-90, 0, 0), (-90, 180, 180), (20, -35, 15), (-70, 120, -40)):
            matrix = euler_to_matrix(*angles)
            rx, ry, rz = matrix_to_euler(matrix, near=angles)
            again = euler_to_matrix(rx, ry, rz)
            for row in range(3):
                for col in range(3):
                    self.assertAlmostEqual(matrix[row][col], again[row][col], places=5)

    def test_top_view_places_the_eye_above_the_model(self) -> None:
        frame = tool_to_frame(_tool(rx=-90, ry=0, rz=0))
        self.assertGreater(frame.eye.y, CANONICAL_DISTANCE * 0.9)
        self.assertAlmostEqual(frame.eye.x, 0, places=4)
        self.assertAlmostEqual(frame.eye.z, 0, places=4)
        self.assertAlmostEqual(frame.target.length(), 0, places=5)

    def test_bottom_view_is_not_the_top_view(self) -> None:
        top = tool_to_frame(_tool(rx=-90, ry=0, rz=0))
        bottom = tool_to_frame(_tool(rx=-90, ry=180, rz=180))
        self.assertLess(bottom.eye.y, -CANONICAL_DISTANCE * 0.9)
        self.assertLess(top.eye.dot(bottom.eye), -0.9)

    def test_camera_roundtrip_keeps_the_view(self) -> None:
        for angles in ((0, 0, 0), (-90, 0, 0), (-90, 180, 180), (15, 40, -25), (-45, 10, 80)):
            frame = tool_to_frame(_tool(rx=angles[0], ry=angles[1], rz=angles[2]))
            rx, ry, rz = matrix_to_euler(euler_to_matrix(*angles), near=angles)
            back = tool_to_frame(_tool(rx=rx, ry=ry, rz=rz))
            self.assertAlmostEqual(frame.eye.x, back.eye.x, places=4)
            self.assertAlmostEqual(frame.eye.y, back.eye.y, places=4)
            self.assertAlmostEqual(frame.eye.z, back.eye.z, places=4)
            self.assertGreater(frame.up.dot(back.up), 0.999)

    def test_pitch_over_the_top_keeps_turning(self) -> None:
        near = (0.0, 0.0, 0.0)
        previous_up = Vec3(0, 1, 0)
        for pitch in range(0, 181, 10):
            frame = _pitched_camera(pitch)
            rx, ry, rz = matrix_to_euler(_frame_matrix(frame), near=near)
            self.assertLessEqual(abs(rx), 90.01)
            shown = tool_to_frame(_tool(rx=rx, ry=ry, rz=rz))
            self.assertGreater(shown.up.dot(frame.up), 0.99)
            self.assertGreater(shown.eye.normalized().dot(frame.eye.normalized()), 0.99)
            self.assertGreater(shown.up.dot(previous_up), 0.9)
            near = (rx, ry, rz)
            previous_up = shown.up

    def test_yaw_past_the_side_does_not_flip(self) -> None:
        near = (0.0, 0.0, 0.0)
        previous_up = Vec3(0, 1, 0)
        for yaw in range(0, 361, 15):
            frame = _yawed_camera(yaw)
            rx, ry, rz = matrix_to_euler(_frame_matrix(frame), near=near)
            self.assertLessEqual(abs(rx), 90.01)
            shown = tool_to_frame(_tool(rx=rx, ry=ry, rz=rz))
            self.assertGreater(shown.up.dot(frame.up), 0.99)
            self.assertGreater(shown.up.dot(previous_up), 0.9)
            near = (rx, ry, rz)
            previous_up = shown.up


class NavigationTests(unittest.TestCase):
    def test_pen_right_moves_the_tool_right(self) -> None:
        previous = tool_to_frame(_tool())
        current, ortho = apply_pen_delta(
            previous, Vec3(0, 0, 0),
            dx_px=25, dy_px=0, twist_degrees=0,
            orbiting=False, ortho_extent=CANONICAL_ORTHO,
        )
        moved = apply_navigation(_tool(), previous, current, CANONICAL_ORTHO, ortho)
        self.assertGreater(moved.x, 420)
        self.assertAlmostEqual(moved.y, 300, places=3)
        self.assertAlmostEqual(moved.scale_x, 100, places=3)

    def test_pen_up_moves_the_tool_up_the_screen(self) -> None:
        previous = tool_to_frame(_tool())
        current, ortho = apply_pen_delta(
            previous, Vec3(0, 0, 0),
            dx_px=0, dy_px=25, twist_degrees=0,
            orbiting=False, ortho_extent=CANONICAL_ORTHO,
        )
        moved = apply_navigation(_tool(), previous, current, CANONICAL_ORTHO, ortho)
        self.assertLess(moved.y, 280)
        self.assertAlmostEqual(moved.x, 400, places=3)

    def test_twist_zoom_grows_the_tool(self) -> None:
        previous = tool_to_frame(_tool())
        current, ortho = apply_pen_delta(
            previous, Vec3(0, 0, 0),
            dx_px=0, dy_px=0, twist_degrees=40,
            orbiting=False, ortho_extent=CANONICAL_ORTHO,
        )
        moved = apply_navigation(_tool(), previous, current, CANONICAL_ORTHO, ortho)
        self.assertGreater(moved.scale_x, 110)
        self.assertAlmostEqual(moved.x, 400, places=3)
        self.assertAlmostEqual(moved.y, 300, places=3)

    def test_orbit_stays_put_and_keeps_scale(self) -> None:
        tool = _tool(rx=10, ry=-20, rz=5)
        previous = tool_to_frame(tool)
        current, ortho = apply_pen_delta(
            previous, Vec3(0, 0, 0),
            dx_px=40, dy_px=-15, twist_degrees=20,
            orbiting=True, ortho_extent=CANONICAL_ORTHO,
        )
        moved = apply_navigation(tool, previous, current, CANONICAL_ORTHO, ortho)
        self.assertAlmostEqual(moved.x, tool.x, places=3)
        self.assertAlmostEqual(moved.y, tool.y, places=3)
        self.assertAlmostEqual(moved.scale_x, tool.scale_x, places=3)
        self.assertGreater(abs(moved.ry - tool.ry) + abs(moved.rx - tool.rx), 1.0)

    def test_sideways_pan_then_orbit_does_not_slide(self) -> None:
        tool = _tool()
        previous = tool_to_frame(tool)
        panned, ortho = apply_pen_delta(
            previous, Vec3(0, 0, 0),
            dx_px=30, dy_px=0, twist_degrees=0,
            orbiting=False, ortho_extent=CANONICAL_ORTHO,
        )
        tool = apply_navigation(tool, previous, panned, CANONICAL_ORTHO, ortho)
        parked = tool.x
        # Next orbit sample starts from the centered virtual camera again.
        previous = tool_to_frame(tool)
        orbited, ortho = apply_pen_delta(
            previous, Vec3(0, 0, 0),
            dx_px=50, dy_px=0, twist_degrees=0,
            orbiting=True, ortho_extent=CANONICAL_ORTHO,
        )
        tool = apply_navigation(tool, previous, orbited, CANONICAL_ORTHO, ortho)
        self.assertAlmostEqual(tool.x, parked, places=3)

    def test_background_drag_follows_the_hand_and_crosses_the_top(self) -> None:
        frame = _front()
        # Pen-right is a negative yaw: the eye swings toward screen left,
        # which is the drag that makes the model follow the cursor right.
        turned = screen_orbit(frame, math.radians(-12), 0.0)
        dx, dy = background_drag(frame, turned, deg_per_px=1.0)
        self.assertGreater(dx, 8.0)
        self.assertAlmostEqual(dy, 0.0, delta=0.5)

        frame = _front()
        raised = screen_orbit(frame, 0.0, math.radians(12))
        dx, dy = background_drag(frame, raised, deg_per_px=1.0)
        self.assertAlmostEqual(dx, 0.0, delta=0.5)
        self.assertGreater(dy, 8.0)

        # Stepping over the top keeps dragging the same way. It does not reverse.
        frame = _front()
        for _ in range(18):
            nxt = screen_orbit(frame, 0.0, math.radians(10))
            dx, dy = background_drag(frame, nxt, deg_per_px=1.0)
            self.assertGreater(dy, 5.0)
            self.assertAlmostEqual(dx, 0.0, delta=1.0)
            self.assertGreater(nxt.up.dot(frame.up), 0.9)
            frame = nxt

    def test_screen_roll_spins_in_the_canvas_not_around_the_front(self) -> None:
        frame = screen_orbit(_front(), math.radians(50), math.radians(-35))
        rolled = roll_frame(frame, math.radians(17))
        self.assertAlmostEqual(screen_roll_degrees(frame, rolled), 17.0, delta=0.2)
        aimed = screen_orbit(frame, math.radians(28), math.radians(-16))
        self.assertAlmostEqual(screen_roll_degrees(frame, aimed), 0.0, delta=0.5)

        front = euler_to_matrix(0, 0, 0)
        spun = euler_to_matrix(*_angles(with_screen_roll(_tool(), 20)))
        # Up tips toward screen left. The face toward the camera stays put.
        self.assertLess(spun[0][1], -0.2)
        self.assertGreater(spun[1][1], 0.8)
        self.assertAlmostEqual(spun[2][2], front[2][2], delta=0.05)

        before = euler_to_matrix(0, 90, 180)
        after = euler_to_matrix(*_angles(with_screen_roll(_tool(rx=0, ry=90, rz=180), 20)))
        # The side facing the camera stays there. The old front swings in the
        # canvas instead of being the axle.
        for row in range(3):
            self.assertAlmostEqual(after[row][0], before[row][0], delta=0.05)
        self.assertGreater(abs(after[0][2] - before[0][2]) + abs(after[1][2] - before[1][2]), 0.2)
        self.assertAlmostEqual(after[2][1], 0.0, delta=0.05)

        tool = _tool(rx=-40, ry=55, rz=20)
        previous = euler_to_matrix(tool.rx, tool.ry, tool.rz)
        for _ in range(16):
            tool = with_screen_roll(tool, 8)
            matrix = euler_to_matrix(tool.rx, tool.ry, tool.rz)
            agreement = sum(
                previous[row][col] * matrix[row][col]
                for row in range(3) for col in range(3)
            )
            self.assertGreater(agreement, 2.9)
            self.assertLessEqual(abs(tool.rx), 90.01)
            previous = matrix

    def test_twist_tenths_stay_the_same_speed_through_the_turn(self) -> None:
        from rotatrix_zbrush.tablet import _twist_degrees

        slow = _twist_degrees(100) - _twist_degrees(0)
        fast = _twist_degrees(2000) - _twist_degrees(1900)
        self.assertAlmostEqual(slow, 10.0, delta=0.01)
        self.assertAlmostEqual(fast, 10.0, delta=0.01)

    def test_ortho_zoom_without_a_distance_change(self) -> None:
        previous = CameraFrame(Vec3(0, 0, 5), Vec3(0, 0, 0), Vec3(0, 1, 0))
        current = CameraFrame(Vec3(0, 0, 5), Vec3(0, 0, 0), Vec3(0, 1, 0))
        moved = apply_navigation(_tool(), previous, current, 5.0, 2.5)
        self.assertAlmostEqual(moved.scale_x, 200, places=3)


def _angles(tool: ToolTransform) -> tuple[float, float, float]:
    return tool.rx, tool.ry, tool.rz


def _front() -> CameraFrame:
    return CameraFrame(Vec3(0, 0, CANONICAL_DISTANCE), Vec3(0, 0, 0), Vec3(0, 1, 0))


def _pitched_camera(pitch_degrees: float) -> CameraFrame:
    """Camera that pitches over the top. Up stays continuous through the pole."""
    angle = math.radians(pitch_degrees)
    eye = Vec3(0.0, math.sin(angle) * CANONICAL_DISTANCE, math.cos(angle) * CANONICAL_DISTANCE)
    up = Vec3(0.0, math.cos(angle), -math.sin(angle))
    return CameraFrame(eye, Vec3(0, 0, 0), up)


def _yawed_camera(yaw_degrees: float) -> CameraFrame:
    angle = math.radians(yaw_degrees)
    eye = Vec3(math.sin(angle) * CANONICAL_DISTANCE, 0.0, math.cos(angle) * CANONICAL_DISTANCE)
    return CameraFrame(eye, Vec3(0, 0, 0), Vec3(0, 1, 0))


def _frame_matrix(frame: CameraFrame) -> tuple[tuple[float, float, float], ...]:
    backward = (frame.eye - frame.target).normalized()
    up = frame.up.normalized()
    right = up.cross(backward).normalized()
    up = backward.cross(right).normalized()
    return (
        (right.x, up.x, backward.x),
        (right.y, up.y, backward.y),
        (right.z, up.z, backward.z),
    )


class StrokeAxisTests(unittest.TestCase):
    def test_vertical_drag_stays_on_the_midline(self) -> None:
        from rotatrix_zbrush.viewport import orbit_segments

        segments = orbit_segments(0, 40, center_x=800, center_y=500)
        self.assertEqual(len(segments), 1)
        x0, y0, x1, y1 = segments[0]
        self.assertEqual((x0, x1), (800, 800))
        self.assertNotEqual(y0, y1)
        self.assertLess(y0, 0)

    def test_side_to_side_stays_level_through_the_middle(self) -> None:
        from rotatrix_zbrush.viewport import orbit_segments

        segments = orbit_segments(30, 0, center_x=800, center_y=500)
        self.assertEqual(len(segments), 1)
        x0, y0, x1, y1 = segments[0]
        self.assertEqual((y0, y1), (500, 500))
        self.assertLess(x0, 0)
        self.assertNotEqual(x0, x1)

    def test_twist_turns_about_the_fixed_view(self) -> None:
        from rotatrix_zbrush.view import _rotation_degrees, _upright_euler, camera_twist, euler_to_matrix

        def view_in_model(rx: float, ry: float, rz: float) -> tuple[float, float, float]:
            matrix = euler_to_matrix(rx, ry, rz)
            return (matrix[2][0], matrix[2][1], matrix[2][2])

        front = camera_twist(_tool(), 20)
        self.assertAlmostEqual(front.rx, 0.0, places=3)
        self.assertAlmostEqual(front.ry, 0.0, places=3)
        self.assertAlmostEqual(front.rz, 20.0, places=3)

        before = _tool(rx=0, ry=40, rz=0)
        turned = camera_twist(before, 15)
        self.assertLess(abs(turned.rx), 90.0)
        aim_before = view_in_model(0.0, 40.0, 0.0)
        aim_after = view_in_model(turned.rx, turned.ry, turned.rz)
        self.assertAlmostEqual(sum(a * b for a, b in zip(aim_before, aim_after)), 1.0, places=3)
        self.assertAlmostEqual(
            _rotation_degrees(euler_to_matrix(0, 40, 0), euler_to_matrix(turned.rx, turned.ry, turned.rz)),
            15.0,
            places=2,
        )
        self.assertNotAlmostEqual(turned.rz, 15.0, places=0)

        # The upright side is stored with Z at 180. That flag is not the twist.
        side = _tool(rx=0, ry=90, rz=180)
        vx, vy, vz = _upright_euler(side.rx, side.ry, side.rz)
        spun = camera_twist(side, 20)
        side_aim = view_in_model(vx, vy, vz)
        spun_aim = view_in_model(spun.rx, spun.ry, spun.rz)
        self.assertAlmostEqual(sum(a * b for a, b in zip(side_aim, spun_aim)), 1.0, places=3)
        self.assertLess(abs(spun.rx), 90.0)

    def test_scribble_orbit_does_not_add_twist(self) -> None:
        from rotatrix_zbrush.viewport import twist_should_apply

        self.assertFalse(twist_should_apply(8.0, 1.0))
        self.assertFalse(twist_should_apply(4.0, 3.0))
        self.assertTrue(twist_should_apply(0.0, 2.0))


if __name__ == "__main__":
    unittest.main()
