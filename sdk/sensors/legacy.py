from __future__ import annotations

from dataclasses import dataclass

from sdk.core.clock import SystemClock
from sdk.core.frame import SensorFrame

from .base import SensorAdapter


@dataclass(frozen=True)
class FTSensorConfig:
    name: str = "ft"
    port: str = "COM3"
    baudrate: int = 115200
    calibration_duration: float = 3.0


@dataclass(frozen=True)
class IMUSensorConfig:
    name: str = "imu"
    port: str = "COM4"
    baudrate: int = 115200


@dataclass(frozen=True)
class MotorsSensorConfig:
    name: str = "motors"
    port: str = "/dev/ttyUSB0"
    baudrate: int = 115200
    calibration_duration: float = 3.0


@dataclass(frozen=True)
class MicrophoneSensorConfig:
    name: str = "microphone"
    channels: int = 1
    rate: int = 44100
    chunk: int = 1024
    device_index: int | None = None


@dataclass(frozen=True)
class CameraSensorConfig:
    name: str = "camera"
    device_index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30


def _build_frame_time(
    *,
    clock: SystemClock,
    time_info: dict[str, object] | None,
    device_time: float | None = None,
) -> object:
    time_info = time_info or {}
    host_capture_time_ns = time_info.get("host_capture_time_ns")
    monotonic_capture_time_ns = time_info.get("monotonic_capture_time_ns")
    host_arrival_time_ns = time_info.get("host_arrival_time_ns")
    host_read_start_time_ns = time_info.get("host_read_start_time_ns")
    host_read_end_time_ns = time_info.get("host_read_end_time_ns")

    return clock.capture_at(
        host_time_ns=int(host_capture_time_ns) if host_capture_time_ns is not None else None,
        monotonic_time_ns=(
            int(monotonic_capture_time_ns)
            if monotonic_capture_time_ns is not None
            else None
        ),
        device_time=device_time,
        host_arrival_time_ns=(
            int(host_arrival_time_ns)
            if host_arrival_time_ns is not None
            else None
        ),
        host_read_start_time_ns=(
            int(host_read_start_time_ns)
            if host_read_start_time_ns is not None
            else None
        ),
        host_read_end_time_ns=(
            int(host_read_end_time_ns)
            if host_read_end_time_ns is not None
            else None
        ),
    )


class LegacyFTAdapter(SensorAdapter):
    def __init__(self, config: FTSensorConfig, clock: SystemClock | None = None) -> None:
        super().__init__(config.name, "ft_sensor", "force_torque")
        from sensors import FTSensor

        self.config = config
        self.clock = clock or SystemClock()
        self.sensor = FTSensor(
            port=config.port,
            baudrate=config.baudrate,
            calibration_duration=config.calibration_duration,
        )

    def start(self) -> None:
        self.sensor.start()

    def stop(self) -> None:
        self.sensor.stop()

    def read_frame(self) -> SensorFrame | None:
        data, _timestamp, frame_id, time_info = self.sensor.get_data_with_time_info()
        if data is None:
            return None

        frame_time = _build_frame_time(clock=self.clock, time_info=time_info)
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
            "calibration_duration": self.config.calibration_duration,
        }

    def get_status(self) -> dict[str, object]:
        runtime_status = self.sensor.get_runtime_status()
        return {
            "running": runtime_status["running"],
            "frame_count": runtime_status["frame_count"],
            "latest_timestamp": runtime_status["latest_timestamp"],
            "calibration_finished": self.sensor.calibration_finished,
        }

    def is_ready(self) -> bool:
        return self.sensor.is_calibrated()

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        return self.sensor.wait_until_calibrated(timeout=timeout)


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
        data, _timestamp, frame_id, time_info = self.sensor.get_data_with_time_info()
        if data is None:
            return None

        frame_time = _build_frame_time(clock=self.clock, time_info=time_info)
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
        runtime_status = self.sensor.get_runtime_status()
        return {
            "running": runtime_status["running"],
            "frame_count": runtime_status["frame_count"],
            "latest_timestamp": runtime_status["latest_timestamp"],
        }


