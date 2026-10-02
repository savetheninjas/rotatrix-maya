# SPDX-License-Identifier: GPL-3.0-or-later
"""Orbit pivot for the Maya Rotatrix plugin.

No scene locators are created. Pivot order is controlled from the panel:
mouse raycast, selection, screen center, last used, then origin/target.
"""

from __future__ import annotations

import maya.api.OpenMaya as om
import maya.cmds as cmds
from openaxis.geometry import Vec3

from . import viewport
from .log import log

LEGACY_MARKER_NAME = "__rotatrixPivot"


class PivotEvaluator:
    """Determines orbit pivot from cursor raycast, selection, or viewport center."""

    def evaluate(self, settings: dict) -> tuple[Vec3, str] | None:
        if settings.get("pivot_mouse", True):
            pt = self.raycast_mouse_cursor()
            if pt is not None:
                return pt, "mouse"
        if settings.get("pivot_selection", True):
            pt = self.selection_center()
            if pt is not None:
                return pt, "selection"
        if settings.get("pivot_center", True):
            pt = self.raycast_center()
            if pt is not None:
                return pt, "center"
        return None

    @staticmethod
    def selection_center() -> Vec3 | None:
        try:
            selected = cmds.ls(selection=True, long=True) or []
            if not selected:
                return None
            bbox = cmds.exactWorldBoundingBox(selected)
        except Exception:
            return None
        return Vec3(
            (bbox[0] + bbox[3]) * 0.5,
            (bbox[1] + bbox[4]) * 0.5,
            (bbox[2] + bbox[5]) * 0.5,
        )

    @staticmethod
    def raycast_mouse_cursor() -> Vec3 | None:
        pixels = viewport.cursor_view_pixels()
        if pixels is None:
            return None
        x, y, view = pixels
        return _raycast_view(view, x, y)

    @staticmethod
    def raycast_center() -> Vec3 | None:
        pixels = viewport.view_center_pixels()
        if pixels is None:
            return None
        x, y, view = pixels
        return _raycast_view(view, x, y)


def evaluate_pivot(
    target: tuple[float, float, float],
    locked_pivot: tuple[float, float, float] | None,
    server_pivot: tuple[float, float, float] | None,
    evaluator: PivotEvaluator,
    settings: dict | None = None,
) -> tuple[float, float, float]:
    def _fmt(point):
        return f"({point[0]:.3f}, {point[1]:.3f}, {point[2]:.3f})"

    settings = settings or {}
    if locked_pivot is not None:
        log(f"pivot: locked {_fmt(locked_pivot)}")
        return locked_pivot

    result = evaluator.evaluate(settings)
    if result is not None:
        pivot_pt, method = result
        point = (pivot_pt.x, pivot_pt.y, pivot_pt.z)
        log(f"pivot: {method} {_fmt(point)}")
        return point

    if settings.get("pivot_last_used", True) and server_pivot is not None:
        log(f"pivot: last used {_fmt(server_pivot)}")
        return server_pivot

    fallback = settings.get("pivot_fallback", "origin")
    if fallback == "origin":
        log("pivot: origin (0.000, 0.000, 0.000)")
        return (0.0, 0.0, 0.0)
    log(f"pivot: target {_fmt(target)}")
    return target


def _raycast_view(view, x: int, y: int) -> Vec3 | None:
    ray = viewport.ray_from_view(view, x, y)
    if ray is None:
        return None
    origin, direction, max_param = ray
    best_param = max_param
    best_point = None

    it = om.MItDag(om.MItDag.kDepthFirst, om.MFn.kMesh)
    while not it.isDone():
        try:
            path = it.getPath()
            if not path.isVisible():
                it.next()
                continue
            fn_dag = om.MFnDagNode(path)
            if fn_dag.isIntermediateObject:
                it.next()
                continue
            hit = _intersect_mesh(path, origin, direction, best_param)
        except Exception:
            hit = None
        if hit is not None:
            point, param = hit
            if param < best_param:
                best_param = param
                best_point = point
        it.next()

    if best_point is None:
        return None
    return Vec3(best_point[0], best_point[1], best_point[2])


def _intersect_mesh(path, origin, direction, max_param):
    fn_mesh = om.MFnMesh(path)
    src = om.MFloatPoint(origin.x, origin.y, origin.z)
    vec = om.MFloatVector(direction.x, direction.y, direction.z)
    hit = None
    try:
        hit = fn_mesh.closestIntersection(
            src, vec, om.MSpace.kWorld, float(max_param), False,
        )
    except TypeError:
        try:
            hit = fn_mesh.closestIntersection(
                src, vec, None, None, False,
                om.MSpace.kWorld, float(max_param), False,
            )
        except Exception:
            return None
    except Exception:
        return None
    if not hit:
        return None
    point = hit[0]
    param = float(hit[1]) if len(hit) > 1 else 0.0
    return (point.x, point.y, point.z), param


def delete_legacy_marker() -> None:
    """Remove a leftover locator from older plugin versions, if present."""
    if not cmds.objExists(LEGACY_MARKER_NAME):
        return
    try:
        cmds.delete(LEGACY_MARKER_NAME)
    except Exception as exc:
        log(f"legacy marker cleanup: {exc!r}", "WARN")
