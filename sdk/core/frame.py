from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FrameTime:
    host_time: float
    monotonic_time: float
    device_time: float | None = None
    aligned_time: float | None = None


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
    metadata: dict[str, Any] = field(default_factory=dict)
