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
    frame_queue_size: int = 128
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
        self._frame_queue = deque()
        self._dropped_frame_count = 0
        self._delivered_frame_count = 0
        self._max_queue_depth = 0

    def start(self):
        self.latest_data = None
        self.latest_timestamp = 0.0
        self.frame_count = 0
        self.latest_time_info = {}
        self._imu_samples.clear()
        self._runtime_metadata = {}
        self._ready = False
        self._frame_queue.clear()
        self._dropped_frame_count = 0
        self._delivered_frame_count = 0
        self._max_queue_depth = 0
        super().start()

    def get_data(self):
        with self.lock:
            data = clone_realsense_payload(self.latest_data)
            return data, self.latest_timestamp, self.frame_count

    def get_data_with_time_info(self):
        with self.lock:
            data = clone_realsense_payload(self.latest_data)
            return data, self.latest_timestamp, self.frame_count, dict(self.latest_time_info)

    def get_next_data_with_time_info(self):
        with self.lock:
            if not self._frame_queue:
                return None, 0.0, 0, {}
            packet = self._frame_queue.popleft()
            self._delivered_frame_count += 1
            data = clone_realsense_payload(packet["data"])
            return data, packet["timestamp"], packet["frame_id"], dict(packet["time_info"])

    def get_all_data_with_time_info(self):
        packets = []
        with self.lock:
            while self._frame_queue:
                packet = self._frame_queue.popleft()
                self._delivered_frame_count += 1
                data = clone_realsense_payload(packet["data"])
                packets.append((data, packet["timestamp"], packet["frame_id"], dict(packet["time_info"])))
        return packets

    def get_runtime_status(self):
        with self.lock:
            return {
                "running": self.running,
                "frame_count": self.frame_count,
                "produced_frame_count": self.frame_count,
                "delivered_frame_count": self._delivered_frame_count,
                "dropped_frame_count": self._dropped_frame_count,
                "latest_timestamp": self.latest_timestamp,
                "queue_depth": len(self._frame_queue),
                "max_queue_depth": self._max_queue_depth,
                "queue_capacity": max(1, int(self.config.frame_queue_size)),
                "ready": self._ready,
            }

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
                self._publish_frame_packet(
                    payload,
                    capture_wall_ns=capture_wall_ns,
                    capture_mono_ns=capture_mono_ns,
                    read_start_wall_ns=read_start_wall_ns,
                    read_end_wall_ns=read_end_wall_ns,
                )
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

        try:
            self._profile = self._pipeline.start(config)
        except Exception as exc:
            raise RuntimeError(_build_open_failure_diagnostics(self.config, exc)) from exc
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

    def _publish_frame_packet(
        self,
        payload: dict[str, Any],
        *,
        capture_wall_ns: int,
        capture_mono_ns: int,
        read_start_wall_ns: int,
        read_end_wall_ns: int,
    ) -> None:
        time_info = {
            "host_capture_time_ns": capture_wall_ns,
            "monotonic_capture_time_ns": capture_mono_ns,
            "host_arrival_time_ns": read_start_wall_ns,
            "host_read_start_time_ns": read_start_wall_ns,
            "host_read_end_time_ns": read_end_wall_ns,
        }
        with self.lock:
            self.frame_count += 1
            packet = {
                "data": payload,
                "timestamp": capture_wall_ns / 1_000_000_000.0,
                "frame_id": self.frame_count,
                "time_info": time_info,
            }
            queue_capacity = max(1, int(self.config.frame_queue_size))
            if len(self._frame_queue) >= queue_capacity:
                self._frame_queue.popleft()
                self._dropped_frame_count += 1
            self._frame_queue.append(packet)
            self._max_queue_depth = max(self._max_queue_depth, len(self._frame_queue))
            self.latest_data = payload
            self.latest_timestamp = packet["timestamp"]
            self.latest_time_info = dict(time_info)

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


def _build_open_failure_diagnostics(config: RealsenseConfig, error: Exception) -> str:
    requested_streams = _describe_requested_streams(config)
    lines = [str(error)]
    if requested_streams:
        lines.append("请求的流配置:")
        lines.extend(f"- {_format_stream_description(entry)}" for entry in requested_streams)

    try:
        device = _select_realsense_device(config)
    except Exception as exc:
        lines.append(f"无法枚举设备 profile: {exc}")
        return "\n".join(lines)

    if device is None:
        lines.append("未检测到可用的 RealSense 设备")
        return "\n".join(lines)

    device_name = device.get_info(rs.camera_info.name)
    serial_number = device.get_info(rs.camera_info.serial_number)
    lines.append(f"检测到设备: {device_name} ({serial_number})")

    supported_profiles = _collect_device_profiles(device)
    mismatches = _find_request_mismatches(requested_streams, supported_profiles)
    if mismatches:
        lines.append("下列请求在当前设备上没有找到精确匹配:")
        lines.extend(f"- {entry}" for entry in mismatches)
    elif requested_streams:
        lines.append("每个单独 stream profile 看起来都存在，失败更像是这些 profile 的组合不兼容。")

    return "\n".join(lines)


