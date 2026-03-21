from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from sdk.core.clock import SystemClock
from sdk.core.frame import SensorFrame

from .base import SensorAdapter

try:
    from sensors.realsense_sensor import (
        RealsenseConfig as LowLevelRealsenseConfig,
        RealsenseIMUConfig as LowLevelRealsenseIMUConfig,
        RealsenseImageStreamConfig as LowLevelRealsenseImageStreamConfig,
        RealsensePointCloudConfig as LowLevelRealsensePointCloudConfig,
        RealsenseSensor,
    )
except Exception as exc:  # pragma: no cover
    LowLevelRealsenseConfig = None
    LowLevelRealsenseIMUConfig = None
    LowLevelRealsenseImageStreamConfig = None
    LowLevelRealsensePointCloudConfig = None
    RealsenseSensor = None
    _REALSENSE_IMPORT_ERROR = exc
else:  # pragma: no cover
    _REALSENSE_IMPORT_ERROR = None


@dataclass(frozen=True)
class RealSenseImageConfig:
    width: int = 640
    height: int = 480
    fps: int = 30


@dataclass(frozen=True)
class RealSenseIMUConfig:
    accel_fps: int = 250
    gyro_fps: int = 200
    max_samples_per_frame: int = 512


@dataclass(frozen=True)
class RealSenseDerivedConfig:
    enable_aligned_depth_to_color: bool = False
    enable_pointcloud: bool = False
    pointcloud_colored: bool = True


@dataclass(frozen=True)
class RealSenseConfig:
    name: str = "realsense"
    serial_number: str | None = None
    enable_color: bool = True
    enable_depth: bool = True
    enable_ir1: bool = False
    enable_ir2: bool = False
    enable_imu: bool = False
    width: int = 640
    height: int = 480
    fps: int = 30
    color: RealSenseImageConfig = field(default_factory=RealSenseImageConfig)
    depth: RealSenseImageConfig = field(default_factory=RealSenseImageConfig)
    infrared: RealSenseImageConfig = field(default_factory=RealSenseImageConfig)
    imu: RealSenseIMUConfig = field(default_factory=RealSenseIMUConfig)
    derived: RealSenseDerivedConfig = field(default_factory=RealSenseDerivedConfig)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RealSenseConfig":
        payload = dict(payload)
        width = int(payload.get("width", 640))
        height = int(payload.get("height", 480))
        fps = int(payload.get("fps", 30))

        def _image_config(key: str) -> RealSenseImageConfig:
            section = payload.get(key, {})
            if not isinstance(section, dict):
                raise ValueError(f"realsense.{key} 配置必须是对象")
            return RealSenseImageConfig(
                width=int(section.get("width", width)),
                height=int(section.get("height", height)),
                fps=int(section.get("fps", fps)),
            )

        imu_section = payload.get("imu", {})
        if not isinstance(imu_section, dict):
            raise ValueError("realsense.imu 配置必须是对象")
        derived_section = payload.get("derived", {})
        if not isinstance(derived_section, dict):
            raise ValueError("realsense.derived 配置必须是对象")
        legacy_align_to_color = bool(payload.get("align_to_color", False))

        return cls(
            name=str(payload.get("name", "realsense")),
            serial_number=payload.get("serial_number"),
            enable_color=bool(payload.get("enable_color", True)),
            enable_depth=bool(payload.get("enable_depth", True)),
            enable_ir1=bool(payload.get("enable_ir1", False)),
            enable_ir2=bool(payload.get("enable_ir2", False)),
            enable_imu=bool(payload.get("enable_imu", False)),
            width=width,
            height=height,
            fps=fps,
            color=_image_config("color"),
            depth=_image_config("depth"),
            infrared=_image_config("infrared"),
            imu=RealSenseIMUConfig(
                accel_fps=int(imu_section.get("accel_fps", 250)),
                gyro_fps=int(imu_section.get("gyro_fps", 200)),
                max_samples_per_frame=int(imu_section.get("max_samples_per_frame", 512)),
            ),
            derived=RealSenseDerivedConfig(
                enable_aligned_depth_to_color=bool(
                    derived_section.get("enable_aligned_depth_to_color", legacy_align_to_color)
                ),
                enable_pointcloud=bool(derived_section.get("enable_pointcloud", False)),
                pointcloud_colored=bool(derived_section.get("pointcloud_colored", True)),
            ),
        )

    def to_low_level_config(self) -> LowLevelRealsenseConfig:
        if LowLevelRealsenseConfig is None:
            raise RuntimeError(f"无法导入底层 RealSense 传感器: {_REALSENSE_IMPORT_ERROR}")
        return LowLevelRealsenseConfig(
            name=self.name,
            serial_number=self.serial_number,
            enable_color=self.enable_color,
            enable_depth=self.enable_depth,
            enable_ir1=self.enable_ir1,
            enable_ir2=self.enable_ir2,
            enable_imu=self.enable_imu,
            enable_aligned_depth_to_color=self.derived.enable_aligned_depth_to_color,
            enable_pointcloud=self.derived.enable_pointcloud,
            color=LowLevelRealsenseImageStreamConfig(
                width=self.color.width,
                height=self.color.height,
                fps=self.color.fps,
            ),
            depth=LowLevelRealsenseImageStreamConfig(
                width=self.depth.width,
                height=self.depth.height,
                fps=self.depth.fps,
            ),
            infrared=LowLevelRealsenseImageStreamConfig(
                width=self.infrared.width,
                height=self.infrared.height,
                fps=self.infrared.fps,
            ),
            imu=LowLevelRealsenseIMUConfig(
                accel_fps=self.imu.accel_fps,
                gyro_fps=self.imu.gyro_fps,
                max_samples_per_frame=self.imu.max_samples_per_frame,
            ),
            pointcloud=LowLevelRealsensePointCloudConfig(
                colored=self.derived.pointcloud_colored,
            ),
        )


