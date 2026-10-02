# SPDX-License-Identifier: GPL-3.0-or-later
"""Motion performance stats tracker."""

from __future__ import annotations

import time


class PerfStats:
    """Tracks per-motion FPS and latency stats."""

    __slots__ = (
        "_start_time", "_apply_count",
        "_total_refresh", "_max_refresh",
        "_total_pipeline", "_pipeline_count", "_max_pipeline",
        "_last_push", "last_report",
    )

    def __init__(self) -> None:
        self.last_report: dict | None = None
        self.reset()

    def reset(self) -> None:
        now = time.perf_counter()
        self._start_time = now
        self._apply_count = 0
        self._total_refresh = 0.0
        self._max_refresh = 0.0
        self._total_pipeline = 0.0
        self._pipeline_count = 0
        self._max_pipeline = 0.0
        self._last_push = now

    def record_frame(self, refresh_time: float, t_send: float | None) -> None:
        self._apply_count += 1
        self._total_refresh += refresh_time
        if refresh_time > self._max_refresh:
            self._max_refresh = refresh_time
        if t_send is not None:
            pipeline = time.time() - t_send
            self._total_pipeline += pipeline
            self._pipeline_count += 1
            if pipeline > self._max_pipeline:
                self._max_pipeline = pipeline

    def maybe_push(self, interval: float = 1.0) -> bool:
        now = time.perf_counter()
        if now - self._last_push < interval:
            return False
        self._push(now)
        return True

    def finalize(self) -> None:
        if self._apply_count > 0:
            self._push(time.perf_counter())

    def _push(self, now: float) -> None:
        self._last_push = now
        duration = now - self._start_time
        fps = self._apply_count / max(duration, 0.001)
        self.last_report = {
            "fps": round(fps),
            "duration": round(duration, 1),
            "refresh_avg": round(
                self._total_refresh / self._apply_count * 1000
            ) if self._apply_count else 0,
            "refresh_max": round(self._max_refresh * 1000),
            "pipe_avg": round(
                self._total_pipeline / max(self._pipeline_count, 1) * 1000
            ),
            "pipe_max": round(self._max_pipeline * 1000),
        }
