# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure tests for pen camera math. No Maya."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from openaxis.geometry import Vec3  # noqa: E402
from rotatrix_maya.pen_math import (  # noqa: E402
    CameraFrame,
    apply_pen_delta,
    dolly,
    orbit,
    pan,
    roll,
    unwrap_degrees,
)


def _frame() -> CameraFrame:
    return CameraFrame(Vec3(0, 0, 5), Vec3(0, 0, 0), Vec3(0, 1, 0))


class UnwrapTests(unittest.TestCase):
    def test_crosses_zero(self) -> None:
        self.assertAlmostEqual(unwrap_degrees(350, 10), 20)
        self.assertAlmostEqual(unwrap_degrees(10, 350), -20)

    def test_short_way_across_the_negative_side(self) -> None:
        self.assertAlmostEqual(unwrap_degrees(-170, 170), -20)
        self.assertAlmostEqual(unwrap_degrees(170, -170), 20)

    def test_unchanged(self) -> None:
        self.assertAlmostEqual(unwrap_degrees(0, 0), 0)


class OrbitTests(unittest.TestCase):
    def test_yaw_right_moves_eye_toward_positive_x(self) -> None:
        moved = orbit(_frame(), Vec3(0, 0, 0), math.radians(90), 0)
        self.assertGreater(moved.eye.x, 4.0)
        self.assertAlmostEqual(moved.eye.y, 0, places=5)
        self.assertAlmostEqual(moved.eye.length(), 5, places=5)

    def test_pitch_up_moves_eye_down(self) -> None:
        # Positive pitch looks up: the eye drops below the pivot.
        moved = orbit(_frame(), Vec3(0, 0, 0), 0, math.radians(90))
        self.assertLess(moved.eye.y, -4.0)
        self.assertAlmostEqual(moved.eye.length(), 5, places=5)

    def test_pen_mapping_orbit_and_roll(self) -> None:
        frame, extent = apply_pen_delta(
            _frame(),
            Vec3(0, 0, 0),
            dx_px=100,
            dy_px=0,
            twist_degrees=90,
            orbiting=True,
            ortho_extent=None,
        )
        self.assertLess(frame.eye.x, -0.5)
        self.assertAlmostEqual(frame.eye.length(), 5, places=4)
        self.assertIsNone(extent)
        # Clockwise roll tips camera-up toward screen-right (+X while looking down -Z).
        self.assertGreater(frame.up.x, 0.5)
        self.assertAlmostEqual(frame.target.x, 0, places=4)
        self.assertAlmostEqual(frame.target.y, 0, places=4)
        self.assertAlmostEqual(frame.target.z, 0, places=4)

    def test_side_to_side_stays_level_and_centered(self) -> None:
        frame = _frame()
        for _ in range(8):
            frame, _extent = apply_pen_delta(
                frame,
                Vec3(0, 0, 0),
                dx_px=40,
                dy_px=0,
                twist_degrees=0,
                orbiting=True,
                ortho_extent=None,
            )
        self.assertAlmostEqual(frame.eye.y, 0, places=4)
        self.assertLess(frame.eye.x, -1.0)
        self.assertAlmostEqual(frame.up.x, 0, places=4)
        self.assertGreater(frame.up.y, 0.99)
        self.assertAlmostEqual(frame.target.length(), 0, places=5)

    def test_vertical_stroke_does_not_yaw(self) -> None:
        frame, _extent = apply_pen_delta(
            _frame(),
            Vec3(0, 0, 0),
            dx_px=0,
            dy_px=80,
            twist_degrees=0,
            orbiting=True,
            ortho_extent=None,
        )
        self.assertAlmostEqual(frame.eye.x, 0, places=4)
        self.assertLess(frame.eye.y, -0.5)
        self.assertGreater(frame.up.y, 0.9)


class PanZoomTests(unittest.TestCase):
    def test_pen_pan_is_opposite_the_stroke(self) -> None:
        frame, _extent = apply_pen_delta(
            _frame(),
            Vec3(0, 0, 0),
            dx_px=40,
            dy_px=40,
            twist_degrees=0,
            orbiting=False,
            ortho_extent=None,
        )
        self.assertLess(frame.eye.x, -0.05)
        self.assertLess(frame.eye.y, -0.05)

    def test_pan_keeps_the_view_direction(self) -> None:
        frame = pan(_frame(), 10, 0, 0.01)
        direction = (frame.target - frame.eye).normalized()
        self.assertAlmostEqual(direction.x, 0, places=5)
        self.assertAlmostEqual(direction.z, -1, places=5)
        self.assertGreater(frame.eye.x, 0)

    def test_dolly_in_shortens_distance(self) -> None:
        frame, factor = dolly(_frame(), 40, 0.01)
        self.assertLess(factor, 1.0)
        self.assertAlmostEqual(frame.target.z, 0, places=5)
        self.assertLess(frame.eye.z, 5)
        self.assertGreater(frame.eye.z, 0.01)

    def test_pen_pan_zoom_scales_ortho(self) -> None:
        frame, extent = apply_pen_delta(
            _frame(),
            Vec3(0, 0, 0),
            dx_px=0,
            dy_px=0,
            twist_degrees=30,
            orbiting=False,
            ortho_extent=10.0,
        )
        self.assertIsNotNone(extent)
        assert extent is not None
        self.assertLess(extent, 10.0)
        self.assertLess(frame.eye.z, 5)


class RollTests(unittest.TestCase):
    def test_roll_leaves_eye_and_target(self) -> None:
        frame = roll(_frame(), math.radians(90))
        self.assertAlmostEqual(frame.eye.z, 5, places=5)
        self.assertAlmostEqual(frame.target.z, 0, places=5)
        self.assertAlmostEqual(frame.up.x, 1, places=5)
        self.assertAlmostEqual(frame.up.y, 0, places=5)


if __name__ == "__main__":
    unittest.main()
