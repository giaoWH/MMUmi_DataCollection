from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from sdk.core.clock import SystemClock
from sdk.core.frame import SensorFrame

from .base import SensorAdapter


@dataclass(frozen=True)
class FTSensorConfig:
    name: str = "ft"
    port: str = "COM3"
    baudrate: int = 115200
    calibration_duration: float = 3.0
    enable_torque: bool = True


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
    enable_motor_1: bool = True
    enable_motor_2: bool = True


@dataclass(frozen=True)
class MicrophoneSensorConfig:
    name: str = "microphone"
    channels: int = 1
    rate: int = 48000
    chunk: int = 1024
    device_index: int | None = None


@dataclass(frozen=True)
class CameraSensorConfig:
    name: str = "camera"
    device_index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30
    flip_horizontal: bool = False
    flip_vertical: bool = False
    frame_queue_size: int = 128


@dataclass(frozen=True)
class GelSightSensorConfig:
    name: str = "gelsight"
    device_index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30
    frame_queue_size: int = 128


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
        return self._build_sensor_frame(data, frame_id=frame_id, time_info=time_info)

    def read_available_frames(self) -> list[SensorFrame]:
        frames: list[SensorFrame] = []
        for data, _timestamp, frame_id, time_info in self.sensor.get_all_data_with_time_info():
            if data is None:
                continue
            frames.append(self._build_sensor_frame(data, frame_id=frame_id, time_info=time_info))
        return frames

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "port": self.config.port,
            "baudrate": self.config.baudrate,
            "calibration_duration": self.config.calibration_duration,
            "enable_torque": self.config.enable_torque,
        }

    def get_status(self) -> dict[str, object]:
        runtime_status = self.sensor.get_runtime_status()
        return {
            "running": runtime_status["running"],
            "frame_count": runtime_status["frame_count"],
            "produced_frame_count": runtime_status["produced_frame_count"],
            "delivered_frame_count": runtime_status["delivered_frame_count"],
            "dropped_frame_count": runtime_status["dropped_frame_count"],
            "latest_timestamp": runtime_status["latest_timestamp"],
            "queue_depth": runtime_status["queue_depth"],
            "max_queue_depth": runtime_status["max_queue_depth"],
            "queue_capacity": runtime_status["queue_capacity"],
            "calibration_finished": self.sensor.calibration_finished,
        }

    def is_ready(self) -> bool:
        return self.sensor.is_calibrated()

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        return self.sensor.wait_until_calibrated(timeout=timeout)

    def _build_sensor_frame(self, data, *, frame_id: int, time_info: dict[str, object]) -> SensorFrame:
        data = np.asarray(data, dtype=np.float64).reshape(-1)
        frame_time = _build_frame_time(clock=self.clock, time_info=time_info)
        payload = {
            "force": data[:3].tolist(),
        }
        units = {"force": "N"}
        if self.config.enable_torque:
            payload["torque"] = data[3:].tolist()
            units["torque"] = "Nm"
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=frame_id,
            time=frame_time,
            payload=payload,
            metadata={"units": units},
        )


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
        return self._build_sensor_frame(data, frame_id=frame_id, time_info=time_info)

    def read_available_frames(self) -> list[SensorFrame]:
        frames: list[SensorFrame] = []
        for data, _timestamp, frame_id, time_info in self.sensor.get_all_data_with_time_info():
            if data is None:
                continue
            frames.append(self._build_sensor_frame(data, frame_id=frame_id, time_info=time_info))
        return frames

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
            "produced_frame_count": runtime_status["produced_frame_count"],
            "delivered_frame_count": runtime_status["delivered_frame_count"],
            "dropped_frame_count": runtime_status["dropped_frame_count"],
            "latest_timestamp": runtime_status["latest_timestamp"],
            "queue_depth": runtime_status["queue_depth"],
            "max_queue_depth": runtime_status["max_queue_depth"],
            "queue_capacity": runtime_status["queue_capacity"],
        }

    def _build_sensor_frame(self, data, *, frame_id: int, time_info: dict[str, object]) -> SensorFrame:
        data = np.asarray(data, dtype=np.float64).reshape(-1)
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
        return self._build_sensor_frame(data, frame_id=frame_id, time_info=time_info)

    def read_available_frames(self) -> list[SensorFrame]:
        frames: list[SensorFrame] = []
        for data, _timestamp, frame_id, time_info in self.sensor.get_all_data_with_time_info():
            if data is None:
                continue
            frames.append(self._build_sensor_frame(data, frame_id=frame_id, time_info=time_info))
        return frames

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "port": self.config.port,
            "baudrate": self.config.baudrate,
            "calibration_duration": self.config.calibration_duration,
            "enable_motor_1": self.config.enable_motor_1,
            "enable_motor_2": self.config.enable_motor_2,
        }

    def get_status(self) -> dict[str, object]:
        runtime_status = self.sensor.get_runtime_status()
        return {
            "running": runtime_status["running"],
            "frame_count": runtime_status["frame_count"],
            "produced_frame_count": runtime_status["produced_frame_count"],
            "delivered_frame_count": runtime_status["delivered_frame_count"],
            "dropped_frame_count": runtime_status["dropped_frame_count"],
            "latest_timestamp": runtime_status["latest_timestamp"],
            "queue_depth": runtime_status["queue_depth"],
            "max_queue_depth": runtime_status["max_queue_depth"],
            "queue_capacity": runtime_status["queue_capacity"],
            "calibration_finished": self.sensor.calibration_finished,
        }

    def is_ready(self) -> bool:
        return self.sensor.is_calibrated()

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        return self.sensor.wait_until_calibrated(timeout=timeout)

    def _build_sensor_frame(self, data, *, frame_id: int, time_info: dict[str, object]) -> SensorFrame:
        data = np.asarray(data, dtype=np.float64).reshape(-1)
        frame_time = _build_frame_time(clock=self.clock, time_info=time_info)
        payload: dict[str, dict[str, float]] = {}
        channels: list[str] = []
        if self.config.enable_motor_1:
            payload["motor_1"] = {
                "position": float(data[0]),
                "velocity": float(data[1]),
                "torque": float(data[2]),
            }
            channels.extend(["M1_P", "M1_V", "M1_T"])
        if self.config.enable_motor_2:
            payload["motor_2"] = {
                "position": float(data[3]),
                "velocity": float(data[4]),
                "torque": float(data[5]),
            }
            channels.extend(["M2_P", "M2_V", "M2_T"])
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=frame_id,
            time=frame_time,
            payload=payload,
            metadata={"channels": channels},
        )


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
        data, _timestamp, frame_id, time_info = self.sensor.get_next_data_with_time_info()
        if data is None:
            return None

        return self._build_sensor_frame(data, frame_id=frame_id, time_info=time_info)

    def read_available_frames(self) -> list[SensorFrame]:
        frames: list[SensorFrame] = []
        for data, _timestamp, frame_id, time_info in self.sensor.get_all_data_with_time_info():
            if data is None:
                continue
            frames.append(self._build_sensor_frame(data, frame_id=frame_id, time_info=time_info))
        return frames

    def _build_sensor_frame(self, data, *, frame_id: int, time_info: dict[str, object]) -> SensorFrame:
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
                "sample_rate": self.sensor.effective_rate,
                "chunk": self.config.chunk,
                "dtype": "int16",
                "device_index": self.sensor.resolved_device_index,
                "device_name": self.sensor.device_name,
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "channels": self.config.channels,
            "sample_rate": self.sensor.effective_rate,
            "chunk": self.config.chunk,
            "device_index": self.sensor.resolved_device_index,
            "device_name": self.sensor.device_name,
            "requested_sample_rate": self.config.rate,
        }

    def get_status(self) -> dict[str, object]:
        return {
            "running": self.sensor.running,
            "frame_count": self.sensor.frame_count,
            "latest_timestamp": self.sensor.latest_timestamp,
            "ready": self.sensor.is_calibrated(),
            "open_error": self.sensor.open_error,
        }

    def is_ready(self) -> bool:
        return self.sensor.is_calibrated()

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        return self.sensor.wait_until_calibrated(timeout=timeout)


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
            flip_horizontal=config.flip_horizontal,
            flip_vertical=config.flip_vertical,
            frame_queue_size=config.frame_queue_size,
        )

    def start(self) -> None:
        self.sensor.start()

    def stop(self) -> None:
        self.sensor.stop()

    def read_frame(self) -> SensorFrame | None:
        data, _timestamp, frame_id, time_info = self.sensor.get_next_data_with_time_info()
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
                "flip_horizontal": self.config.flip_horizontal,
                "flip_vertical": self.config.flip_vertical,
                "frame_queue_size": self.config.frame_queue_size,
            },
        )

    def read_available_frames(self) -> list[SensorFrame]:
        frames: list[SensorFrame] = []
        for data, _timestamp, frame_id, time_info in self.sensor.get_all_data_with_time_info():
            if data is None:
                continue
            frame_time = _build_frame_time(clock=self.clock, time_info=time_info)
            frames.append(
                SensorFrame(
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
                        "flip_horizontal": self.config.flip_horizontal,
                        "flip_vertical": self.config.flip_vertical,
                        "frame_queue_size": self.config.frame_queue_size,
                    },
                )
            )
        return frames

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "width": self.config.width,
            "height": self.config.height,
            "fps": self.config.fps,
            "device_index": self.config.device_index,
            "flip_horizontal": self.config.flip_horizontal,
            "flip_vertical": self.config.flip_vertical,
            "frame_queue_size": self.config.frame_queue_size,
        }

    def get_status(self) -> dict[str, object]:
        return self.sensor.get_runtime_status()


