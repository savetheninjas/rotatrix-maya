# SPDX-License-Identifier: GPL-3.0-or-later
"""Write view_gnomon.obj, a calibration model for reading ZBrush's canvas.

Positive axes have heads. Negative axes are short blunt pegs.

- +Z is the long square nose. At the snapped front it points at the screen.
- +Y is the medium round arrow, with a fin on the +X side. Screen-up at the front.
- +X is the short triangular arrow. Screen-right at the front.
"""

from __future__ import annotations

import math
from pathlib import Path


def _along(axis: str, length: float, radius_y: float, radius_z: float) -> tuple[float, float, float]:
    if axis == "z":
        return radius_y, radius_z, length
    if axis == "y":
        return radius_y, length, radius_z
    return length, radius_y, radius_z


class Mesh:
    def __init__(self) -> None:
        self.verts: list[tuple[float, float, float]] = []
        self.groups: list[tuple[str, list[tuple[int, int, int]]]] = []

    def add(self, name: str, verts: list[tuple[float, float, float]], faces: list[tuple[int, int, int]]) -> None:
        base = len(self.verts)
        self.verts.extend(verts)
        self.groups.append((name, [(a + base, b + base, c + base) for a, b, c in faces]))

    def write(self, path: Path) -> None:
        lines = ["# Rotatrix view gnomon. +Z long square, +Y medium round with a fin, +X short triangle."]
        for x, y, z in self.verts:
            lines.append(f"v {x:.5f} {y:.5f} {z:.5f}")
        for name, faces in self.groups:
            lines.append(f"o {name}")
            for a, b, c in faces:
                lines.append(f"f {a + 1} {b + 1} {c + 1}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ring(axis: str, length: float, radius: float, sides: int, phase: float = 0.0) -> list[tuple[float, float, float]]:
    points = []
    for index in range(sides):
        angle = phase + math.tau * index / sides
        points.append(_along(axis, length, math.cos(angle) * radius, math.sin(angle) * radius))
    return points


def _tube(axis: str, start: float, end: float, radius: float, sides: int, phase: float = 0.0) -> tuple[list, list]:
    verts = _ring(axis, start, radius, sides, phase) + _ring(axis, end, radius, sides, phase)
    faces: list[tuple[int, int, int]] = []
    for index in range(sides):
        nxt = (index + 1) % sides
        a, b = index, nxt
        c, d = index + sides, nxt + sides
        faces.append((a, c, b))
        faces.append((b, c, d))
    return verts, faces


def _cap(axis: str, length: float, radius: float, sides: int, outward: bool, phase: float = 0.0) -> tuple[list, list]:
    center = _along(axis, length, 0.0, 0.0)
    ring = _ring(axis, length, radius, sides, phase)
    verts = [center, *ring]
    faces = []
    for index in range(sides):
        nxt = (index + 1) % sides
        if outward:
            faces.append((0, 1 + index, 1 + nxt))
        else:
            faces.append((0, 1 + nxt, 1 + index))
    return verts, faces


def _cone(axis: str, start: float, end: float, radius: float, sides: int, phase: float = 0.0) -> tuple[list, list]:
    tip = _along(axis, end, 0.0, 0.0)
    ring = _ring(axis, start, radius, sides, phase)
    verts = [tip, *ring]
    faces = []
    for index in range(sides):
        nxt = (index + 1) % sides
        faces.append((0, 1 + nxt, 1 + index))
    cap_verts, cap_faces = _cap(axis, start, radius, sides, outward=False, phase=phase)
    base = len(verts)
    faces.extend((a + base, b + base, c + base) for a, b, c in cap_faces)
    return verts + cap_verts, faces


def _arrow(axis: str, length: float, radius: float, sides: int, phase: float = 0.0) -> list[tuple[str, list, list]]:
    head = length * 0.28
    shaft_end = length - head
    shaft_v, shaft_f = _tube(axis, 0.12, shaft_end, radius, sides, phase)
    cap_v, cap_f = _cap(axis, 0.12, radius, sides, outward=False, phase=phase)
    head_v, head_f = _cone(axis, shaft_end, length, radius * 2.1, sides, phase)
    return [
        (f"{axis}_shaft", shaft_v, shaft_f),
        (f"{axis}_shaft_cap", cap_v, cap_f),
        (f"{axis}_head", head_v, head_f),
    ]


def _peg(axis: str, length: float, radius: float, sides: int) -> list[tuple[str, list, list]]:
    shaft_v, shaft_f = _tube(axis, -length, -0.12, radius, sides)
    cap_v, cap_f = _cap(axis, -length, radius, sides, outward=True)
    return [(f"neg_{axis}", shaft_v, shaft_f), (f"neg_{axis}_cap", cap_v, cap_f)]


def _fin() -> tuple[list, list]:
    """A plate on the +X side of the up arrow, so roll has a direction."""
    y0, y1 = 0.28, 0.62
    x0, x1 = 0.08, 0.28
    z0, z1 = -0.025, 0.025
    corners = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    quads = [
        (0, 1, 2, 3), (4, 7, 6, 5),
        (0, 4, 5, 1), (3, 2, 6, 7),
        (0, 3, 7, 4), (1, 5, 6, 2),
    ]
    faces = []
    for a, b, c, d in quads:
        faces.append((a, b, c))
        faces.append((a, c, d))
    return corners, faces


def build() -> Mesh:
    mesh = Mesh()
    for name, verts, faces in _arrow("z", 1.35, 0.09, 4, phase=math.pi / 4):
        mesh.add(name, verts, faces)
    for name, verts, faces in _arrow("y", 0.95, 0.07, 10):
        mesh.add(name, verts, faces)
    for name, verts, faces in _arrow("x", 0.62, 0.07, 3, phase=math.pi / 2):
        mesh.add(name, verts, faces)
    for axis, length in (("z", 0.28), ("y", 0.22), ("x", 0.18)):
        for name, verts, faces in _peg(axis, length, 0.035, 6):
            mesh.add(name, verts, faces)
    fin_v, fin_f = _fin()
    mesh.add("up_fin", fin_v, fin_f)
    return mesh


def main() -> None:
    path = Path(__file__).with_name("view_gnomon.obj")
    build().write(path)
    print(path)


if __name__ == "__main__":
    main()
