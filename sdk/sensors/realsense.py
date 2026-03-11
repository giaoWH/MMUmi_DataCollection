from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

from sdk.core.clock import SystemClock
from sdk.core.frame import SensorFrame

from .base import SensorAdapter

try:
    import pyrealsense2 as rs
except ImportError:  # pragma: no cover
    rs = None


@dataclass(frozen=True)
class RealSenseConfig:
    name: str = "realsense"
    width: int = 640
    height: int = 480
    fps: int = 30
    enable_depth: bool = True
    enable_color: bool = True
    serial_number: str | None = None
    align_to_color: bool = True


class RealSenseRGBDAdapter(SensorAdapter):
    def __init__(
        self,
        config: RealSenseConfig,
        clock: SystemClock | None = None,
    ) -> None:
        super().__init__(config.name, "realsense", "rgbd")
        self.config = config
        self.clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._pipeline = None
        self._align = None
        self._frame_id = 0
        self._latest_payload: dict[str, object] | None = None
        self._latest_device_time: float | None = None
        self._latest_host_time = 0.0
        self._intrinsics: dict[str, object] = {}
        self._depth_scale: float | None = None

    def start(self) -> None:
        if rs is None:
            raise RuntimeError("未安装 pyrealsense2，无法启动 RealSense")
        if self._running:
            return

        pipeline = rs.pipeline()
        rs_config = rs.config()
        if self.config.serial_number:
            rs_config.enable_device(self.config.serial_number)
        if self.config.enable_color:
            rs_config.enable_stream(
                rs.stream.color,
                self.config.width,
                self.config.height,
                rs.format.bgr8,
                self.config.fps,
            )
        if self.config.enable_depth:
            rs_config.enable_stream(
                rs.stream.depth,
                self.config.width,
                self.config.height,
                rs.format.z16,
                self.config.fps,
            )

        profile = pipeline.start(rs_config)
        self._pipeline = pipeline
        self._align = rs.align(rs.stream.color) if self.config.align_to_color else None
        self._depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()
        self._intrinsics = self._extract_intrinsics(profile)
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None

    def read_frame(self) -> SensorFrame | None:
        with self._lock:
            if self._latest_payload is None:
                return None
            payload: dict[str, object] = {}
            for key, value in self._latest_payload.items():
                if isinstance(value, np.ndarray):
                    payload[key] = value.copy()
                elif isinstance(value, dict):
                    payload[key] = dict(value)
                else:
                    payload[key] = value
            frame_id = self._frame_id
            device_time = self._latest_device_time
            host_time = self._latest_host_time

        frame_time = self.clock.capture_at(
            host_time=host_time,
            device_time=device_time,
        )
        return SensorFrame(
            sensor_name=self.name,
            sensor_type=self.sensor_type,
            modality=self.modality,
            frame_id=frame_id,
            time=frame_time,
            payload=payload,
            metadata={
                "depth_scale": self._depth_scale,
                "intrinsics": dict(self._intrinsics),
                "stream_config": self._build_stream_config(),
            },
        )

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "name": self.config.name,
            "width": self.config.width,
            "height": self.config.height,
            "fps": self.config.fps,
            "enable_color": self.config.enable_color,
            "enable_depth": self.config.enable_depth,
            "align_to_color": self.config.align_to_color,
            "serial_number": self.config.serial_number,
            "depth_scale": self._depth_scale,
            "intrinsics": dict(self._intrinsics),
            "stream_config": self._build_stream_config(),
        }

    def get_status(self) -> dict[str, object]:
        return {
            "running": self._running,
            "frame_count": self._frame_id,
            "latest_timestamp": self._latest_host_time,
        }

    def _worker(self) -> None:  # pragma: no cover
        assert rs is not None
        assert self._pipeline is not None

        while self._running:
            frames = self._pipeline.wait_for_frames(timeout_ms=1000)
            if self._align is not None:
                frames = self._align.process(frames)

            color_frame = frames.get_color_frame() if self.config.enable_color else None
            depth_frame = frames.get_depth_frame() if self.config.enable_depth else None
            if self.config.enable_color and not color_frame:
                continue
            if self.config.enable_depth and not depth_frame:
                continue

            payload: dict[str, object] = {}
            device_time = None
            if color_frame:
                payload["color"] = np.asanyarray(color_frame.get_data())
                device_time = color_frame.get_timestamp() / 1000.0
            if depth_frame:
                payload["depth"] = np.asanyarray(depth_frame.get_data())
                if device_time is None:
                    device_time = depth_frame.get_timestamp() / 1000.0

            with self._lock:
                self._frame_id += 1
                self._latest_payload = payload
                self._latest_device_time = device_time
                self._latest_host_time = self.clock.capture(device_time=device_time).host_time

    def _extract_intrinsics(self, profile: object) -> dict[str, object]:
        intrinsics: dict[str, object] = {}
        if rs is None:
            return intrinsics

        streams: list[tuple[str, object]] = []
        if self.config.enable_color:
            streams.append(("color", profile.get_stream(rs.stream.color)))
        if self.config.enable_depth:
            streams.append(("depth", profile.get_stream(rs.stream.depth)))

        for name, stream in streams:
            video_profile = stream.as_video_stream_profile()
            stream_intrinsics = video_profile.get_intrinsics()
            intrinsics[name] = {
                "width": stream_intrinsics.width,
                "height": stream_intrinsics.height,
                "fx": stream_intrinsics.fx,
                "fy": stream_intrinsics.fy,
                "ppx": stream_intrinsics.ppx,
                "ppy": stream_intrinsics.ppy,
                "coeffs": list(stream_intrinsics.coeffs),
            }
        return intrinsics

    def _build_stream_config(self) -> dict[str, object]:
        return {
            "width": self.config.width,
            "height": self.config.height,
            "fps": self.config.fps,
            "enable_color": self.config.enable_color,
            "enable_depth": self.config.enable_depth,
            "align_to_color": self.config.align_to_color,
        }