class LegacyGelSightAdapter(SensorAdapter):
    def __init__(self, config: GelSightSensorConfig, clock: SystemClock | None = None) -> None:
        super().__init__(config.name, "gelsight_sensor", "visuotactile")
        from sensors.gelsight_sensor import GelSightSensor

        self.config = config
        self.clock = clock or SystemClock()
        self.sensor = GelSightSensor(
            name=config.name,
            device_index=config.device_index,
            width=config.width,
            height=config.height,
            fps=config.fps,
            frame_queue_size=config.frame_queue_size,
        )

    def start(self) -> None:
        self.sensor.start()

    def stop(self) -> None:
        self.sensor.stop()

    def read_frame(self) -> SensorFrame | None:
        data, _timestamp, frame_id, time_info = self.sensor.get_next_data_with_time_info()
        if data is None:
            return None

        frame_time = _build_frame_time(clock=self.clock, time_info=time_info)
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=frame_id,
            time=frame_time,
            payload={"image": data},
            metadata={
                "width": self.config.width,
                "height": self.config.height,
                "fps": self.config.fps,
                "device_index": self.config.device_index,
                "sensor_family": "gelsight_mini",
                "frame_queue_size": self.config.frame_queue_size,
            },
        )

    def read_available_frames(self) -> list[SensorFrame]:
        frames: list[SensorFrame] = []
        for data, _timestamp, frame_id, time_info in self.sensor.get_all_data_with_time_info():
            if data is None:
                continue
            frame_time = _build_frame_time(clock=self.clock, time_info=time_info)
            frames.append(
                SensorFrame(
                    sensor_name=self.name,
                    sensor_type=self.sensor_type,
                    modality=self.modality,
                    frame_id=frame_id,
                    time=frame_time,
                    payload={"image": data},
                    metadata={
                        "width": self.config.width,
                        "height": self.config.height,
                        "fps": self.config.fps,
                        "device_index": self.config.device_index,
                        "sensor_family": "gelsight_mini",
                        "frame_queue_size": self.config.frame_queue_size,
                    },
                )
            )
        return frames

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "width": self.config.width,
            "height": self.config.height,
            "fps": self.config.fps,
            "device_index": self.config.device_index,
            "sensor_family": "gelsight_mini",
            "frame_queue_size": self.config.frame_queue_size,
        }

    def get_status(self) -> dict[str, object]:
        return self.sensor.get_runtime_status()
