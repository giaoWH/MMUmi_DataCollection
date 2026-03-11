from __future__ import annotations

import time

from .frame import FrameTime


class SystemClock:
    def capture(
        self,
        *,
        device_time: float | None = None,
        aligned_time: float | None = None,
    ) -> FrameTime:
        return FrameTime(
            host_time=time.time(),
            monotonic_time=time.perf_counter(),
            device_time=device_time,
            aligned_time=aligned_time,
        )

    def capture_at(
        self,
        *,
        host_time: float,
        device_time: float | None = None,
        aligned_time: float | None = None,
    ) -> FrameTime:
        return FrameTime(
            host_time=host_time,
            monotonic_time=time.perf_counter(),
            device_time=device_time,
            aligned_time=aligned_time,
        )
