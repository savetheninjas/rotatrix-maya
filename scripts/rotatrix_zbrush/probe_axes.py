# SPDX-License-Identifier: GPL-3.0-or-later
"""Fine sample of the rotation folds.

Load from ZScript: Python Scripting: Load. This is not a startup plugin.

One-pixel strokes cross the four places a full turn folded in the 10° grid.
Typed angles then walk those same neighborhoods in 0.01° and 0.001° steps,
and a short list of exact values is written twice to see if the result settles.
The tool transform is restored at the end. Strokes stay off the document.
"""

from __future__ import annotations

from pathlib import Path

import zbrush.commands as zbc

OUT = Path(r"C:\Users\SeanHewitt\AppData\Local\Temp\RotatrixZBrushProbeFine.txt")
SHIFT = "Preferences:Utilities:Shift"
ORIGIN = -4096.0

_lines: list[str] = []


def _say(line: str) -> None:
    _lines.append(line)
    print(line)
    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")


def _values() -> list[float]:
    return [float(v) for v in zbc.get_transform()]


def _rot(values: list[float]) -> str:
    return f"rx={values[6]:.4f} ry={values[7]:.4f} rz={values[8]:.4f}"


def _wrap(degrees: float) -> float:
    return (degrees + 180.0) % 360.0 - 180.0


def _near(a: float, b: float, limit: float = 1.0) -> bool:
    return abs(_wrap(a - b)) <= limit


def _folded(rz: float) -> bool:
    return abs(abs(_wrap(rz)) - 180.0) < 20.0 or abs(rz) > 90.0


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
    zbc.update(redraw_ui=False)
    return _values()


def _center() -> tuple[float, float]:
    width = float(zbc.get("Document:Width"))
    height = float(zbc.get("Document:Height"))
    if width < 16.0 or height < 16.0:
        raise RuntimeError(f"document is {width:g} by {height:g}")
    return width * 0.5, height * 0.5


def _drag(x0: float, y0: float, x1: float, y1: float) -> None:
    snapped = False
    try:
        if float(zbc.get(SHIFT)) != 0.0:
            zbc.set(SHIFT, 0)
            snapped = True
    except Exception:
        snapped = False
    try:
        coords = (x0, y0, x1, y1)
        if zbc.exists("1004"):
            zbc.click("1004", *coords)
        else:
            zbc.canvas_click(*coords)
        zbc.update(redraw_ui=False)
    finally:
        if snapped:
            zbc.set(SHIFT, 1)


def _stroke(axis: str, pixels: float, center: tuple[float, float]) -> list[float]:
    cx, cy = center
    if axis == "h":
        _drag(ORIGIN, cy, ORIGIN + pixels, cy)
    else:
        _drag(cx, ORIGIN, cx, ORIGIN + pixels)
    return _values()


def _pixel_sign(axis: str, center: tuple[float, float]) -> float:
    _set_rotation(0.0, 0.0, 0.0)
    before = _values()
    _stroke(axis, 40.0, center)
    after = _values()
    primary = 7 if axis == "h" else 6
    delta = _wrap(after[primary] - before[primary])
    _say(
        f"calibration {axis} 40px "
        f"drx={_wrap(after[6] - before[6]):+.4f} "
        f"dry={_wrap(after[7] - before[7]):+.4f} "
        f"drz={_wrap(after[8] - before[8]):+.4f}"
    )
    if abs(delta) < 0.2:
        raise RuntimeError(f"{axis} calibration did not turn the model")
    return 1.0 if delta > 0.0 else -1.0


def _mark(previous: list[float] | None, current: list[float]) -> str:
    if previous is None:
        return ""
    jumps = [
        name
        for name, index in (("rx", 6), ("ry", 7), ("rz", 8))
        if abs(_wrap(current[index] - previous[index])) > 20.0
    ]
    if not jumps:
        return ""
    return " FLIP " + ",".join(jumps)


