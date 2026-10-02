# SPDX-License-Identifier: GPL-3.0-or-later
"""Save canvas pictures of the view gnomon at known rotations.

Load from ZScript: Python Scripting: Load. This is not a startup plugin.

Import view_gnomon.obj, draw it large on the canvas, and turn Edit on first.
The script changes rotation only, then puts the tool back.
"""

from __future__ import annotations

from pathlib import Path

import zbrush.commands as zbc

SHOTS = Path(r"C:\Users\SeanHewitt\Documents\Rotatrix\scripts\rotatrix_zbrush\calibration\shots")
LOG = SHOTS / "capture_log.txt"
SHIFT = "Preferences:Utilities:Shift"
ORIGIN = -4096.0

TYPED = (
    ("front", 0.0, 0.0, 0.0),
    ("z30", 0.0, 0.0, 30.0),
    ("y45", 0.0, 45.0, 0.0),
    ("y90", 0.0, 90.0, 0.0),
    ("y90_z180", 0.0, 90.0, 180.0),
    # What a 20° view twist writes from Y=45, and from the upright side.
    ("twist_y45", -18.882, 41.641, 27.236),
    ("twist_side", -89.999, 70.0, 90.0),
    # Z added on the upright side, the model-axis turn.
    ("side_plus_z", 0.0, 90.0, 200.0),
    # Small steps around the upright side, to see which slider leans the view.
    ("side_xm30", -30.0, 90.0, 180.0),
    ("side_xm15", -15.0, 90.0, 180.0),
    ("side_x15", 15.0, 90.0, 180.0),
    ("side_x30", 30.0, 90.0, 180.0),
    ("side_y75", 0.0, 75.0, 180.0),
    ("side_y105", 0.0, 105.0, 180.0),
    ("side_z150", 0.0, 90.0, 150.0),
    ("side_z210", 0.0, 90.0, 210.0),
)

_lines: list[str] = []


def _say(line: str) -> None:
    _lines.append(line)
    print(line)
    LOG.write_text("\n".join(_lines) + "\n", encoding="utf-8")


def _values() -> list[float]:
    return [float(value) for value in zbc.get_transform()]


def _wrap(degrees: float) -> float:
    return (degrees + 180.0) % 360.0 - 180.0


def _set_rotation(rx: float, ry: float, rz: float) -> list[float]:
    values = _values()
    zbc.set_transform(
        x_position=values[0],
        y_position=values[1],
        z_position=values[2],
        x_scale=values[3],
        y_scale=values[4],
        z_scale=values[5],
        x_rotate=rx,
        y_rotate=ry,
        z_rotate=rz,
    )
    zbc.update(redraw_ui=True)
    return _values()


def _restore(original: list[float]) -> None:
    zbc.set_transform(
        x_position=original[0],
        y_position=original[1],
        z_position=original[2],
        x_scale=original[3],
        y_scale=original[4],
        z_scale=original[5],
        x_rotate=original[6],
        y_rotate=original[7],
        z_rotate=original[8],
    )
    zbc.update(redraw_ui=True)


def _tag(values: list[float]) -> str:
    return f"x{values[6]:.1f}_y{values[7]:.1f}_z{values[8]:.1f}".replace("-", "m")


def _save(label: str, values: list[float]) -> None:
    path = SHOTS / f"{label}__{_tag(values)}.png"
    zbc.set_next_filename(path.as_posix())
    zbc.press("Document:Export")
    size = path.stat().st_size if path.exists() else 0
    _say(f"{label} stored rx={values[6]:.4f} ry={values[7]:.4f} rz={values[8]:.4f} file={path.name} bytes={size}")


def _center() -> tuple[float, float]:
    width = float(zbc.get("Document:Width"))
    height = float(zbc.get("Document:Height"))
    return width * 0.5, height * 0.5


def _drag(pixels: float, center_y: float) -> None:
    snapped = False
    try:
        if float(zbc.get(SHIFT)) != 0.0:
            zbc.set(SHIFT, 0)
            snapped = True
    except Exception:
        snapped = False
    try:
        coords = (ORIGIN, center_y, ORIGIN + pixels, center_y)
        if zbc.exists("1004"):
            zbc.click("1004", *coords)
        else:
            zbc.canvas_click(*coords)
        zbc.update(redraw_ui=True)
    finally:
        if snapped:
            zbc.set(SHIFT, 1)


def _side_fold(values: list[float]) -> bool:
    turned = abs(_wrap(values[7])) >= 80.0
    upright = abs(abs(_wrap(values[8])) - 180.0) < 25.0
    return turned and upright


def _drag_to_side(center_y: float) -> list[float]:
    _set_rotation(0.0, 0.0, 0.0)
    last = _values()
    stalled = 0
    for _step in range(48):
        _drag(8.0, center_y)
        current = _values()
        moved = abs(_wrap(current[7] - last[7])) > 0.2 or abs(_wrap(current[8] - last[8])) > 0.2
        last = current
        if _side_fold(current):
            return current
        if not moved:
            stalled += 1
            if stalled >= 3:
                break
        else:
            stalled = 0
    return last


def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    original = _values()
    _say(
        "start "
        f"rx={original[6]:.4f} ry={original[7]:.4f} rz={original[8]:.4f}"
    )
    try:
        for label, rx, ry, rz in TYPED:
            stored = _set_rotation(rx, ry, rz)
            _save(label, stored)
        _center_y = _center()[1]
        dragged = _drag_to_side(_center_y)
        _save("side_drag", dragged)
    except Exception as exc:
        _say(f"stopped: {exc!r}")
    finally:
        _restore(original)
        _say("restored the tool")


main()