class LegacyMotorsAdapter(SensorAdapter):
    def __init__(self, config: MotorsSensorConfig, clock: SystemClock | None = None) -> None:
        super().__init__(config.name, "motors_sensor", "motor_state")
        from sensors import MotorsSensor

        self.config = config
        self.clock = clock or SystemClock()
        self.sensor = MotorsSensor(
            port=config.port,
            baudrate=config.baudrate,
            calibration_duration=config.calibration_duration,
        )

    def start(self) -> None:
        self.sensor.start()

    def stop(self) -> None:
        self.sensor.stop()

    def read_frame(self) -> SensorFrame | None:
        data, _timestamp, frame_id, time_info = self.sensor.get_data_with_time_info()
        if data is None:
            return None

        frame_time = _build_frame_time(clock=self.clock, time_info=time_info)
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=frame_id,
            time=frame_time,
            payload={
                "motor_state": data.tolist(),
                "motor_1": {
                    "position": float(data[0]),
                    "velocity": float(data[1]),
                    "torque": float(data[2]),
                },
                "motor_2": {
                    "position": float(data[3]),
                    "velocity": float(data[4]),
                    "torque": float(data[5]),
                },
            },
            metadata={
                "channels": [
                    "M1_P",
                    "M1_V",
                    "M1_T",
                    "M2_P",
                    "M2_V",
                    "M2_T",
                ]
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "port": self.config.port,
            "baudrate": self.config.baudrate,
            "calibration_duration": self.config.calibration_duration,
        }

    def get_status(self) -> dict[str, object]:
        runtime_status = self.sensor.get_runtime_status()
        return {
            "running": runtime_status["running"],
            "frame_count": runtime_status["frame_count"],
            "latest_timestamp": runtime_status["latest_timestamp"],
            "calibration_finished": self.sensor.calibration_finished,
        }

    def is_ready(self) -> bool:
        return self.sensor.is_calibrated()

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        return self.sensor.wait_until_calibrated(timeout=timeout)


class LegacyMicrophoneAdapter(SensorAdapter):
    def __init__(self, config: MicrophoneSensorConfig, clock: SystemClock | None = None) -> None:
        super().__init__(config.name, "microphone_sensor", "audio")
        from sensors import MicrophoneSensor

        self.config = config
        self.clock = clock or SystemClock()
        self.sensor = MicrophoneSensor(
            name=config.name,
            channels=config.channels,
            rate=config.rate,
            chunk=config.chunk,
            device_index=config.device_index,
        )

    def start(self) -> None:
        self.sensor.start()

    def stop(self) -> None:
        self.sensor.stop()

    def read_frame(self) -> SensorFrame | None:
        data, _timestamp, frame_id, time_info = self.sensor.get_data_with_time_info()
        if data is None:
            return None

        frame_time = _build_frame_time(clock=self.clock, time_info=time_info)
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=frame_id,
            time=frame_time,
            payload={"audio": data},
            metadata={
                "channels": self.config.channels,
                "sample_rate": self.config.rate,
                "chunk": self.config.chunk,
                "dtype": "int16",
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "channels": self.config.channels,
            "sample_rate": self.config.rate,
            "chunk": self.config.chunk,
            "device_index": self.config.device_index,
        }

    def get_status(self) -> dict[str, object]:
        return {
            "running": self.sensor.running,
            "frame_count": self.sensor.frame_count,
            "latest_timestamp": self.sensor.latest_timestamp,
        }


class LegacyCameraAdapter(SensorAdapter):
    def __init__(self, config: CameraSensorConfig, clock: SystemClock | None = None) -> None:
        super().__init__(config.name, "camera_sensor", "rgb")
        from sensors.camera_sensor import CameraSensor

        self.config = config
        self.clock = clock or SystemClock()
        self.sensor = CameraSensor(
            name=config.name,
            device_index=config.device_index,
            width=config.width,
            height=config.height,
            fps=config.fps,
        )

    def start(self) -> None:
        self.sensor.start()

    def stop(self) -> None:
        self.sensor.stop()

    def read_frame(self) -> SensorFrame | None:
        data, _timestamp, frame_id, time_info = self.sensor.get_data_with_time_info()
        if data is None:
            return None

        frame_time = _build_frame_time(clock=self.clock, time_info=time_info)
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=frame_id,
            time=frame_time,
            payload={"color": data},
            metadata={
                "width": self.config.width,
                "height": self.config.height,
                "fps": self.config.fps,
                "device_index": self.config.device_index,
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "width": self.config.width,
            "height": self.config.height,
            "fps": self.config.fps,
            "device_index": self.config.device_index,
        }

    def get_status(self) -> dict[str, object]:
        return {
            "running": self.sensor.running,
            "frame_count": self.sensor.frame_count,
            "latest_timestamp": self.sensor.latest_timestamp,
        }