def _pixel_window(
    title: str,
    axis: str,
    pixel: float,
    center: tuple[float, float],
    ready,
    done,
    coarse_limit: int = 40,
    fine_limit: int = 80,
) -> None:
    _say("")
    _say(title)
    _set_rotation(0.0, 0.0, 0.0)
    coarse = 10.0 * pixel
    previous = _values()
    _say(f"start {_rot(previous)}")
    for _ in range(coarse_limit):
        if ready(_values()):
            break
        previous = _stroke(axis, coarse, center)
    else:
        _say("coarse approach stopped at its limit")
    _say(f"fine start {_rot(_values())}")
    previous = _values()
    for index in range(1, fine_limit + 1):
        current = _stroke(axis, pixel, center)
        _say(f"px {index} {_rot(current)}{_mark(previous, current)}")
        previous = current
        if done(current):
            for extra in range(index + 1, index + 6):
                current = _stroke(axis, pixel, center)
                _say(f"px {extra} {_rot(current)}{_mark(previous, current)}")
                previous = current
            return
    _say("fine window reached its pixel limit")


def _typed_sweep(title: str, axis: int, start: float, stop: float, step: float, base: tuple[float, float, float]) -> None:
    _say("")
    _say(title)
    previous: list[float] | None = None
    milli_start = int(round(start * 1000.0))
    milli_stop = int(round(stop * 1000.0))
    milli_step = int(round(step * 1000.0))
    if milli_step == 0:
        raise RuntimeError(f"step is too small for {title}")
    for milli in range(milli_start, milli_stop + (1 if milli_step > 0 else -1), milli_step):
        degrees = milli / 1000.0
        angles = [base[0], base[1], base[2]]
        angles[axis] = degrees
        read = _set_rotation(*angles)
        changed = ""
        if any(abs(_wrap(read[6 + i] - angles[i])) > 0.05 for i in range(3)):
            changed = " CHANGED"
        _say(
            f"write rx={angles[0]:.4f} ry={angles[1]:.4f} rz={angles[2]:.4f} "
            f"read {_rot(read)}{changed}{_mark(previous, read)}"
        )
        previous = read


def _write_twice(rx: float, ry: float, rz: float) -> None:
    first = _set_rotation(rx, ry, rz)
    second = _set_rotation(first[6], first[7], first[8])
    changed = ""
    if any(abs(_wrap(first[6 + i] - (rx, ry, rz)[i])) > 0.05 for i in range(3)):
        changed = " CHANGED"
    settled = " settled" if all(_near(first[6 + i], second[6 + i], 0.02) for i in range(3)) else " moved again"
    _say(
        f"write rx={rx:.4f} ry={ry:.4f} rz={rz:.4f} "
        f"read {_rot(first)}{changed} again {_rot(second)}{settled}"
    )


def _one_pixel_scale(center: tuple[float, float]) -> None:
    _say("")
    _say("ONE PIXEL FROM THE FRONT, BOTH SIGNS")
    for axis, label in (("h", "sideways"), ("v", "up-down")):
        for sign in (1.0, -1.0):
            _set_rotation(0.0, 0.0, 0.0)
            before = _values()
            for count in range(1, 6):
                current = _stroke(axis, sign, center)
                _say(
                    f"{label} {sign:+.0f}px x{count} {_rot(current)} "
                    f"d from start rx={_wrap(current[6] - before[6]):+.4f} "
                    f"ry={_wrap(current[7] - before[7]):+.4f} "
                    f"rz={_wrap(current[8] - before[8]):+.4f}"
                )


