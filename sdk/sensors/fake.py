from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

from sdk.core.clock import SystemClock
from sdk.core.frame import SensorFrame

from .base import SensorAdapter


@dataclass(frozen=True)
class FakeFTSensorConfig:
    name: str = "ft"
    sample_rate_hz: float = 100.0


@dataclass(frozen=True)
class FakeIMUSensorConfig:
    name: str = "imu"
    sample_rate_hz: float = 200.0


@dataclass(frozen=True)
class FakeRealSenseConfig:
    name: str = "realsense"
    width: int = 640
    height: int = 480
    fps: int = 30


@dataclass(frozen=True)
class FakeMotorsConfig:
    name: str = "motors"
    sample_rate_hz: float = 100.0


@dataclass(frozen=True)
class FakeMicrophoneConfig:
    name: str = "microphone"
    channels: int = 1
    rate: int = 48000
    chunk: int = 1024


@dataclass(frozen=True)
class FakeCameraConfig:
    name: str = "camera"
    width: int = 640
    height: int = 480
    fps: int = 30


@dataclass(frozen=True)
class FakeGelSightConfig:
    name: str = "gelsight"
    width: int = 640
    height: int = 480
    fps: int = 30


class _BaseFakeAdapter(SensorAdapter):
    def __init__(
        self,
        name: str,
        sensor_type: str,
        modality: str,
        *,
        sample_rate_hz: float,
        clock: SystemClock | None = None,
    ) -> None:
        super().__init__(name, sensor_type, modality)
        self.clock = clock or SystemClock()
        self.sample_rate_hz = sample_rate_hz
        self._running = False
        self._frame_id = 0
        self._started_at_monotonic_ns = 0
        self._last_emitted_at_monotonic_ns = 0

    def start(self) -> None:
        self._running = True
        self._started_at_monotonic_ns = time.perf_counter_ns()
        self._last_emitted_at_monotonic_ns = 0
        self._frame_id = 0

    def stop(self) -> None:
        self._running = False

    def get_status(self) -> dict[str, object]:
        return {
            "running": self._running,
            "frame_count": self._frame_id,
            "latest_timestamp": self._last_emitted_at_monotonic_ns / 1_000_000_000.0,
        }

    def read_frame(self) -> SensorFrame | None:
        if not self._running:
            return None

        now_wall_ns = time.time_ns()
        now_mono_ns = time.perf_counter_ns()
        min_interval = 1.0 / self.sample_rate_hz if self.sample_rate_hz > 0 else 0.0
        if (
            self._last_emitted_at_monotonic_ns > 0
            and (now_mono_ns - self._last_emitted_at_monotonic_ns) < int(min_interval * 1_000_000_000)
        ):
            return None

        self._frame_id += 1
        self._last_emitted_at_monotonic_ns = now_mono_ns
        phase = (now_mono_ns - self._started_at_monotonic_ns) / 1_000_000_000.0
        frame_time = self.clock.capture_at(
            host_time_ns=now_wall_ns,
            monotonic_time_ns=now_mono_ns,
            device_time=phase,
            device_time_ns=int(round(phase * 1_000_000_000)),
            host_arrival_time_ns=now_wall_ns,
            host_read_start_time_ns=now_wall_ns,
            host_read_end_time_ns=now_wall_ns,
        )
        return self._build_frame(frame_time=frame_time, phase=phase)

    def _build_frame(self, *, frame_time: object, phase: float) -> SensorFrame:
        raise NotImplementedError


class FakeFTAdapter(_BaseFakeAdapter):
    def __init__(self, config: FakeFTSensorConfig, clock: SystemClock | None = None) -> None:
        super().__init__(
            config.name,
            "fake_ft_sensor",
            "force_torque",
            sample_rate_hz=config.sample_rate_hz,
            clock=clock,
        )
        self.config = config

    def _build_frame(self, *, frame_time: object, phase: float) -> SensorFrame:
        force = [
            8.0 * math.sin(phase * 1.2),
            5.0 * math.cos(phase * 0.7),
            12.0 + 0.8 * math.sin(phase * 0.3),
        ]
        torque = [
            0.4 * math.sin(phase * 0.9),
            0.2 * math.cos(phase * 1.4),
            0.3 * math.sin(phase * 1.7),
        ]
        wrench = force + torque
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=self._frame_id,
            time=frame_time,
            payload={
                "force_torque": wrench,
                "force": force,
                "torque": torque,
            },
            metadata={
                "units": {"force": "N", "torque": "Nm"},
                "source": "fake",
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "sample_rate_hz": self.sample_rate_hz,
            "source": "fake",
        }


