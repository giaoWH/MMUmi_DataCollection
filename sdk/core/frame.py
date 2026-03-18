from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def seconds_to_ns(value: float | None) -> int | None:
    if value is None:
        return None
    return int(round(value * 1_000_000_000))


def ns_to_seconds(value: int | None) -> float | None:
    if value is None:
        return None
    return value / 1_000_000_000.0


@dataclass(frozen=True)
class FrameTime:
    host_time: float | None = None
    monotonic_time: float | None = None
    device_time: float | None = None
    aligned_time: float | None = None
    host_time_ns: int | None = None
    monotonic_time_ns: int | None = None
    device_time_ns: int | None = None
    aligned_time_ns: int | None = None
    host_arrival_time: float | None = None
    host_arrival_time_ns: int | None = None
    host_read_start_time_ns: int | None = None
    host_read_end_time_ns: int | None = None

    def __post_init__(self) -> None:
        host_time_ns = self.host_time_ns if self.host_time_ns is not None else seconds_to_ns(self.host_time)
        monotonic_time_ns = (
            self.monotonic_time_ns
            if self.monotonic_time_ns is not None
            else seconds_to_ns(self.monotonic_time)
        )
        device_time_ns = self.device_time_ns if self.device_time_ns is not None else seconds_to_ns(self.device_time)
        aligned_time_ns = self.aligned_time_ns if self.aligned_time_ns is not None else seconds_to_ns(self.aligned_time)
        host_arrival_time_ns = (
            self.host_arrival_time_ns
            if self.host_arrival_time_ns is not None
            else seconds_to_ns(self.host_arrival_time)
        )

        object.__setattr__(self, "host_time_ns", host_time_ns)
        object.__setattr__(self, "monotonic_time_ns", monotonic_time_ns)
        object.__setattr__(self, "device_time_ns", device_time_ns)
        object.__setattr__(self, "aligned_time_ns", aligned_time_ns)
        object.__setattr__(self, "host_arrival_time_ns", host_arrival_time_ns)

        if self.host_time is None:
            object.__setattr__(self, "host_time", ns_to_seconds(host_time_ns))
        if self.monotonic_time is None:
            object.__setattr__(self, "monotonic_time", ns_to_seconds(monotonic_time_ns))
        if self.device_time is None:
            object.__setattr__(self, "device_time", ns_to_seconds(device_time_ns))
        if self.aligned_time is None:
            object.__setattr__(self, "aligned_time", ns_to_seconds(aligned_time_ns))
        if self.host_arrival_time is None:
            object.__setattr__(self, "host_arrival_time", ns_to_seconds(host_arrival_time_ns))


@dataclass
class SensorFrame:
    sensor_name: str
    sensor_type: str
    modality: str
    frame_id: int
    time: FrameTime
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrajectoryFrame:
    source: str
    frame_id: int
    time: FrameTime
    position: list[float]
    quaternion: list[float]
    tracking_state: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AlignedFrame:
    sequence_id: int
    aligned_time: float
    frames: dict[str, SensorFrame]
    missing_sensors: list[str]
    age_by_sensor: dict[str, float]
    aligned_time_ns: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.aligned_time_ns is None:
            self.aligned_time_ns = seconds_to_ns(self.aligned_time)