def _describe_requested_streams(config: RealsenseConfig) -> list[dict[str, object]]:
    requests: list[dict[str, object]] = []

    def _video_request(name: str, stream_type, stream_index: int, fmt, width: int, height: int, fps: int) -> None:
        requests.append(
            {
                "name": name,
                "stream_type": str(stream_type),
                "stream_index": stream_index,
                "format": str(fmt),
                "width": width,
                "height": height,
                "fps": fps,
            }
        )

    def _motion_request(name: str, stream_type, fmt, fps: int) -> None:
        requests.append(
            {
                "name": name,
                "stream_type": str(stream_type),
                "stream_index": 0,
                "format": str(fmt),
                "width": None,
                "height": None,
                "fps": fps,
            }
        )

    if config.enable_color:
        _video_request(
            "color",
            rs.stream.color,
            0,
            rs.format.bgr8,
            config.color.width,
            config.color.height,
            config.color.fps,
        )
    if config.enable_depth:
        _video_request(
            "depth",
            rs.stream.depth,
            0,
            rs.format.z16,
            config.depth.width,
            config.depth.height,
            config.depth.fps,
        )
    if config.enable_ir1:
        _video_request(
            "ir1",
            rs.stream.infrared,
            1,
            rs.format.y8,
            config.infrared.width,
            config.infrared.height,
            config.infrared.fps,
        )
    if config.enable_ir2:
        _video_request(
            "ir2",
            rs.stream.infrared,
            2,
            rs.format.y8,
            config.infrared.width,
            config.infrared.height,
            config.infrared.fps,
        )
    if config.enable_imu:
        _motion_request("accel", rs.stream.accel, rs.format.motion_xyz32f, config.imu.accel_fps)
        _motion_request("gyro", rs.stream.gyro, rs.format.motion_xyz32f, config.imu.gyro_fps)
    return requests


def _format_stream_description(entry: dict[str, object]) -> str:
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        stream_type = str(entry.get("stream_type"))
        stream_index = int(entry.get("stream_index", 0))
        if stream_type == str(rs.stream.infrared):
            name = f"ir{stream_index}"
        elif stream_type == str(rs.stream.color):
            name = "color"
        elif stream_type == str(rs.stream.depth):
            name = "depth"
        elif stream_type == str(rs.stream.accel):
            name = "accel"
        elif stream_type == str(rs.stream.gyro):
            name = "gyro"
        else:
            name = f"{stream_type}:{stream_index}"
    prefix = f"{name}: {entry['format']}"
    width = entry.get("width")
    height = entry.get("height")
    if isinstance(width, int) and isinstance(height, int):
        return f"{prefix} {width}x{height}@{entry['fps']}"
    return f"{prefix} @{entry['fps']}"


def _select_realsense_device(config: RealsenseConfig):
    context = rs.context()
    devices = context.query_devices()
    if len(devices) == 0:
        return None
    if config.serial_number:
        for device in devices:
            if device.get_info(rs.camera_info.serial_number) == config.serial_number:
                return device
        raise RuntimeError(f"未找到序列号为 {config.serial_number} 的 RealSense 设备")
    return devices[0]


def _collect_device_profiles(device) -> list[dict[str, object]]:
    profiles: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()

    for sensor in device.query_sensors():
        for profile in sensor.get_stream_profiles():
            stream_type = str(profile.stream_type())
            stream_index = int(profile.stream_index())
            fmt = str(profile.format())
            fps = int(profile.fps())
            width = None
            height = None
            if profile.is_video_stream_profile():
                video_profile = profile.as_video_stream_profile()
                width = int(video_profile.width())
                height = int(video_profile.height())
            key = (stream_type, stream_index, fmt, fps, width, height)
            if key in seen:
                continue
            seen.add(key)
            profiles.append(
                {
                    "stream_type": stream_type,
                    "stream_index": stream_index,
                    "format": fmt,
                    "fps": fps,
                    "width": width,
                    "height": height,
                }
            )
    return profiles


def _find_request_mismatches(
    requests: list[dict[str, object]],
    supported_profiles: list[dict[str, object]],
) -> list[str]:
    mismatches: list[str] = []
    for request in requests:
        if any(_profile_matches_request(profile, request) for profile in supported_profiles):
            continue
        candidate_profiles = [
            profile
            for profile in supported_profiles
            if profile["stream_type"] == request["stream_type"] and profile["stream_index"] == request["stream_index"]
        ]
        if not candidate_profiles:
            candidate_profiles = [
                profile
                for profile in supported_profiles
                if profile["stream_type"] == request["stream_type"]
            ]
        supported = ", ".join(_format_stream_description(profile) for profile in candidate_profiles[:8])
        if len(candidate_profiles) > 8:
            supported = f"{supported}, ..."
        if supported:
            mismatches.append(f"{_format_stream_description(request)}; 可用候选: {supported}")
        else:
            mismatches.append(f"{_format_stream_description(request)}; 当前设备未暴露同类 profile")
    return mismatches


def _profile_matches_request(profile: dict[str, object], request: dict[str, object]) -> bool:
    if profile["stream_type"] != request["stream_type"]:
        return False
    if profile["stream_index"] != request["stream_index"]:
        return False
    if profile["format"] != request["format"]:
        return False
    if profile["fps"] != request["fps"]:
        return False
    if profile.get("width") != request.get("width"):
        return False
    if profile.get("height") != request.get("height"):
        return False
    return True


def _sample_point_colors(color_image: np.ndarray, texcoords: np.ndarray) -> np.ndarray:
    height, width = color_image.shape[:2]
    xs = np.clip(np.round(texcoords[:, 0] * (width - 1)).astype(np.int32), 0, width - 1)
    ys = np.clip(np.round(texcoords[:, 1] * (height - 1)).astype(np.int32), 0, height - 1)
    return color_image[ys, xs].copy()


RealSenseSensor = RealsenseSensor
