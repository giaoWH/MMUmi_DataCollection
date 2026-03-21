from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyrealsense2 as rs

from .common.base_sensor import BaseSensor


@dataclass(frozen=True)
class RealsenseImageStreamConfig:
    width: int = 640
    height: int = 480
    fps: int = 30


@dataclass(frozen=True)
class RealsenseIMUConfig:
    accel_fps: int = 250
    gyro_fps: int = 200
    max_samples_per_frame: int = 512


@dataclass(frozen=True)
class RealsensePointCloudConfig:
    colored: bool = True


@dataclass(frozen=True)
class RealsenseConfig:
    name: str = "Realsense"
    serial_number: str | None = None
    enable_color: bool = True
    enable_depth: bool = True
    enable_ir1: bool = True
    enable_ir2: bool = True
    enable_imu: bool = True
    enable_aligned_depth_to_color: bool = True
    enable_pointcloud: bool = True
    color: RealsenseImageStreamConfig = field(default_factory=RealsenseImageStreamConfig)
    depth: RealsenseImageStreamConfig = field(default_factory=RealsenseImageStreamConfig)
    infrared: RealsenseImageStreamConfig = field(default_factory=RealsenseImageStreamConfig)
    imu: RealsenseIMUConfig = field(default_factory=RealsenseIMUConfig)
    pointcloud: RealsensePointCloudConfig = field(default_factory=RealsensePointCloudConfig)


