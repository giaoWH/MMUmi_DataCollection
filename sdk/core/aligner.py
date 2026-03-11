from __future__ import annotations

from collections import deque
from typing import Deque

from .frame import AlignedFrame, SensorFrame


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

    def align(self, aligned_time: float) -> AlignedFrame:
        frames: dict[str, SensorFrame] = {}
        missing: list[str] = []
        dropped: list[str] = []
        age_by_sensor: dict[str, float] = {}

        for sensor_name in self.required_sensors:
            frame = self._select_frame(sensor_name, aligned_time)
            if frame is None:
                missing.append(sensor_name)
                continue

            age = aligned_time - frame.time.host_time
            if self.max_frame_age is not None and abs(age) > self.max_frame_age:
                missing.append(sensor_name)
                dropped.append(sensor_name)
                continue

            frames[sensor_name] = frame
            age_by_sensor[sensor_name] = age

        absolute_ages = [abs(age) for age in age_by_sensor.values()]
        age_stats = {
            "count": len(absolute_ages),
            "max_abs_age": max(absolute_ages) if absolute_ages else 0.0,
            "mean_abs_age": sum(absolute_ages) / len(absolute_ages) if absolute_ages else 0.0,
        }

        aligned = AlignedFrame(
            sequence_id=self._sequence_id,
            aligned_time=aligned_time,
            frames=frames,
            missing_sensors=missing,
            age_by_sensor=age_by_sensor,
            metadata={
                "strategy": self.strategy,
                "present_sensors": sorted(frames.keys()),
                "dropped_sensors": dropped,
                "age_stats": age_stats,
            },
        )
        self._sequence_id += 1
        return aligned

    def _select_frame(self, sensor_name: str, aligned_time: float) -> SensorFrame | None:
        buffer = self._buffers.get(sensor_name)
        if not buffer:
            return None

        candidates = list(buffer)
        if self.strategy == "latest_before":
            valid = [frame for frame in candidates if frame.time.host_time <= aligned_time]
            return valid[-1] if valid else None

        return min(
            candidates,
            key=lambda frame: abs(frame.time.host_time - aligned_time),
        )
