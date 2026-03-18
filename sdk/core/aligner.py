from __future__ import annotations

from collections import deque
from typing import Deque

from .frame import AlignedFrame, SensorFrame, ns_to_seconds, seconds_to_ns


class BufferedFrameAligner:
    def __init__(
        self,
        required_sensors: list[str],
        *,
        max_buffer_size: int = 256,
        max_frame_age: float | None = None,
        strategy: str = "nearest",
    ) -> None:
        if strategy not in {"nearest", "latest_before"}:
            raise ValueError(f"不支持的对齐策略: {strategy}")

        self.required_sensors = list(required_sensors)
        self.max_frame_age = max_frame_age
        self.strategy = strategy
        self._buffers: dict[str, Deque[SensorFrame]] = {
            sensor: deque(maxlen=max_buffer_size) for sensor in required_sensors
        }
        self._sequence_id = 0

    def add_frame(self, frame: SensorFrame) -> None:
        if frame.sensor_name not in self._buffers:
            self._buffers[frame.sensor_name] = deque(maxlen=256)
        self._buffers[frame.sensor_name].append(frame)

    def align(
        self,
        aligned_time: float,
        *,
        aligned_time_ns: int | None = None,
        aligned_monotonic_time_ns: int | None = None,
    ) -> AlignedFrame:
        aligned_time_ns = aligned_time_ns if aligned_time_ns is not None else seconds_to_ns(aligned_time)
        if aligned_time_ns is None:
            raise ValueError("aligned_time 或 aligned_time_ns 必须至少提供一个")
        aligned_monotonic_time_ns = (
            aligned_monotonic_time_ns
            if aligned_monotonic_time_ns is not None
            else aligned_time_ns
        )

        frames: dict[str, SensorFrame] = {}
        missing: list[str] = []
        dropped: list[str] = []
        age_by_sensor: dict[str, float] = {}
        age_by_sensor_ns: dict[str, int] = {}

        for sensor_name in self.required_sensors:
            frame = self._select_frame(sensor_name, aligned_monotonic_time_ns)
            if frame is None:
                missing.append(sensor_name)
                continue

            age_ns = aligned_monotonic_time_ns - self._frame_reference_time_ns(frame)
            age = ns_to_seconds(age_ns)
            if age is None:
                missing.append(sensor_name)
                continue
            if self.max_frame_age is not None and abs(age) > self.max_frame_age:
                missing.append(sensor_name)
                dropped.append(sensor_name)
                continue

            frames[sensor_name] = frame
            age_by_sensor[sensor_name] = age
            age_by_sensor_ns[sensor_name] = age_ns

        absolute_age_ns = [abs(age_ns) for age_ns in age_by_sensor_ns.values()]
        age_stats = {
            "count": len(absolute_age_ns),
            "max_abs_age": ns_to_seconds(max(absolute_age_ns)) if absolute_age_ns else 0.0,
            "mean_abs_age": (
                ns_to_seconds(sum(absolute_age_ns) // len(absolute_age_ns))
                if absolute_age_ns
                else 0.0
            ),
        }

        aligned = AlignedFrame(
            sequence_id=self._sequence_id,
            aligned_time=aligned_time,
            aligned_time_ns=aligned_time_ns,
            frames=frames,
            missing_sensors=missing,
            age_by_sensor=age_by_sensor,
            metadata={
                "strategy": self.strategy,
                "present_sensors": sorted(frames.keys()),
                "dropped_sensors": dropped,
                "aligned_monotonic_time_ns": aligned_monotonic_time_ns,
                "age_by_sensor_ns": age_by_sensor_ns,
                "age_stats": age_stats,
                "age_stats_ns": {
                    "count": len(absolute_age_ns),
                    "max_abs_age_ns": max(absolute_age_ns) if absolute_age_ns else 0,
                    "mean_abs_age_ns": (
                        sum(absolute_age_ns) // len(absolute_age_ns)
                        if absolute_age_ns
                        else 0
                    ),
                },
            },
        )
        self._sequence_id += 1
        return aligned

    def _select_frame(self, sensor_name: str, aligned_monotonic_time_ns: int) -> SensorFrame | None:
        buffer = self._buffers.get(sensor_name)
        if not buffer:
            return None

        candidates = list(buffer)
        if self.strategy == "latest_before":
            valid = [
                frame
                for frame in candidates
                if self._frame_reference_time_ns(frame) <= aligned_monotonic_time_ns
            ]
            return valid[-1] if valid else None

        return min(
            candidates,
            key=lambda frame: abs(self._frame_reference_time_ns(frame) - aligned_monotonic_time_ns),
        )

    def _frame_reference_time_ns(self, frame: SensorFrame) -> int:
        if frame.time.monotonic_time_ns is not None:
            return frame.time.monotonic_time_ns
        if frame.time.host_time_ns is not None:
            return frame.time.host_time_ns
        raise ValueError(f"帧缺少可用于对齐的纳秒级时间戳: {frame.sensor_name}")