def clone_realsense_payload(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.copy()
    if isinstance(value, dict):
        return {key: clone_realsense_payload(child) for key, child in value.items()}
    if isinstance(value, list):
        return [clone_realsense_payload(item) for item in value]
    return value


def normalize_frame_to_uint8(frame: np.ndarray) -> np.ndarray:
    array = np.asarray(frame)
    if array.dtype == np.uint8:
        return array.copy()
    if array.size == 0:
        return np.zeros(array.shape, dtype=np.uint8)
    min_value = float(np.min(array))
    max_value = float(np.max(array))
    if max_value <= min_value:
        return np.zeros(array.shape, dtype=np.uint8)
    normalized = (array.astype(np.float32) - min_value) * (255.0 / (max_value - min_value))
    return np.clip(normalized, 0.0, 255.0).astype(np.uint8)


def depth_to_preview(depth_frame: np.ndarray) -> np.ndarray:
    return normalize_frame_to_uint8(depth_frame)


def infrared_to_preview(ir_frame: np.ndarray) -> np.ndarray:
    return normalize_frame_to_uint8(ir_frame)


class RealsenseSensor(BaseSensor):
    def __init__(self, config: RealsenseConfig | None = None):
        self.config = config or RealsenseConfig()
        super().__init__(self.config.name)
        self._pipeline = None
        self._profile = None
        self._align = None
        self._pointcloud = None
        self._imu_samples = deque(maxlen=self.config.imu.max_samples_per_frame)
        self._runtime_metadata: dict[str, object] = {}
        self._ready = False

    def start(self):
        self.latest_data = None
        self.latest_timestamp = 0.0
        self.frame_count = 0
        self.latest_time_info = {}
        self._imu_samples.clear()
        self._runtime_metadata = {}
        self._ready = False
        super().start()

    def get_data(self):
        with self.lock:
            data = clone_realsense_payload(self.latest_data)
            return data, self.latest_timestamp, self.frame_count

    def get_data_with_time_info(self):
        with self.lock:
            data = clone_realsense_payload(self.latest_data)
            return data, self.latest_timestamp, self.frame_count, dict(self.latest_time_info)

    def is_calibrated(self):
        return self._ready

    def wait_until_calibrated(self, timeout=None):
        if self._ready:
            return True
        deadline = None if timeout is None else time.time() + timeout
        while self.running and not self._ready:
            if deadline is not None and time.time() >= deadline:
                return False
            time.sleep(0.01)
        return self._ready

    def _worker(self):
        try:
            self._open_pipeline()
            self._ready = True
        except Exception as exc:
            print(f"[{self.name}] RealSense 打开失败: {exc}")
            self.running = False
            self._close_hardware()
            return

        while self.running:
            try:
                read_start_wall_ns = time.time_ns()
                read_start_mono_ns = time.perf_counter_ns()
                frames = self._pipeline.wait_for_frames(timeout_ms=1000)
                read_end_wall_ns = time.time_ns()
                read_end_mono_ns = time.perf_counter_ns()

                payload = self._build_payload(frames)
                if payload is None:
                    continue

                capture_wall_ns = (read_start_wall_ns + read_end_wall_ns) // 2
                capture_mono_ns = (read_start_mono_ns + read_end_mono_ns) // 2
                with self.lock:
                    self.latest_data = payload
                    self.latest_timestamp = capture_wall_ns / 1_000_000_000.0
                    self.frame_count += 1
                    self.latest_time_info = {
                        "host_capture_time_ns": capture_wall_ns,
                        "monotonic_capture_time_ns": capture_mono_ns,
                        "host_arrival_time_ns": read_start_wall_ns,
                        "host_read_start_time_ns": read_start_wall_ns,
                        "host_read_end_time_ns": read_end_wall_ns,
                    }
            except Exception as exc:
                print(f"[{self.name}] 运行时错误: {exc}")
                time.sleep(0.05)

    def _close_hardware(self):
        self._ready = False
        if self._pipeline is not None:
            try:
                self._pipeline.stop()
            except Exception:
                pass
        self._pipeline = None
        self._profile = None
        self._align = None
        self._pointcloud = None

    def _open_pipeline(self):
        self._pipeline = rs.pipeline()
        config = rs.config()
        if self.config.serial_number:
            config.enable_device(self.config.serial_number)

        if self.config.enable_color:
            config.enable_stream(
                rs.stream.color,
                0,
                self.config.color.width,
                self.config.color.height,
                rs.format.bgr8,
                self.config.color.fps,
            )
        if self.config.enable_depth:
            config.enable_stream(
                rs.stream.depth,
                0,
                self.config.depth.width,
                self.config.depth.height,
                rs.format.z16,
                self.config.depth.fps,
            )
        if self.config.enable_ir1:
            config.enable_stream(
                rs.stream.infrared,
                1,
                self.config.infrared.width,
                self.config.infrared.height,
                rs.format.y8,
                self.config.infrared.fps,
            )
        if self.config.enable_ir2:
            config.enable_stream(
                rs.stream.infrared,
                2,
                self.config.infrared.width,
                self.config.infrared.height,
                rs.format.y8,
                self.config.infrared.fps,
            )
        if self.config.enable_imu:
            config.enable_stream(rs.stream.accel, rs.format.motion_xyz32f, self.config.imu.accel_fps)
            config.enable_stream(rs.stream.gyro, rs.format.motion_xyz32f, self.config.imu.gyro_fps)

        self._profile = self._pipeline.start(config)
        self._align = (
            rs.align(rs.stream.color)
            if self.config.enable_aligned_depth_to_color and self.config.enable_color and self.config.enable_depth
            else None
        )
        self._pointcloud = rs.pointcloud() if self.config.enable_pointcloud else None
        self._runtime_metadata = self._build_runtime_metadata()

    def _build_payload(self, frames):
        payload = {
            "color": None,
            "depth": None,
            "ir1": None,
            "ir2": None,
            "aligned_depth_to_color": None,
            "pointcloud": None,
            "imu_samples": [],
            "metadata": {},
        }

        has_any = False
        color_frame = frames.get_color_frame() if self.config.enable_color else None
        depth_frame = frames.get_depth_frame() if self.config.enable_depth else None
        ir1_frame = frames.get_infrared_frame(1) if self.config.enable_ir1 else None
        ir2_frame = frames.get_infrared_frame(2) if self.config.enable_ir2 else None

        if color_frame:
            payload["color"] = np.asanyarray(color_frame.get_data()).copy()
            has_any = True
        if depth_frame:
            payload["depth"] = np.asanyarray(depth_frame.get_data()).copy()
            has_any = True
        if ir1_frame:
            payload["ir1"] = np.asanyarray(ir1_frame.get_data()).copy()
            has_any = True
        if ir2_frame:
            payload["ir2"] = np.asanyarray(ir2_frame.get_data()).copy()
            has_any = True

        if self._align is not None:
            aligned_frames = self._align.process(frames)
            aligned_depth_frame = aligned_frames.get_depth_frame()
            if aligned_depth_frame:
                payload["aligned_depth_to_color"] = np.asanyarray(aligned_depth_frame.get_data()).copy()
                has_any = True

        imu_samples = self._collect_imu_samples(frames)
        payload["imu_samples"] = imu_samples
        if imu_samples:
            has_any = True

        if self._pointcloud is not None and depth_frame:
            pointcloud = self._build_pointcloud(depth_frame, color_frame)
            payload["pointcloud"] = pointcloud
            has_any = True

        payload["metadata"] = self._build_frame_metadata(frames, payload)
        return payload if has_any else None

    def _collect_imu_samples(self, frames):
        samples = []

        def _collector(frame):
            if not frame or not frame.is_motion_frame():
                return
            motion_frame = frame.as_motion_frame()
            motion = motion_frame.get_motion_data()
            stream_type = motion_frame.get_profile().stream_type()
            sample_type = "accel" if stream_type == rs.stream.accel else "gyro"
            sample = {
                "sample_type": sample_type,
                "frame_number": int(motion_frame.get_frame_number()),
                "timestamp_ms": float(motion_frame.get_timestamp()),
                "x": float(motion.x),
                "y": float(motion.y),
                "z": float(motion.z),
            }
            self._imu_samples.append(sample)

        frames.foreach(_collector)
        while self._imu_samples:
            samples.append(dict(self._imu_samples.popleft()))
        return samples

    def _build_pointcloud(self, depth_frame, color_frame):
        if color_frame and self.config.pointcloud.colored:
            self._pointcloud.map_to(color_frame)
        points = self._pointcloud.calculate(depth_frame)
        vertices = np.asarray(points.get_vertices(), dtype=np.float32).reshape(-1, 3).copy()
        payload = {"vertices": vertices}

        if color_frame and self.config.pointcloud.colored:
            texcoords = np.asarray(points.get_texture_coordinates(), dtype=np.float32).reshape(-1, 2)
            color_image = np.asanyarray(color_frame.get_data())
            payload["colors"] = _sample_point_colors(color_image, texcoords)
        return payload

    def _build_runtime_metadata(self):
        device = self._profile.get_device()
        metadata = {
            "device_name": device.get_info(rs.camera_info.name),
            "serial_number": device.get_info(rs.camera_info.serial_number),
            "depth_scale": None,
            "stream_profiles": {},
            "intrinsics": {},
        }
        try:
            metadata["depth_scale"] = device.first_depth_sensor().get_depth_scale()
        except Exception:
            metadata["depth_scale"] = None

        for stream_profile in self._profile.get_streams():
            key = _stream_key_from_profile(stream_profile)
            metadata["stream_profiles"][key] = {
                "fps": int(stream_profile.fps()),
                "stream_index": int(stream_profile.stream_index()),
                "stream_name": str(stream_profile.stream_name()),
                "stream_type": str(stream_profile.stream_type()),
                "format": str(stream_profile.format()),
            }
            if stream_profile.is_video_stream_profile():
                intrinsics = stream_profile.as_video_stream_profile().get_intrinsics()
                metadata["intrinsics"][key] = {
                    "width": intrinsics.width,
                    "height": intrinsics.height,
                    "fx": intrinsics.fx,
                    "fy": intrinsics.fy,
                    "ppx": intrinsics.ppx,
                    "ppy": intrinsics.ppy,
                    "coeffs": list(intrinsics.coeffs),
                }
        return metadata

    def _build_frame_metadata(self, frames, payload):
        frame_info = {}

        def _append(name, frame):
            if not frame:
                return
            frame_info[name] = {
                "frame_number": int(frame.get_frame_number()),
                "timestamp_ms": float(frame.get_timestamp()),
                "timestamp_domain": str(frame.get_frame_timestamp_domain()),
            }

        _append("color", frames.get_color_frame() if self.config.enable_color else None)
        _append("depth", frames.get_depth_frame() if self.config.enable_depth else None)
        _append("ir1", frames.get_infrared_frame(1) if self.config.enable_ir1 else None)
        _append("ir2", frames.get_infrared_frame(2) if self.config.enable_ir2 else None)

        pointcloud_vertices = 0
        pointcloud = payload.get("pointcloud")
        if isinstance(pointcloud, dict) and isinstance(pointcloud.get("vertices"), np.ndarray):
            pointcloud_vertices = int(pointcloud["vertices"].shape[0])

        return {
            "device": clone_realsense_payload(self._runtime_metadata),
            "frame_info": frame_info,
            "imu_sample_count": len(payload.get("imu_samples", [])),
            "pointcloud_vertices": pointcloud_vertices,
        }


def _stream_key_from_profile(stream_profile):
    stream_type = stream_profile.stream_type()
    stream_index = int(stream_profile.stream_index())
    if stream_type == rs.stream.color:
        return "color"
    if stream_type == rs.stream.depth:
        return "depth"
    if stream_type == rs.stream.infrared:
        return f"ir{stream_index}"
    if stream_type == rs.stream.accel:
        return "accel"
    if stream_type == rs.stream.gyro:
        return "gyro"
    return f"{stream_type}_{stream_index}"


def _sample_point_colors(color_image: np.ndarray, texcoords: np.ndarray) -> np.ndarray:
    height, width = color_image.shape[:2]
    xs = np.clip(np.round(texcoords[:, 0] * (width - 1)).astype(np.int32), 0, width - 1)
    ys = np.clip(np.round(texcoords[:, 1] * (height - 1)).astype(np.int32), 0, height - 1)
    return color_image[ys, xs].copy()


RealSenseSensor = RealsenseSensor