class FakeIMUAdapter(_BaseFakeAdapter):
    def __init__(self, config: FakeIMUSensorConfig, clock: SystemClock | None = None) -> None:
        super().__init__(
            config.name,
            "fake_imu_sensor",
            "imu",
            sample_rate_hz=config.sample_rate_hz,
            clock=clock,
        )
        self.config = config

    def _build_frame(self, *, frame_time: object, phase: float) -> SensorFrame:
        acceleration = [
            0.5 * math.sin(phase * 1.4),
            0.3 * math.cos(phase * 0.9),
            9.81 + 0.1 * math.sin(phase * 0.5),
        ]
        angular_velocity = [
            0.02 * math.sin(phase * 1.3),
            0.03 * math.cos(phase * 1.1),
            0.04 * math.sin(phase * 0.8),
        ]
        yaw = 0.2 * math.sin(phase * 0.4)
        half_yaw = yaw / 2.0
        quaternion = [math.cos(half_yaw), 0.0, 0.0, math.sin(half_yaw)]
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=self._frame_id,
            time=frame_time,
            payload={
                "acceleration": acceleration,
                "angular_velocity": angular_velocity,
                "quaternion": quaternion,
            },
            metadata={
                "units": {
                    "acceleration": "m/s^2",
                    "angular_velocity": "rad/s",
                },
                "source": "fake",
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "sample_rate_hz": self.sample_rate_hz,
            "source": "fake",
        }


