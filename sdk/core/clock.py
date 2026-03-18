from __future__ import annotations

import time

from .frame import FrameTime


class SystemClock:
    def capture(
        self,
        *,
        device_time: float | None = None,
        device_time_ns: int | None = None,
        aligned_time: float | None = None,
        aligned_time_ns: int | None = None,
        host_arrival_time_ns: int | None = None,
        host_read_start_time_ns: int | None = None,
        host_read_end_time_ns: int | None = None,
    ) -> FrameTime:
        host_time_ns = time.time_ns()
        monotonic_time_ns = time.perf_counter_ns()
        return FrameTime(
            host_time_ns=host_time_ns,
            monotonic_time_ns=monotonic_time_ns,
            device_time=device_time,
            device_time_ns=device_time_ns,
            aligned_time=aligned_time,
            aligned_time_ns=aligned_time_ns,
            host_arrival_time_ns=host_arrival_time_ns if host_arrival_time_ns is not None else host_time_ns,
            host_read_start_time_ns=host_read_start_time_ns,
            host_read_end_time_ns=host_read_end_time_ns,
        )

    def capture_at(
        self,
        *,
        host_time: float | None = None,
        host_time_ns: int | None = None,
        monotonic_time: float | None = None,
        monotonic_time_ns: int | None = None,
        device_time: float | None = None,
        device_time_ns: int | None = None,
        aligned_time: float | None = None,
        aligned_time_ns: int | None = None,
        host_arrival_time: float | None = None,
        host_arrival_time_ns: int | None = None,
        host_read_start_time_ns: int | None = None,
        host_read_end_time_ns: int | None = None,
    ) -> FrameTime:
        if host_time is None and host_time_ns is None:
            host_time_ns = time.time_ns()
        if monotonic_time is None and monotonic_time_ns is None:
            monotonic_time_ns = time.perf_counter_ns()

        return FrameTime(
            host_time=host_time,
            host_time_ns=host_time_ns,
            monotonic_time=monotonic_time,
            monotonic_time_ns=monotonic_time_ns,
            device_time=device_time,
            device_time_ns=device_time_ns,
            aligned_time=aligned_time,
            aligned_time_ns=aligned_time_ns,
            host_arrival_time=host_arrival_time,
            host_arrival_time_ns=host_arrival_time_ns,
            host_read_start_time_ns=host_read_start_time_ns,
            host_read_end_time_ns=host_read_end_time_ns,
        )
