from __future__ import annotations

import time
from dataclasses import dataclass

from sdk.core.clock import SystemClock
from sdk.core.frame import SensorFrame

from .base import SensorAdapter


@dataclass(frozen=True)
class FTSensorConfig:
    name: str = "ft"
    port: str = "COM3"
    baudrate: int = 115200


@dataclass(frozen=True)
class IMUSensorConfig:
    name: str = "imu"
    port: str = "COM4"
    baudrate: int = 115200


class LegacyFTAdapter(SensorAdapter):
    def __init__(self, config: FTSensorConfig, clock: SystemClock | None = None) -> None:
        super().__init__(config.name, "ft_sensor", "force_torque")
        from sensors import FTSensor

        self.config = config
        self.clock = clock or SystemClock()
        self.sensor = FTSensor(port=config.port, baudrate=config.baudrate)

    def start(self) -> None:
        self.sensor.start()

    def stop(self) -> None:
        self.sensor.stop()

    def read_frame(self) -> SensorFrame | None:
        data, device_time, frame_id = self.sensor.get_data()
        if data is None:
            return None

        host_time = device_time if device_time > 0 else time.time()
        frame_time = self.clock.capture_at(host_time=host_time)
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=frame_id,
            time=frame_time,
            payload={
                "force_torque": data.tolist(),
                "force": data[:3].tolist(),
                "torque": data[3:].tolist(),
            },
            metadata={"units": {"force": "N", "torque": "Nm"}},
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "port": self.config.port,
            "baudrate": self.config.baudrate,
        }

    def get_status(self) -> dict[str, object]:
        return {
            "running": self.sensor.running,
            "frame_count": self.sensor.frame_count,
            "latest_timestamp": self.sensor.latest_timestamp,
        }


class LegacyIMUAdapter(SensorAdapter):
    def __init__(self, config: IMUSensorConfig, clock: SystemClock | None = None) -> None:
        super().__init__(config.name, "imu_sensor", "imu")
        from sensors import IMUSensor

        self.config = config
        self.clock = clock or SystemClock()
        self.sensor = IMUSensor(port=config.port, baudrate=config.baudrate)

    def start(self) -> None:
        self.sensor.start()

    def stop(self) -> None:
        self.sensor.stop()

    def read_frame(self) -> SensorFrame | None:
        data, device_time, frame_id = self.sensor.get_data()
        if data is None:
            return None

        host_time = device_time if device_time > 0 else time.time()
        frame_time = self.clock.capture_at(host_time=host_time)
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=frame_id,
            time=frame_time,
            payload={
                "acceleration": data[:3].tolist(),
                "angular_velocity": data[3:6].tolist(),
                "quaternion": data[6:].tolist(),
            },
            metadata={
                "units": {
                    "acceleration": "m/s^2",
                    "angular_velocity": "rad/s",
                }
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "port": self.config.port,
            "baudrate": self.config.baudrate,
        }

    def get_status(self) -> dict[str, object]:
        return {
            "running": self.sensor.running,
            "frame_count": self.sensor.frame_count,
            "latest_timestamp": self.sensor.latest_timestamp,
        }