class RealSenseRGBDAdapter(SensorAdapter):
    def __init__(
        self,
        config: RealSenseConfig,
        clock: SystemClock | None = None,
    ) -> None:
        super().__init__(config.name, "realsense", "rgbd")
        self.config = config
        self.clock = clock or SystemClock()
        self.sensor = RealsenseSensor(config.to_low_level_config()) if RealsenseSensor is not None else None

    def start(self) -> None:
        if self.sensor is None:
            raise RuntimeError(f"无法导入底层 RealSense 传感器: {_REALSENSE_IMPORT_ERROR}")
        self.sensor.start()

    def stop(self) -> None:
        if self.sensor is not None:
            self.sensor.stop()

    def read_frame(self) -> SensorFrame | None:
        if self.sensor is None:
            return None

        payload, _timestamp, frame_id, time_info = self.sensor.get_data_with_time_info()
        if payload is None:
            return None

        device_time = _select_device_time_seconds(payload.get("metadata", {}))
        frame_time = self.clock.capture_at(
            host_time_ns=time_info.get("host_capture_time_ns"),
            monotonic_time_ns=time_info.get("monotonic_capture_time_ns"),
            device_time=device_time,
            device_time_ns=int(round(device_time * 1_000_000_000)) if device_time is not None else None,
            host_arrival_time_ns=time_info.get("host_arrival_time_ns"),
            host_read_start_time_ns=time_info.get("host_read_start_time_ns"),
            host_read_end_time_ns=time_info.get("host_read_end_time_ns"),
        )
        metadata = payload.get("metadata", {})
        normalized_payload = {
            key: _clone_value(value)
            for key, value in payload.items()
            if key != "metadata"
        }
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=frame_id,
            time=frame_time,
            payload=normalized_payload,
            metadata=_clone_value(metadata),
        )

    def get_metadata(self) -> dict[str, object]:
        runtime_metadata = {}
        if self.sensor is not None:
            runtime_metadata = _clone_value(getattr(self.sensor, "_runtime_metadata", {}))
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "name": self.config.name,
            "serial_number": self.config.serial_number,
            "enable_color": self.config.enable_color,
            "enable_depth": self.config.enable_depth,
            "enable_ir1": self.config.enable_ir1,
            "enable_ir2": self.config.enable_ir2,
            "enable_imu": self.config.enable_imu,
            "width": self.config.width,
            "height": self.config.height,
            "fps": self.config.fps,
            "stream_config": {
                "color": {
                    "width": self.config.color.width,
                    "height": self.config.color.height,
                    "fps": self.config.color.fps,
                },
                "depth": {
                    "width": self.config.depth.width,
                    "height": self.config.depth.height,
                    "fps": self.config.depth.fps,
                },
                "infrared": {
                    "width": self.config.infrared.width,
                    "height": self.config.infrared.height,
                    "fps": self.config.infrared.fps,
                },
                "imu": {
                    "accel_fps": self.config.imu.accel_fps,
                    "gyro_fps": self.config.imu.gyro_fps,
                    "max_samples_per_frame": self.config.imu.max_samples_per_frame,
                },
                "derived": {
                    "enable_aligned_depth_to_color": self.config.derived.enable_aligned_depth_to_color,
                    "enable_pointcloud": self.config.derived.enable_pointcloud,
                    "pointcloud_colored": self.config.derived.pointcloud_colored,
                },
            },
            "runtime": runtime_metadata,
        }

    def get_status(self) -> dict[str, object]:
        if self.sensor is None:
            return {"running": False, "frame_count": 0, "latest_timestamp": 0.0}
        return {
            "running": self.sensor.running,
            "frame_count": self.sensor.frame_count,
            "latest_timestamp": self.sensor.latest_timestamp,
        }

    def is_ready(self) -> bool:
        return self.sensor.is_calibrated() if self.sensor is not None else False

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        return self.sensor.wait_until_calibrated(timeout=timeout) if self.sensor is not None else False


def _clone_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.copy()
    if isinstance(value, dict):
        return {key: _clone_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_clone_value(item) for item in value]
    return value


def _select_device_time_seconds(metadata: dict[str, Any]) -> float | None:
    frame_info = metadata.get("frame_info", {})
    for key in ("color", "depth", "ir1", "ir2"):
        info = frame_info.get(key)
        if isinstance(info, dict) and "timestamp_ms" in info:
            return float(info["timestamp_ms"]) / 1000.0
    return None