class FakeRealSenseAdapter(_BaseFakeAdapter):
    def __init__(self, config: FakeRealSenseConfig, clock: SystemClock | None = None) -> None:
        super().__init__(
            config.name,
            "fake_realsense",
            "rgbd",
            sample_rate_hz=float(config.fps),
            clock=clock,
        )
        self.config = config
        self._grid_x, self._grid_y = np.meshgrid(
            np.arange(self.config.width, dtype=np.uint16),
            np.arange(self.config.height, dtype=np.uint16),
        )

    def _build_frame(self, *, frame_time: object, phase: float) -> SensorFrame:
        shift = int((phase * 30.0) % 255)
        color = np.zeros((self.config.height, self.config.width, 3), dtype=np.uint8)
        color[..., 0] = ((self._grid_x + shift) % 255).astype(np.uint8)
        color[..., 1] = ((self._grid_y + 2 * shift) % 255).astype(np.uint8)
        color[..., 2] = np.uint8((32 + shift) % 255)

        depth = (1000 + (self._grid_x // 8) + (self._grid_y // 10) + int(phase * 20)).astype(np.uint16)
        intrinsics = {
            "color": {
                "width": self.config.width,
                "height": self.config.height,
                "fx": 615.0,
                "fy": 615.0,
                "ppx": self.config.width / 2.0,
                "ppy": self.config.height / 2.0,
                "coeffs": [0.0, 0.0, 0.0, 0.0, 0.0],
            },
            "depth": {
                "width": self.config.width,
                "height": self.config.height,
                "fx": 615.0,
                "fy": 615.0,
                "ppx": self.config.width / 2.0,
                "ppy": self.config.height / 2.0,
                "coeffs": [0.0, 0.0, 0.0, 0.0, 0.0],
            },
        }
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=self._frame_id,
            time=frame_time,
            payload={
                "color": color,
                "depth": depth,
            },
            metadata={
                "depth_scale": 0.001,
                "intrinsics": intrinsics,
                "source": "fake",
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "width": self.config.width,
            "height": self.config.height,
            "fps": self.config.fps,
            "depth_scale": 0.001,
            "source": "fake",
        }


class FakeMotorsAdapter(_BaseFakeAdapter):
    def __init__(self, config: FakeMotorsConfig, clock: SystemClock | None = None) -> None:
        super().__init__(
            config.name,
            "fake_motors_sensor",
            "motor_state",
            sample_rate_hz=config.sample_rate_hz,
            clock=clock,
        )
        self.config = config

    def _build_frame(self, *, frame_time: object, phase: float) -> SensorFrame:
        motor_1 = [
            0.5 * math.sin(phase * 0.8),
            0.3 * math.cos(phase * 1.3),
            0.2 * math.sin(phase * 1.7),
        ]
        motor_2 = [
            0.6 * math.cos(phase * 0.6),
            0.25 * math.sin(phase * 1.1),
            0.15 * math.cos(phase * 1.5),
        ]
        state = motor_1 + motor_2
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=self._frame_id,
            time=frame_time,
            payload={
                "motor_state": state,
                "motor_1": {
                    "position": motor_1[0],
                    "velocity": motor_1[1],
                    "torque": motor_1[2],
                },
                "motor_2": {
                    "position": motor_2[0],
                    "velocity": motor_2[1],
                    "torque": motor_2[2],
                },
            },
            metadata={
                "channels": ["M1_P", "M1_V", "M1_T", "M2_P", "M2_V", "M2_T"],
                "source": "fake",
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "sample_rate_hz": self.sample_rate_hz,
            "source": "fake",
        }


class FakeMicrophoneAdapter(_BaseFakeAdapter):
    def __init__(self, config: FakeMicrophoneConfig, clock: SystemClock | None = None) -> None:
        super().__init__(
            config.name,
            "fake_microphone_sensor",
            "audio",
            sample_rate_hz=float(config.rate) / float(config.chunk),
            clock=clock,
        )
        self.config = config
        self._sample_index = 0

    def start(self) -> None:
        super().start()
        self._sample_index = 0

    def _build_frame(self, *, frame_time: object, phase: float) -> SensorFrame:
        sample_idx = np.arange(self.config.chunk, dtype=np.float64) + self._sample_index
        wave = 0.4 * np.sin(2.0 * np.pi * 440.0 * sample_idx / float(self.config.rate))
        if self.config.channels > 1:
            channels = []
            for channel in range(self.config.channels):
                channels.append(
                    0.4 * np.sin(
                        2.0 * np.pi * (440.0 + 20.0 * channel) * sample_idx / float(self.config.rate)
                    )
                )
            audio = np.stack(channels, axis=1)
        else:
            audio = wave

        self._sample_index += self.config.chunk
        audio_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=self._frame_id,
            time=frame_time,
            payload={"audio": audio_int16},
            metadata={
                "channels": self.config.channels,
                "sample_rate": self.config.rate,
                "chunk": self.config.chunk,
                "dtype": "int16",
                "source": "fake",
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "channels": self.config.channels,
            "sample_rate": self.config.rate,
            "chunk": self.config.chunk,
            "source": "fake",
        }


class FakeCameraAdapter(_BaseFakeAdapter):
    def __init__(self, config: FakeCameraConfig, clock: SystemClock | None = None) -> None:
        super().__init__(
            config.name,
            "fake_camera_sensor",
            "rgb",
            sample_rate_hz=float(config.fps),
            clock=clock,
        )
        self.config = config
        self._grid_x, self._grid_y = np.meshgrid(
            np.arange(self.config.width, dtype=np.uint16),
            np.arange(self.config.height, dtype=np.uint16),
        )

    def _build_frame(self, *, frame_time: object, phase: float) -> SensorFrame:
        shift = int((phase * 20.0) % 255)
        color = np.zeros((self.config.height, self.config.width, 3), dtype=np.uint8)
        color[..., 0] = ((self._grid_x + shift) % 255).astype(np.uint8)
        color[..., 1] = ((self._grid_y + shift * 3) % 255).astype(np.uint8)
        color[..., 2] = np.uint8((80 + shift) % 255)
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=self._frame_id,
            time=frame_time,
            payload={"color": color},
            metadata={
                "width": self.config.width,
                "height": self.config.height,
                "fps": self.config.fps,
                "source": "fake",
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "width": self.config.width,
            "height": self.config.height,
            "fps": self.config.fps,
            "source": "fake",
        }


class FakeGelSightAdapter(_BaseFakeAdapter):
    def __init__(self, config: FakeGelSightConfig, clock: SystemClock | None = None) -> None:
        super().__init__(
            config.name,
            "fake_gelsight_sensor",
            "visuotactile",
            sample_rate_hz=float(config.fps),
            clock=clock,
        )
        self.config = config
        self._grid_x, self._grid_y = np.meshgrid(
            np.arange(self.config.width, dtype=np.uint16),
            np.arange(self.config.height, dtype=np.uint16),
        )

    def _build_frame(self, *, frame_time: object, phase: float) -> SensorFrame:
        shift = int((phase * 25.0) % 255)
        image = np.zeros((self.config.height, self.config.width, 3), dtype=np.uint8)
        image[..., 0] = ((self._grid_x // 2 + shift) % 255).astype(np.uint8)
        image[..., 1] = ((self._grid_y // 2 + shift * 2) % 255).astype(np.uint8)
        image[..., 2] = np.uint8((120 + shift) % 255)
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=self._frame_id,
            time=frame_time,
            payload={"image": image},
            metadata={
                "width": self.config.width,
                "height": self.config.height,
                "fps": self.config.fps,
                "sensor_family": "gelsight_mini",
                "source": "fake",
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "width": self.config.width,
            "height": self.config.height,
            "fps": self.config.fps,
            "sensor_family": "gelsight_mini",
            "source": "fake",
        }
