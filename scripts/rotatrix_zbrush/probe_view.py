# SPDX-License-Identifier: GPL-3.0-or-later
"""Photograph the canvas at known rotations.

Load from ZScript: Python Scripting: Load. This is not a startup plugin.

The tool stays the one already on the canvas. Only its rotation is changed,
then put back. Each picture is what that rotation actually shows, plus the
rotation the twist math would write for a 20 degree turn of the same pose.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import zbrush.commands as zbc

OUT = Path(r"C:\Users\SeanHewitt\AppData\Local\Temp\RotatrixZBrushView")
_SCRIPTS = Path(r"C:\Users\SeanHewitt\Documents\maya\modules\rotatrix_maya\scripts")
_LIB = Path(r"C:\Users\SeanHewitt\Documents\maya\modules\rotatrix_maya\lib")

# Coarse on purpose. A full-document grab would sit in pixol_pick for a long time.
_COLUMNS = 96
_ROWS = 72


def _png(path: Path, width: int, height: int, rgb: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    rows = []
    stride = width * 3
    for y in range(height):
        rows.append(b"\x00" + rgb[y * stride:(y + 1) * stride])
    image = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(image)


def _values() -> list[float]:
    return [float(value) for value in zbc.get_transform()]


def _set_rotation(original: list[float], rx: float, ry: float, rz: float) -> list[float]:
    zbc.set_transform(
        x_position=original[0], y_position=original[1], z_position=original[2],
        x_scale=original[3], y_scale=original[4], z_scale=original[5],
        x_rotate=rx, y_rotate=ry, z_rotate=rz,
    )
    zbc.update(redraw_ui=True)
    return _values()


def _grab(path: Path) -> None:
    width = float(zbc.get("Document:Width"))
    height = float(zbc.get("Document:Height"))
    x0, x1 = width * 0.12, width * 0.88
    y0, y1 = height * 0.12, height * 0.88
    rgb = bytearray(_COLUMNS * _ROWS * 3)
    index = 0
    for row in range(_ROWS):
        v = y0 + (y1 - y0) * (row / max(_ROWS - 1, 1))
        for col in range(_COLUMNS):
            h = x0 + (x1 - x0) * (col / max(_COLUMNS - 1, 1))
            rgb[index] = max(0, min(255, int(zbc.pixol_pick(2, h, v))))
            rgb[index + 1] = max(0, min(255, int(zbc.pixol_pick(3, h, v))))
            rgb[index + 2] = max(0, min(255, int(zbc.pixol_pick(4, h, v))))
            index += 3
    _png(path, _COLUMNS, _ROWS, bytes(rgb))


def _normal(h: float, v: float) -> str:
    return "{:.3f},{:.3f},{:.3f}".format(
        zbc.pixol_pick(6, h, v),
        zbc.pixol_pick(7, h, v),
        zbc.pixol_pick(8, h, v),
    )


def _twist_of(rx: float, ry: float, rz: float) -> tuple[float, float, float] | None:
    for folder in (_SCRIPTS, _LIB):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
    try:
        from rotatrix_zbrush.view import ToolTransform, camera_twist
    except Exception as exc:
        print(f"twist math unavailable: {exc!r}")
        return None
    spun = camera_twist(ToolTransform(0, 0, 0, 1, 1, 1, rx, ry, rz), 20.0)
    return spun.rx, spun.ry, spun.rz


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    original = _values()
    notes: list[str] = ["requested stored file normal_at_center"]
    bases = (
        ("front", 0.0, 0.0, 0.0),
        ("yaw45", 0.0, 45.0, 0.0),
        ("yaw90", 0.0, 90.0, 0.0),
        ("yaw90_upright", 0.0, 90.0, 180.0),
        ("yaw135_upright", 0.0, 135.0, 180.0),
        ("back_upright", 0.0, 180.0, 180.0),
        ("pitch45", -45.0, 0.0, 0.0),
        ("pitch80", -80.0, 0.0, 0.0),
        ("over_top", 80.0, 180.0, 0.0),
        ("three_quarter", -25.0, 40.0, 0.0),
    )
    shots: list[tuple[str, float, float, float]] = []
    for name, rx, ry, rz in bases:
        shots.append((name, rx, ry, rz))
        twisted = _twist_of(rx, ry, rz)
        if twisted is not None:
            shots.append((f"{name}_twist20", twisted[0], twisted[1], twisted[2]))
    width = float(zbc.get("Document:Width"))
    height = float(zbc.get("Document:Height"))
    center = (width * 0.5, height * 0.5)
    try:
        for index, (name, rx, ry, rz) in enumerate(shots, start=1):
            zbc.set_notebar_text(f"Rotatrix view {index}/{len(shots)} {name}", index / len(shots))
            stored = _set_rotation(original, rx, ry, rz)
            filename = f"{index:02d}_{name}.png"
            _grab(OUT / filename)
            normal = _normal(*center)
            line = (
                f"{name} requested {rx:.3f} {ry:.3f} {rz:.3f} "
                f"stored {stored[6]:.3f} {stored[7]:.3f} {stored[8]:.3f} "
                f"file {filename} normal {normal}"
            )
            notes.append(line)
            print(line)
            (OUT / "index.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    finally:
        zbc.set_transform(*original)
        zbc.update(redraw_ui=True)
        zbc.set_notebar_text("", 0)
        print(f"restored, pictures in {OUT}")


main()