def main() -> None:
    global _lines
    _lines = []
    OUT.parent.mkdir(parents=True, exist_ok=True)
    original = _values()
    try:
        speed = float(zbc.get("Preferences:Interface:Rotate Speed"))
    except Exception:
        speed = float("nan")
    center = _center()
    _say("fine rotation sample: 1px strokes at the folds, then typed angles")
    _say(f"rotate speed {speed:g}")
    _say(f"document center {center[0]:.1f} {center[1]:.1f}")
    _say(f"start {_rot(original)}")
    try:
        h_pixel = _pixel_sign("h", center)
        v_pixel = _pixel_sign("v", center)
        _say(f"sideways 1px sign toward +Y {h_pixel:+.0f}")
        _say(f"up-down 1px sign toward +X {v_pixel:+.0f}")
        _one_pixel_scale(center)

        _pixel_window(
            "STROKE sideways, approaching the Z=180 fold",
            "h",
            h_pixel,
            center,
            ready=lambda v: v[7] > 80.0 and not _folded(v[8]),
            done=lambda v: _folded(v[8]) and v[7] > 95.0,
        )
        _pixel_window(
            "STROKE sideways, leaving the Z=180 fold",
            "h",
            h_pixel,
            center,
            ready=lambda v: v[7] < -100.0 and _folded(v[8]),
            done=lambda v: not _folded(v[8]) and v[7] > -80.0,
        )
        _pixel_window(
            "STROKE up-down, approaching the Y=180 fold",
            "v",
            v_pixel,
            center,
            ready=lambda v: v[6] > 75.0 and not _folded(v[7]),
            done=lambda v: _folded(v[7]) and v[6] < 85.0,
        )
        _pixel_window(
            "STROKE up-down, leaving the Y=180 fold",
            "v",
            v_pixel,
            center,
            ready=lambda v: v[6] < -75.0 and _folded(v[7]),
            done=lambda v: not _folded(v[7]) and v[6] > -80.0,
        )

        _typed_sweep("TYPED X 88 to 92 by 0.01, Y=0 Z=0", 0, 88.0, 92.0, 0.01, (0.0, 0.0, 0.0))
        _typed_sweep("TYPED X 89.9 to 90.1 by 0.001, Y=0 Z=0", 0, 89.9, 90.1, 0.001, (0.0, 0.0, 0.0))
        _typed_sweep("TYPED X -88 to -92 by 0.01, Y=0 Z=0", 0, -88.0, -92.0, -0.01, (0.0, 0.0, 0.0))
        _typed_sweep("TYPED X -89.9 to -90.1 by 0.001, Y=0 Z=0", 0, -89.9, -90.1, -0.001, (0.0, 0.0, 0.0))
        _typed_sweep("TYPED X 88 to 92 by 0.01, Y=180 Z=0", 0, 88.0, 92.0, 0.01, (0.0, 180.0, 0.0))
        _typed_sweep("TYPED Y 88 to 92 by 0.01, X=0 Z=0", 1, 88.0, 92.0, 0.01, (0.0, 0.0, 0.0))
        _typed_sweep("TYPED Y 89.9 to 90.1 by 0.001, X=0 Z=0", 1, 89.9, 90.1, 0.001, (0.0, 0.0, 0.0))
        _typed_sweep("TYPED Y -88 to -92 by 0.01, X=0 Z=0", 1, -88.0, -92.0, -0.01, (0.0, 0.0, 0.0))
        _typed_sweep("TYPED Z -10 to 10 by 0.01 at the side X=0 Y=90", 2, -10.0, 10.0, 0.01, (0.0, 90.0, 0.0))
        _typed_sweep("TYPED Z 170 to 190 by 0.01 at the side X=0 Y=90", 2, 170.0, 190.0, 0.01, (0.0, 90.0, 0.0))

        _say("")
        _say("EXACT WRITES, THEN THE READBACK WRITTEN AGAIN")
        exact = (
            (89.0, 0.0, 0.0),
            (89.999, 0.0, 0.0),
            (90.0, 0.0, 0.0),
            (90.001, 0.0, 0.0),
            (91.0, 0.0, 0.0),
            (-89.0, 0.0, 0.0),
            (-89.999, 0.0, 0.0),
            (-90.0, 0.0, 0.0),
            (-90.001, 0.0, 0.0),
            (-91.0, 0.0, 0.0),
            (0.0, 89.0, 0.0),
            (0.0, 90.0, 0.0),
            (0.0, 91.0, 0.0),
            (0.0, 90.0, 180.0),
            (0.0, -90.0, 180.0),
            (0.0, 108.0, -180.0),
            (0.0, 99.0, 179.97),
            (-89.999, 0.0, 0.0),
            (-89.999, 180.0, 180.0),
            (89.28, -180.0, 0.0),
            (-87.84, 0.0, 0.0),
            (0.0, 0.0, 45.0),
            (0.0, 0.0, 90.0),
            (0.0, 0.0, 180.0),
            (45.0, 45.0, 45.0),
        )
        for rx, ry, rz in exact:
            _write_twice(rx, ry, rz)
        _say("")
        _say("done")
    finally:
        zbc.set_transform(*original)
        zbc.update(redraw_ui=False)
        _say(f"restored {_rot(_values())}")


main()
