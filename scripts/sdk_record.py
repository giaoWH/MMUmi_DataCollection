from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import json
import logging
import os
import select
import shutil
import signal
import sys
import termios
import time
import tty
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.config import deep_merge, load_config_file
from sdk.core import BufferedFrameAligner, SensorRegistry, SessionInfo, SystemClock, create_session_info
from sdk.logging import build_logger
from sdk.perception import OrbSlam3SessionProcessConfig
from sdk.processors import GravityCompensationConfig, GravityCompensator
from sdk.sensors.fake import (
    FakeCameraAdapter,
    FakeCameraConfig,
    FakeFTAdapter,
    FakeFTSensorConfig,
    FakeGelSightAdapter,
    FakeGelSightConfig,
    FakeIMUAdapter,
    FakeIMUSensorConfig,
    FakeMicrophoneAdapter,
    FakeMicrophoneConfig,
    FakeMotorsAdapter,
    FakeMotorsConfig,
    FakeRealSenseAdapter,
    FakeRealSenseConfig,
)
from sdk.sensors.legacy import (
    CameraSensorConfig,
    FTSensorConfig,
    GelSightSensorConfig,
    IMUSensorConfig,
    LegacyFTAdapter,
    LegacyGelSightAdapter,
    LegacyIMUAdapter,
    LegacyCameraAdapter,
    LegacyMicrophoneAdapter,
    LegacyMotorsAdapter,
    MicrophoneSensorConfig,
    MotorsSensorConfig,
)
from sdk.sensors.realsense import RealSenseConfig, RealSenseRGBDAdapter
from sdk.storage import SessionWriter

DEFAULT_RECORD_CONFIG_CANDIDATES = (
    REPO_ROOT / "configs" / "record.yaml",
    REPO_ROOT / "configs" / "record.yml",
    REPO_ROOT / "configs" / "record.json",
)

KEY_SPACE = "space"
KEY_ENTER = "enter"
KEY_QUIT_SESSION = "quit_session"
KEY_CTRL_C = "ctrl_c"

STATE_BOOTING = "booting"
STATE_IDLE_READY = "idle_ready"
STATE_RECORDING = "recording"
STATE_STOPPING = "stopping"
STATE_REVIEW_STOPPED = "review_stopped"
STATE_EXITING = "exiting"


@dataclass(frozen=True)
class RecorderConfig:
    output_root: str = "sessions"
    sensor_source: str = "real"
    align_rate_hz: float = 30.0
    duration_sec: float = 0.0
    interactive: bool = False
    startup_discard_sec: float = 0.5
    max_frame_age: float = 0.2
    enable_ft: bool = True
    enable_imu: bool = True
    enable_realsense: bool = True
    enable_motors: bool = False
    enable_microphone: bool = False
    enable_camera: bool = False
    enable_gelsight: bool = False
    enable_trajectory: bool = False
    ft: FTSensorConfig = FTSensorConfig()
    imu: IMUSensorConfig = IMUSensorConfig()
    realsense: RealSenseConfig = RealSenseConfig()
    motors: MotorsSensorConfig = MotorsSensorConfig()
    microphone: MicrophoneSensorConfig = MicrophoneSensorConfig()
    camera: CameraSensorConfig = CameraSensorConfig()
    gelsight: GelSightSensorConfig = GelSightSensorConfig()
    gravity_compensation: GravityCompensationConfig = GravityCompensationConfig()
    trajectory: OrbSlam3SessionProcessConfig = OrbSlam3SessionProcessConfig()


@dataclass
class RecordingSession:
    session_index: int
    session_info: SessionInfo
    writer: SessionWriter
    logger: logging.Logger
    aligner: BufferedFrameAligner
    last_written_frame_ids: dict[str, int]
    next_aligned_time_ns: int
    next_aligned_monotonic_time_ns: int
    sensor_status_baseline: dict[str, dict[str, object]]
    session_queue_peak: dict[str, int]


@dataclass
class StoppedSession:
    session_index: int
    session_info: SessionInfo
    sensor_runtime_status_snapshot: dict[str, dict[str, object]]
    session_sensor_stats: dict[str, dict[str, object]]
    writer_diagnostics: dict[str, object]


@dataclass
class StoppingSession:
    session: RecordingSession
    stop_reason: str
    stop_requested_monotonic_time_ns: int
    finalize_future: Future[StoppedSession]


class KeySource:
    def poll_key(self, timeout_sec: float = 0.0) -> str | None:
        raise NotImplementedError

    def drain_pending_input(self) -> None:
        return None

    def close(self) -> None:
        return None


class TerminalKeySource(KeySource):
    def __init__(self, stream=None) -> None:
        self._stream = stream or sys.stdin
        if not self._stream.isatty():
            raise RuntimeError("交互模式需要在 TTY 终端中运行")
        self._fd = self._stream.fileno()
        self._original_mode = termios.tcgetattr(self._fd)
        # cbreak 保留终端输出的换行处理，只关闭规范模式与回显，
        # 更适合一边单键交互、一边持续打印状态。
        tty.setcbreak(self._fd)
        self._closed = False

    def poll_key(self, timeout_sec: float = 0.0) -> str | None:
        if self._closed:
            return None
        ready, _, _ = select.select([self._fd], [], [], max(0.0, timeout_sec))
        if not ready:
            return None
        data = os.read(self._fd, 1)
        if not data:
            return None
        return data.decode("utf-8", errors="ignore")

    def drain_pending_input(self) -> None:
        while True:
            ready, _, _ = select.select([self._fd], [], [], 0.0)
            if not ready:
                return
            data = os.read(self._fd, 1)
            if not data:
                return

    def close(self) -> None:
        if self._closed:
            return
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._original_mode)
        self._closed = True


class TimedKeySource(KeySource):
    def __init__(self, timed_keys: list[tuple[float, str]], *, time_fn=time.perf_counter) -> None:
        self._timed_keys = list(timed_keys)
        self._time_fn = time_fn
        self._started_at = self._time_fn()
        self._index = 0

    def poll_key(self, timeout_sec: float = 0.0) -> str | None:
        del timeout_sec
        if self._index >= len(self._timed_keys):
            return None
        scheduled_at_sec, key = self._timed_keys[self._index]
        if (self._time_fn() - self._started_at) < scheduled_at_sec:
            return None
        self._index += 1
        return key

    def drain_pending_input(self) -> None:
        elapsed = self._time_fn() - self._started_at
        while self._index < len(self._timed_keys):
            scheduled_at_sec, _key = self._timed_keys[self._index]
            if scheduled_at_sec > elapsed:
                break
            self._index += 1


is_running = True


def _to_jsonable(value: object) -> object:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _json_clone(value: object) -> object:
    return json.loads(json.dumps(_to_jsonable(value), ensure_ascii=False))


def _handle_signal(_sig: int, _frame: object) -> None:
    global is_running
    is_running = False


def build_registry(config: RecorderConfig, clock: SystemClock) -> SensorRegistry:
    registry = SensorRegistry()
    if config.sensor_source == "fake":
        if config.enable_ft:
            registry.register(
                FakeFTAdapter(
                    FakeFTSensorConfig(
                        name=config.ft.name,
                        enable_torque=config.ft.enable_torque,
                    ),
                    clock,
                )
            )
        if config.enable_imu:
            registry.register(FakeIMUAdapter(FakeIMUSensorConfig(name=config.imu.name), clock))
        if config.enable_realsense:
            registry.register(
                FakeRealSenseAdapter(
                    FakeRealSenseConfig(
                        name=config.realsense.name,
                        width=config.realsense.width,
                        height=config.realsense.height,
                        fps=config.realsense.fps,
                        enable_color=config.realsense.enable_color,
                        enable_depth=config.realsense.enable_depth,
                        enable_ir1=config.realsense.enable_ir1,
                        enable_ir2=config.realsense.enable_ir2,
                        enable_imu=config.realsense.enable_imu,
                        enable_aligned_depth_to_color=config.realsense.derived.enable_aligned_depth_to_color,
                        enable_pointcloud=config.realsense.derived.enable_pointcloud,
                    ),
                    clock,
                )
            )
        if config.enable_motors and (config.motors.enable_motor_1 or config.motors.enable_motor_2):
            registry.register(
                FakeMotorsAdapter(
                    FakeMotorsConfig(
                        name=config.motors.name,
                        sample_rate_hz=100.0,
                        enable_motor_1=config.motors.enable_motor_1,
                        enable_motor_2=config.motors.enable_motor_2,
                    ),
                    clock,
                )
            )
        if config.enable_microphone:
            registry.register(
                FakeMicrophoneAdapter(
                    FakeMicrophoneConfig(
                        name=config.microphone.name,
                        channels=config.microphone.channels,
                        rate=config.microphone.rate,
                        chunk=config.microphone.chunk,
                    ),
                    clock,
                )
            )
        if config.enable_camera:
            registry.register(
                FakeCameraAdapter(
                    FakeCameraConfig(
                        name=config.camera.name,
                        width=config.camera.width,
                        height=config.camera.height,
                        fps=config.camera.fps,
                    ),
                    clock,
                )
            )
        if config.enable_gelsight:
            registry.register(
                FakeGelSightAdapter(
                    FakeGelSightConfig(
                        name=config.gelsight.name,
                        width=config.gelsight.width,
                        height=config.gelsight.height,
                        fps=config.gelsight.fps,
                    ),
                    clock,
                )
            )
        return registry

    if config.enable_ft:
        registry.register(LegacyFTAdapter(config.ft, clock))
    if config.enable_imu:
        registry.register(LegacyIMUAdapter(config.imu, clock))
    if config.enable_realsense:
        registry.register(RealSenseRGBDAdapter(config.realsense, clock))
    if config.enable_motors and (config.motors.enable_motor_1 or config.motors.enable_motor_2):
        registry.register(LegacyMotorsAdapter(config.motors, clock))
    if config.enable_microphone:
        registry.register(LegacyMicrophoneAdapter(config.microphone, clock))
    if config.enable_camera:
        registry.register(LegacyCameraAdapter(config.camera, clock))
    if config.enable_gelsight:
        registry.register(LegacyGelSightAdapter(config.gelsight, clock))
    return registry


def _find_sensor_name_by_modality(registry: SensorRegistry, modality: str) -> str | None:
    for sensor_name, sensor in registry.sensors.items():
        if sensor.modality == modality:
            return sensor_name
    return None


def run_static_calibration(
    registry: SensorRegistry,
    config: GravityCompensationConfig,
    logger: logging.Logger,
) -> tuple[GravityCompensator | None, dict[str, object]]:
    if not config.enabled:
        return None, {"enabled": False}

    ft_sensor_name = _find_sensor_name_by_modality(registry, "force_torque")
    imu_sensor_name = _find_sensor_name_by_modality(registry, "imu")
    if ft_sensor_name is None or imu_sensor_name is None:
        raise RuntimeError("启用重力补偿时必须同时具备 FT 和 IMU 传感器")

    if config.stabilization_sec > 0:
        logger.info("等待传感器稳定 %.2f 秒", config.stabilization_sec)
        time.sleep(config.stabilization_sec)

    compensator = GravityCompensator(config.mass, config.com)
    ft_sensor = registry.sensors[ft_sensor_name]
    imu_sensor = registry.sensors[imu_sensor_name]
    ft_samples: list[np.ndarray] = []
    quat_samples: list[np.ndarray] = []
    last_frame_ids = {ft_sensor_name: -1, imu_sensor_name: -1}

    logger.info("开始静态校准 %.2f 秒", config.calibration_duration_sec)
    t_start = time.time()
    while time.time() - t_start < config.calibration_duration_sec:
        ft_frame = ft_sensor.read_frame()
        imu_frame = imu_sensor.read_frame()
        if ft_frame is not None and imu_frame is not None:
            if (
                ft_frame.frame_id != last_frame_ids[ft_sensor_name]
                and imu_frame.frame_id != last_frame_ids[imu_sensor_name]
            ):
                ft_samples.append(np.asarray(ft_frame.payload["force"], dtype=np.float64))
                quat_samples.append(np.asarray(imu_frame.payload["quaternion"], dtype=np.float64))
                last_frame_ids[ft_sensor_name] = ft_frame.frame_id
                last_frame_ids[imu_sensor_name] = imu_frame.frame_id
        time.sleep(0.005)

    minimum = max(config.minimum_samples, 10)
    if len(ft_samples) < minimum:
        raise RuntimeError(f"静态校准数据不足: {len(ft_samples)} < {minimum}")
    if not compensator.calibrate_bias(ft_samples, quat_samples):
        raise RuntimeError("重力补偿静态校准失败")
    logger.info("重力补偿校准完成，样本数=%d, bias=%s", len(ft_samples), compensator.bias.tolist())

    notes = {
        "enabled": True,
        "calibrated": True,
        "sample_count": len(ft_samples),
        "mass": config.mass,
        "com": list(config.com),
        "bias": compensator.bias.tolist(),
    }
    return compensator, notes


def discard_startup_frames(
    registry: SensorRegistry,
    *,
    discard_sec: float,
    poll_sleep_sec: float = 0.002,
) -> dict[str, int]:
    discarded_counts = {sensor_name: 0 for sensor_name in registry.sensors}
    if discard_sec <= 0:
        return discarded_counts

    deadline = time.perf_counter() + discard_sec
    while is_running and time.perf_counter() < deadline:
        consumed_any = False
        for sensor_name, sensor in registry.sensors.items():
            frames = sensor.read_available_frames()
            if not frames:
                continue
            discarded_counts[sensor_name] += len(frames)
            consumed_any = True
        if not consumed_any:
            time.sleep(poll_sleep_sec)
    return discarded_counts


def _drain_sensor_queues(registry: SensorRegistry) -> dict[str, int]:
    drained_counts = {sensor_name: 0 for sensor_name in registry.sensors}
    for sensor_name, sensor in registry.sensors.items():
        frames = sensor.read_available_frames()
        if not frames:
            continue
        drained_counts[sensor_name] += len(frames)
    return drained_counts


def _find_failed_sensors(registry: SensorRegistry) -> list[str]:
    failed: list[str] = []
    for sensor_name, status in registry.get_status().items():
        if status.get("running") is False:
            failed.append(sensor_name)
    return failed


def _status_counter(status: dict[str, object], key: str) -> int:
    fallback_keys = {
        "produced_frame_count": ("frame_count",),
        "delivered_frame_count": ("frame_count",),
    }
    value = status.get(key)
    if value is None:
        for fallback_key in fallback_keys.get(key, ()):
            value = status.get(fallback_key)
            if value is not None:
                break
    if value is None:
        value = 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _capture_sensor_status_snapshot(registry: SensorRegistry) -> dict[str, dict[str, object]]:
    return {
        sensor_name: dict(status)
        for sensor_name, status in registry.get_status().items()
    }


def _update_session_queue_peaks(
    session: RecordingSession,
    sensor_status_snapshot: dict[str, dict[str, object]],
) -> None:
    for sensor_name, status in sensor_status_snapshot.items():
        queue_depth = _status_counter(status, "queue_depth")
        session.session_queue_peak[sensor_name] = max(
            session.session_queue_peak.get(sensor_name, 0),
            queue_depth,
        )


def _build_session_sensor_stats(
    session: RecordingSession,
    sensor_status_snapshot: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    payload: dict[str, dict[str, object]] = {}
    for sensor_name, status in sensor_status_snapshot.items():
        baseline = session.sensor_status_baseline.get(sensor_name, {})
        payload[sensor_name] = {
            "running": status.get("running"),
            "frame_count": max(
                0,
                _status_counter(status, "frame_count") - _status_counter(baseline, "frame_count"),
            ),
            "produced_frame_count": max(
                0,
                _status_counter(status, "produced_frame_count") - _status_counter(baseline, "produced_frame_count"),
            ),
            "delivered_frame_count": max(
                0,
                _status_counter(status, "delivered_frame_count") - _status_counter(baseline, "delivered_frame_count"),
            ),
            "dropped_frame_count": max(
                0,
                _status_counter(status, "dropped_frame_count") - _status_counter(baseline, "dropped_frame_count"),
            ),
            "latest_timestamp": status.get("latest_timestamp"),
            "queue_depth": _status_counter(status, "queue_depth"),
            "max_queue_depth": max(
                session.session_queue_peak.get(sensor_name, 0),
                _status_counter(status, "queue_depth"),
            ),
            "queue_capacity": status.get("queue_capacity"),
        }
    return payload


def _format_int(value: object) -> str:
    if value is None:
        return "-"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _format_float(value: object, digits: int = 3) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _print_operator_summary(
    *,
    session_info,
    config: RecorderConfig,
    sensor_status: dict[str, dict[str, object]],
    writer_diagnostics: dict[str, object],
    startup_discard_notes: dict[str, object] | None = None,
) -> None:
    print("")
    print("=== 录制摘要 ===")
    print(f"Session: {session_info.session_id}")
    print(f"输出目录: {session_info.output_dir}")
    print(f"日志文件: {session_info.output_dir / 'logs' / 'sdk_record.log'}")
    if config.duration_sec > 0 and not config.interactive:
        summary = f"目标录制时长: {config.duration_sec:.1f} 秒"
        if config.startup_discard_sec > 0:
            summary += f" | 开头丢弃: {config.startup_discard_sec:.1f} 秒"
        print(summary)

    if startup_discard_notes and startup_discard_notes.get("enabled"):
        discarded = startup_discard_notes.get("discarded_frames", {})
        discarded_summary = ", ".join(
            f"{sensor}={count}"
            for sensor, count in discarded.items()
            if int(count) > 0
        )
        print(f"启动阶段丢弃帧: {discarded_summary or '-'}")

    print("")
    print("传感器状态:")
    print("名称         产出    写入    丢弃    队列    队列峰值")
    for sensor_name, status in sensor_status.items():
        produced = status.get("produced_frame_count", status.get("frame_count"))
        delivered = status.get("delivered_frame_count", status.get("frame_count"))
        dropped = status.get("dropped_frame_count")
        queue_depth = status.get("queue_depth")
        max_queue_depth = status.get("max_queue_depth")
        print(
            f"{sensor_name:<10} "
            f"{_format_int(produced):>6} "
            f"{_format_int(delivered):>7} "
            f"{_format_int(dropped):>7} "
            f"{_format_int(queue_depth):>7} "
            f"{_format_int(max_queue_depth):>10}"
        )

    write_latency = writer_diagnostics.get("write_latency", {})
    artifacts = writer_diagnostics.get("artifacts", {})
    enqueued_counts = writer_diagnostics.get("enqueued_counts", {})
    print("")
    print("写盘状态:")
    print(
        "  记录数: "
        f"sensor={_format_int(enqueued_counts.get('sensor'))}, "
        f"aligned={_format_int(enqueued_counts.get('aligned'))}, "
        f"trajectory={_format_int(enqueued_counts.get('trajectory'))}"
    )
    print(
        "  队列: "
        f"pending={_format_int(writer_diagnostics.get('pending_items'))}, "
        f"peak={_format_int(writer_diagnostics.get('max_queue_depth'))}, "
        f"artifact_pending={_format_int(artifacts.get('pending'))}, "
        f"artifact_peak={_format_int(artifacts.get('max_pending'))}"
    )
    print(
        "  延迟: "
        f"mean={_format_float(write_latency.get('mean_sec'), 6)}s, "
        f"max={_format_float(write_latency.get('max_sec'), 6)}s"
    )
    error = writer_diagnostics.get("error")
    print(f"  错误: {error or '-'}")


def _build_default_payload() -> dict[str, object]:
    return asdict(RecorderConfig())


def _resolve_record_config_path(config_path: str | None) -> Path | None:
    if config_path:
        raw_path = Path(config_path)
        candidates = [raw_path]
        if not raw_path.is_absolute():
            candidates.append(REPO_ROOT / raw_path)

        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()

        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    for candidate in DEFAULT_RECORD_CONFIG_CANDIDATES:
        if candidate.exists():
            return candidate.resolve()
    return None


def _set_nested_value(payload: dict[str, object], path: tuple[str, ...], value: object) -> None:
    cursor = payload
    for key in path[:-1]:
        next_value = cursor.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            cursor[key] = next_value
        cursor = next_value
    cursor[path[-1]] = value


def _build_cli_overrides(args: argparse.Namespace) -> dict[str, object]:
    overrides: dict[str, object] = {}
    mapping = {
        "output_root": ("output_root",),
        "sensor_source": ("sensor_source",),
        "align_rate_hz": ("align_rate_hz",),
        "duration_sec": ("duration_sec",),
        "startup_discard_sec": ("startup_discard_sec",),
        "max_frame_age": ("max_frame_age",),
        "enable_ft": ("enable_ft",),
        "enable_imu": ("enable_imu",),
        "enable_realsense": ("enable_realsense",),
        "enable_motors": ("enable_motors",),
        "enable_microphone": ("enable_microphone",),
        "enable_camera": ("enable_camera",),
        "enable_gelsight": ("enable_gelsight",),
        "enable_trajectory": ("enable_trajectory",),
        "ft_port": ("ft", "port"),
        "imu_port": ("imu", "port"),
        "motors_port": ("motors", "port"),
        "microphone_device_index": ("microphone", "device_index"),
        "microphone_channels": ("microphone", "channels"),
        "microphone_rate": ("microphone", "rate"),
        "microphone_chunk": ("microphone", "chunk"),
        "camera_device_index": ("camera", "device_index"),
        "camera_width": ("camera", "width"),
        "camera_height": ("camera", "height"),
        "camera_fps": ("camera", "fps"),
        "gelsight_device_index": ("gelsight", "device_index"),
        "gelsight_width": ("gelsight", "width"),
        "gelsight_height": ("gelsight", "height"),
        "gelsight_fps": ("gelsight", "fps"),
        "realsense_width": ("realsense", "width"),
        "realsense_height": ("realsense", "height"),
        "realsense_fps": ("realsense", "fps"),
    }

    for attr_name, path in mapping.items():
        if hasattr(args, attr_name):
            _set_nested_value(overrides, path, getattr(args, attr_name))
    return overrides


def _add_toggle_arguments(parser: argparse.ArgumentParser, sensor_name: str, dest: str) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(f"--enable-{sensor_name}", dest=dest, action="store_true")
    group.add_argument(f"--disable-{sensor_name}", dest=dest, action="store_false")


def parse_args(argv: list[str] | None = None) -> tuple[RecorderConfig, Path | None]:
    parser = argparse.ArgumentParser(
        description="多模态 SDK 录制入口",
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--config",
        help="录制配置文件路径；未提供时会自动尝试 configs/record.yaml",
    )
    parser.add_argument("--output-root")
    parser.add_argument("--sensor-source", choices=["real", "fake"])
    parser.add_argument("--align-rate", dest="align_rate_hz", type=float)
    parser.add_argument("--duration", dest="duration_sec", type=float)
    parser.add_argument("--startup-discard-sec", dest="startup_discard_sec", type=float)
    parser.add_argument("--max-frame-age", type=float)
    _add_toggle_arguments(parser, "ft", "enable_ft")
    _add_toggle_arguments(parser, "imu", "enable_imu")
    _add_toggle_arguments(parser, "realsense", "enable_realsense")
    _add_toggle_arguments(parser, "motors", "enable_motors")
    _add_toggle_arguments(parser, "microphone", "enable_microphone")
    _add_toggle_arguments(parser, "camera", "enable_camera")
    _add_toggle_arguments(parser, "gelsight", "enable_gelsight")
    _add_toggle_arguments(parser, "trajectory", "enable_trajectory")
    parser.add_argument("--ft-port", dest="ft_port")
    parser.add_argument("--imu-port", dest="imu_port")
    parser.add_argument("--motors-port", dest="motors_port")
    parser.add_argument("--microphone-device-index", dest="microphone_device_index", type=int)
    parser.add_argument("--microphone-channels", dest="microphone_channels", type=int)
    parser.add_argument("--microphone-rate", dest="microphone_rate", type=int)
    parser.add_argument("--microphone-chunk", dest="microphone_chunk", type=int)
    parser.add_argument("--camera-device-index", dest="camera_device_index", type=int)
    parser.add_argument("--camera-width", dest="camera_width", type=int)
    parser.add_argument("--camera-height", dest="camera_height", type=int)
    parser.add_argument("--camera-fps", dest="camera_fps", type=int)
    parser.add_argument("--gelsight-device-index", dest="gelsight_device_index", type=int)
    parser.add_argument("--gelsight-width", dest="gelsight_width", type=int)
    parser.add_argument("--gelsight-height", dest="gelsight_height", type=int)
    parser.add_argument("--gelsight-fps", dest="gelsight_fps", type=int)
    parser.add_argument("--realsense-width", dest="realsense_width", type=int)
    parser.add_argument("--realsense-height", dest="realsense_height", type=int)
    parser.add_argument("--realsense-fps", dest="realsense_fps", type=int)
    args = parser.parse_args(argv)

    config_path = _resolve_record_config_path(getattr(args, "config", None))
    payload = _build_default_payload()
    if config_path is not None:
        payload = deep_merge(payload, load_config_file(config_path))
    payload = deep_merge(payload, _build_cli_overrides(args))
    gelsight_payload = dict(payload.get("gelsight", {}))
    gelsight_payload.pop("flip_vertical", None)
    gelsight_payload.pop("flip_horizontal", None)

    config = RecorderConfig(
        output_root=payload["output_root"],
        sensor_source=payload.get("sensor_source", "real"),
        align_rate_hz=payload["align_rate_hz"],
        duration_sec=payload["duration_sec"],
        interactive=payload.get("interactive", False),
        startup_discard_sec=payload["startup_discard_sec"],
        max_frame_age=payload["max_frame_age"],
        enable_ft=payload["enable_ft"],
        enable_imu=payload["enable_imu"],
        enable_realsense=payload["enable_realsense"],
        enable_motors=payload.get("enable_motors", False),
        enable_microphone=payload.get("enable_microphone", False),
        enable_camera=payload.get("enable_camera", False),
        enable_gelsight=payload.get("enable_gelsight", False),
        enable_trajectory=payload.get("enable_trajectory", False),
        ft=FTSensorConfig(**payload.get("ft", {})),
        imu=IMUSensorConfig(**payload.get("imu", {})),
        realsense=RealSenseConfig.from_dict(payload.get("realsense", {})),
        motors=MotorsSensorConfig(**payload.get("motors", {})),
        microphone=MicrophoneSensorConfig(**payload.get("microphone", {})),
        camera=CameraSensorConfig(**payload.get("camera", {})),
        gelsight=GelSightSensorConfig(**gelsight_payload),
        gravity_compensation=GravityCompensationConfig(**payload.get("gravity_compensation", {})),
        trajectory=OrbSlam3SessionProcessConfig(**payload.get("trajectory", {})),
    )
    return config, config_path


def _normalize_key(raw_key: str | None) -> str | None:
    if raw_key is None:
        return None
    if raw_key == " ":
        return KEY_SPACE
    if raw_key in {"\r", "\n"}:
        return KEY_ENTER
    if raw_key.lower() == "q":
        return KEY_QUIT_SESSION
    if raw_key == "\x03":
        return KEY_CTRL_C
    return None


def _warn_deprecated_trajectory(config: RecorderConfig, logger: logging.Logger | None = None) -> None:
    if not config.enable_trajectory:
        return
    warning_message = (
        "检测到已废弃的 enable_trajectory=true 配置；"
        "sdk_record.py 不再在录制结束后自动解算轨迹，请将 session 拷贝到 PC 后运行 scripts/sdk_process_trajectory.py"
    )
    print(warning_message)
    if logger is not None:
        logger.warning(warning_message)


def _build_shared_calibration_notes(
    calibration_notes: dict[str, object],
    startup_discard_notes: dict[str, object],
) -> dict[str, object]:
    return {
        "gravity_compensation": _json_clone(calibration_notes),
        "startup_discard": _json_clone(startup_discard_notes),
    }


def _create_session_info(
    *,
    config: RecorderConfig,
    config_path: Path | None,
    registry: SensorRegistry,
    record_run_id: str | None = None,
    shared_calibration: dict[str, object] | None = None,
    session_index: int | None = None,
) -> SessionInfo:
    notes = {
        "entrypoint": "scripts/sdk_record.py",
        "config_file": str(config_path) if config_path is not None else None,
    }
    if record_run_id is not None:
        notes.update(
            {
                "record_mode": "interactive",
                "record_run_id": record_run_id,
                "shared_calibration": _json_clone(shared_calibration or {}),
                "session_index": session_index,
                "discarded": False,
            }
        )
    return create_session_info(
        config.output_root,
        sensors=registry.get_metadata(),
        config=asdict(config),
        notes=notes,
    )


def _create_session_logger(session_info: SessionInfo) -> logging.Logger:
    return build_logger(
        f"sdk.record.session.{session_info.session_id}",
        log_file=session_info.output_dir / "logs" / "sdk_record.log",
        include_stream=False,
    )


def _record_iteration(
    *,
    session: RecordingSession,
    registry: SensorRegistry,
    compensator: GravityCompensator | None,
    ft_sensor_name: str | None,
    imu_sensor_name: str | None,
    align_interval_ns: int,
    cutoff_monotonic_time_ns: int | None = None,
) -> object | None:
    def _frame_time_ns(frame) -> int | None:
        if frame.time.monotonic_time_ns is not None:
            return int(frame.time.monotonic_time_ns)
        if frame.time.host_time_ns is not None:
            return int(frame.time.host_time_ns)
        return None

    for sensor_name, sensor in registry.sensors.items():
        frames = sensor.read_available_frames()
        if not frames:
            continue
        for frame in frames:
            if cutoff_monotonic_time_ns is not None:
                frame_time_ns = _frame_time_ns(frame)
                if frame_time_ns is None or frame_time_ns > cutoff_monotonic_time_ns:
                    continue
            if frame.frame_id == session.last_written_frame_ids[sensor_name]:
                continue
            session.aligner.add_frame(frame)
            session.writer.write_sensor_frame(frame)
            session.last_written_frame_ids[sensor_name] = frame.frame_id

    current_monotonic_time_ns = time.perf_counter_ns()
    if cutoff_monotonic_time_ns is not None:
        current_monotonic_time_ns = min(current_monotonic_time_ns, cutoff_monotonic_time_ns)
    last_aligned = None
    while session.next_aligned_monotonic_time_ns <= current_monotonic_time_ns:
        aligned = session.aligner.align(
            session.next_aligned_time_ns / 1_000_000_000.0,
            aligned_time_ns=session.next_aligned_time_ns,
            aligned_monotonic_time_ns=session.next_aligned_monotonic_time_ns,
        )
        if (
            compensator is not None
            and ft_sensor_name is not None
            and imu_sensor_name is not None
            and ft_sensor_name in aligned.frames
            and imu_sensor_name in aligned.frames
        ):
            ft_force = np.asarray(aligned.frames[ft_sensor_name].payload["force"], dtype=np.float64)
            imu_quaternion = np.asarray(aligned.frames[imu_sensor_name].payload["quaternion"], dtype=np.float64)
            pure_force, gravity_force = compensator.process(ft_force, imu_quaternion)
            aligned.metadata["gravity_compensation"] = {
                "applied": True,
                "pure_force": pure_force.tolist(),
                "gravity_force": gravity_force.tolist(),
                "bias": compensator.bias.tolist(),
            }
            session.logger.debug("重力补偿已应用: seq=%d", aligned.sequence_id)
        session.writer.write_aligned_frame(aligned)
        last_aligned = aligned
        session.next_aligned_time_ns += align_interval_ns
        session.next_aligned_monotonic_time_ns += align_interval_ns
    return last_aligned


def _print_align_progress(last_aligned: object | None) -> None:
    if last_aligned is None:
        return
    missing = ",".join(last_aligned.missing_sensors) if last_aligned.missing_sensors else "-"
    print(
        f"\r[Align] seq={last_aligned.sequence_id:06d} "
        f"missing={missing:<20} "
        f"frames={len(last_aligned.frames)}",
        end="",
    )


def _complete_stopped_session(
    *,
    session: RecordingSession,
    sensor_runtime_status_snapshot: dict[str, dict[str, object]],
    session_sensor_stats: dict[str, dict[str, object]],
    stop_reason: str,
) -> StoppedSession:
    session.session_info.notes["stop_reason"] = stop_reason
    session.session_info.notes["sensor_runtime_status_snapshot"] = _to_jsonable(sensor_runtime_status_snapshot)
    session.session_info.notes["session_sensor_stats"] = _to_jsonable(session_sensor_stats)
    pre_close_writer_diagnostics = session.writer.get_diagnostics()
    session.session_info.notes["writer_diagnostics_snapshot"] = _to_jsonable(pre_close_writer_diagnostics)
    session.writer.manifest = session.writer._build_manifest()
    session.logger.info("session 结束，原因=%s", stop_reason)
    session.logger.info(
        "传感器运行状态快照: %s",
        json.dumps(_to_jsonable(sensor_runtime_status_snapshot), ensure_ascii=False),
    )
    session.logger.info(
        "session 级统计: %s",
        json.dumps(_to_jsonable(session_sensor_stats), ensure_ascii=False),
    )
    session.logger.info(
        "写盘诊断(关闭前): %s",
        json.dumps(_to_jsonable(pre_close_writer_diagnostics), ensure_ascii=False),
    )
    session.writer.close()
    session.writer.manifest = session.writer._build_manifest()
    session.writer._write_manifest()
    final_writer_diagnostics = session.writer.get_diagnostics()
    session.logger.info(
        "最终写盘诊断: %s",
        json.dumps(_to_jsonable(final_writer_diagnostics), ensure_ascii=False),
    )
    return StoppedSession(
        session_index=session.session_index,
        session_info=session.session_info,
        sensor_runtime_status_snapshot=sensor_runtime_status_snapshot,
        session_sensor_stats=session_sensor_stats,
        writer_diagnostics=final_writer_diagnostics,
    )


def _discard_stopped_session(stopped_session: StoppedSession, *, reason: str) -> None:
    output_dir = stopped_session.session_info.output_dir
    print(f"放弃 session {stopped_session.session_info.session_id}，原因: {reason}")
    if output_dir.exists():
        shutil.rmtree(output_dir)


def _start_interactive_session(
    *,
    config: RecorderConfig,
    config_path: Path | None,
    registry: SensorRegistry,
    record_run_id: str,
    shared_calibration: dict[str, object],
    session_index: int,
) -> RecordingSession:
    _drain_sensor_queues(registry)
    baseline_status = _capture_sensor_status_snapshot(registry)
    session_info = _create_session_info(
        config=config,
        config_path=config_path,
        registry=registry,
        record_run_id=record_run_id,
        shared_calibration=shared_calibration,
        session_index=session_index,
    )
    logger = _create_session_logger(session_info)
    logger.info("交互式 session 创建完成: index=%d, dir=%s", session_index, session_info.output_dir)
    writer = SessionWriter(session_info, async_writes=True)
    start_wall_time_ns = time.time_ns()
    start_monotonic_time_ns = time.perf_counter_ns()
    print("")
    print(f"开始录制 session #{session_index}: {session_info.session_id}")
    print(f"输出目录: {session_info.output_dir}")
    print("再次按空格停止录制")
    return RecordingSession(
        session_index=session_index,
        session_info=session_info,
        writer=writer,
        logger=logger,
        aligner=BufferedFrameAligner(
            required_sensors=list(registry.sensors.keys()),
            max_frame_age=config.max_frame_age,
        ),
        last_written_frame_ids={name: -1 for name in registry.sensors},
        next_aligned_time_ns=start_wall_time_ns,
        next_aligned_monotonic_time_ns=start_monotonic_time_ns,
        sensor_status_baseline=baseline_status,
        session_queue_peak={
            sensor_name: _status_counter(status, "queue_depth")
            for sensor_name, status in baseline_status.items()
        },
    )


def _begin_stopping_session(
    *,
    session: RecordingSession,
    registry: SensorRegistry,
    finalize_executor: ThreadPoolExecutor,
    stop_reason: str,
    stop_requested_monotonic_time_ns: int,
    compensator: GravityCompensator | None,
    ft_sensor_name: str | None,
    imu_sensor_name: str | None,
    align_interval_ns: int,
) -> StoppingSession:
    pre_stop_status = _capture_sensor_status_snapshot(registry)
    _update_session_queue_peaks(session, pre_stop_status)
    _record_iteration(
        session=session,
        registry=registry,
        compensator=compensator,
        ft_sensor_name=ft_sensor_name,
        imu_sensor_name=imu_sensor_name,
        align_interval_ns=align_interval_ns,
        cutoff_monotonic_time_ns=stop_requested_monotonic_time_ns,
    )
    stop_status = _capture_sensor_status_snapshot(registry)
    _update_session_queue_peaks(session, stop_status)
    session_sensor_stats = _build_session_sensor_stats(session, stop_status)
    finalize_future = finalize_executor.submit(
        _complete_stopped_session,
        session=session,
        sensor_runtime_status_snapshot=_json_clone(stop_status),
        session_sensor_stats=_json_clone(session_sensor_stats),
        stop_reason=stop_reason,
    )
    return StoppingSession(
        session=session,
        stop_reason=stop_reason,
        stop_requested_monotonic_time_ns=stop_requested_monotonic_time_ns,
        finalize_future=finalize_future,
    )


def _run_noninteractive(
    *,
    config: RecorderConfig,
    config_path: Path | None,
) -> int:
    clock = SystemClock()
    registry = build_registry(config, clock)

    print("=== SDK 录制启动中 ===")
    if config_path is not None:
        print(f"配置文件: {config_path}")
    print(f"数据源: {config.sensor_source}")
    registry.start_all()
    print("等待传感器就绪...")
    while is_running:
        if registry.wait_until_ready(timeout=0.2):
            break
    if not is_running:
        print("录制在传感器就绪前被中断")
        registry.stop_all()
        return 1

    session_info = _create_session_info(
        config=config,
        config_path=config_path,
        registry=registry,
    )
    logger = _create_session_logger(session_info)
    logger.info("SDK 录制启动，数据源=%s", config.sensor_source)
    if config_path is not None:
        logger.info("加载配置文件: %s", config_path)
    _warn_deprecated_trajectory(config, logger)
    compensator, calibration_notes = run_static_calibration(registry, config.gravity_compensation, logger)
    session_info.notes["gravity_compensation"] = calibration_notes

    startup_discard_notes = {
        "enabled": bool(config.startup_discard_sec > 0),
        "discard_sec": float(max(0.0, config.startup_discard_sec)),
        "discarded_frames": {sensor_name: 0 for sensor_name in registry.sensors},
    }
    if config.startup_discard_sec > 0:
        discard_sec = max(0.0, config.startup_discard_sec)
        print(f"丢弃启动阶段数据: {discard_sec:.1f} 秒")
        logger.info("开始丢弃启动阶段数据 %.2f 秒", discard_sec)
        discarded_counts = discard_startup_frames(registry, discard_sec=discard_sec)
        startup_discard_notes["discarded_frames"] = discarded_counts
        logger.info("启动阶段数据丢弃完成: %s", json.dumps(discarded_counts, ensure_ascii=False))
        if not is_running:
            print("录制在启动阶段数据丢弃期间被中断")
            registry.stop_all()
            return 1
    session_info.notes["startup_discard"] = startup_discard_notes

    writer = SessionWriter(session_info, async_writes=True)
    aligner = BufferedFrameAligner(
        required_sensors=list(registry.sensors.keys()),
        max_frame_age=config.max_frame_age,
    )
    last_written_frame_ids = {name: -1 for name in registry.sensors}
    ft_sensor_name = _find_sensor_name_by_modality(registry, "force_torque")
    imu_sensor_name = _find_sensor_name_by_modality(registry, "imu")

    print(f"Session: {session_info.session_id}")
    print(f"输出目录: {session_info.output_dir}")
    logger.info("Session 创建完成: %s", session_info.output_dir)
    if config.duration_sec > 0:
        if config.startup_discard_sec > 0:
            print(f"录制时长: {config.duration_sec:.1f} 秒（前 {config.startup_discard_sec:.1f} 秒已丢弃）")
        else:
            print(f"录制时长: {config.duration_sec:.1f} 秒")
    else:
        print("录制时长: 持续运行，按 Ctrl+C 停止")

    loop_interval = 1.0 / config.align_rate_hz if config.align_rate_hz > 0 else 0.05
    start_wall_time_ns = time.time_ns()
    start_monotonic_time_ns = time.perf_counter_ns()
    next_aligned_time_ns = start_wall_time_ns
    next_aligned_monotonic_time_ns = start_monotonic_time_ns
    align_interval_ns = max(1, int(round(1_000_000_000.0 / max(config.align_rate_hz, 1e-6))))
    end_monotonic_time_ns = (
        start_monotonic_time_ns + int(round(config.duration_sec * 1_000_000_000.0))
        if config.duration_sec > 0
        else None
    )

    try:
        while is_running:
            loop_start = time.perf_counter()

            for sensor_name, sensor in registry.sensors.items():
                frames = sensor.read_available_frames()
                if not frames:
                    continue
                for frame in frames:
                    if frame.frame_id == last_written_frame_ids[sensor_name]:
                        continue
                    aligner.add_frame(frame)
                    writer.write_sensor_frame(frame)
                    last_written_frame_ids[sensor_name] = frame.frame_id

            current_monotonic_time_ns = time.perf_counter_ns()
            cutoff_monotonic_ns = current_monotonic_time_ns
            if end_monotonic_time_ns is not None:
                cutoff_monotonic_ns = min(cutoff_monotonic_ns, end_monotonic_time_ns)

            last_aligned = None
            while next_aligned_monotonic_time_ns <= cutoff_monotonic_ns:
                aligned = aligner.align(
                    next_aligned_time_ns / 1_000_000_000.0,
                    aligned_time_ns=next_aligned_time_ns,
                    aligned_monotonic_time_ns=next_aligned_monotonic_time_ns,
                )
                if (
                    compensator is not None
                    and ft_sensor_name is not None
                    and imu_sensor_name is not None
                    and ft_sensor_name in aligned.frames
                    and imu_sensor_name in aligned.frames
                ):
                    ft_force = np.asarray(aligned.frames[ft_sensor_name].payload["force"], dtype=np.float64)
                    imu_quaternion = np.asarray(aligned.frames[imu_sensor_name].payload["quaternion"], dtype=np.float64)
                    pure_force, gravity_force = compensator.process(ft_force, imu_quaternion)
                    aligned.metadata["gravity_compensation"] = {
                        "applied": True,
                        "pure_force": pure_force.tolist(),
                        "gravity_force": gravity_force.tolist(),
                        "bias": compensator.bias.tolist(),
                    }
                    logger.debug("重力补偿已应用: seq=%d", aligned.sequence_id)
                writer.write_aligned_frame(aligned)
                last_aligned = aligned
                next_aligned_time_ns += align_interval_ns
                next_aligned_monotonic_time_ns += align_interval_ns

            _print_align_progress(last_aligned)
            if end_monotonic_time_ns is not None and next_aligned_monotonic_time_ns > end_monotonic_time_ns:
                break

            elapsed = time.perf_counter() - loop_start
            time.sleep(max(0.0, loop_interval - elapsed))
    finally:
        print("\n正在停止传感器...")
        logger.info("正在停止传感器")
        registry.stop_all()
        sensor_status = registry.get_status()
        session_info.notes["sensor_runtime_status"] = _to_jsonable(sensor_status)
        writer_diagnostics = writer.get_diagnostics()
        session_info.notes["writer_diagnostics_snapshot"] = _to_jsonable(writer_diagnostics)
        logger.info("传感器运行状态: %s", json.dumps(_to_jsonable(sensor_status), ensure_ascii=False))
        logger.info("写盘诊断: %s", json.dumps(_to_jsonable(writer_diagnostics), ensure_ascii=False))
        writer.close()
        final_writer_diagnostics = writer.get_diagnostics()
        logger.info("最终写盘诊断: %s", json.dumps(_to_jsonable(final_writer_diagnostics), ensure_ascii=False))
        logger.info("录制结束")
        _print_operator_summary(
            session_info=session_info,
            config=config,
            sensor_status=sensor_status,
            writer_diagnostics=final_writer_diagnostics,
            startup_discard_notes=session_info.notes.get("startup_discard"),
        )

    print("完成。")
    return 0


def _run_interactive(
    *,
    config: RecorderConfig,
    config_path: Path | None,
    key_source: KeySource | None = None,
) -> int:
    global is_running
    owned_key_source = False
    if key_source is None:
        key_source = TerminalKeySource()
        owned_key_source = True

    clock = SystemClock()
    registry = build_registry(config, clock)
    run_logger = build_logger("sdk.record.run", include_stream=False)
    finalize_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdk-record-finalize")
    state = STATE_BOOTING
    active_session: RecordingSession | None = None
    stopping_session: StoppingSession | None = None
    stopped_session: StoppedSession | None = None
    registry_started = False
    exit_code = 0
    exit_after_stopping = False
    exit_after_stopping_reason = "ctrl_c"
    exit_after_stopping_code = 0

    try:
        print("=== SDK 交互式录制启动中 ===")
        if config_path is not None:
            print(f"配置文件: {config_path}")
        print(f"数据源: {config.sensor_source}")
        if config.duration_sec > 0:
            message = f"interactive=true 时将忽略 duration_sec={config.duration_sec}"
            print(message)
            run_logger.warning(message)
        _warn_deprecated_trajectory(config, run_logger)

        registry.start_all()
        registry_started = True
        print("等待传感器就绪...")
        while is_running:
            if registry.wait_until_ready(timeout=0.2):
                break
        if not is_running:
            print("录制在传感器就绪前被中断")
            return 1

        compensator, calibration_notes = run_static_calibration(registry, config.gravity_compensation, run_logger)
        startup_discard_notes = {
            "enabled": bool(config.startup_discard_sec > 0),
            "discard_sec": float(max(0.0, config.startup_discard_sec)),
            "discarded_frames": {sensor_name: 0 for sensor_name in registry.sensors},
        }
        if config.startup_discard_sec > 0:
            discard_sec = max(0.0, config.startup_discard_sec)
            print(f"丢弃启动阶段数据: {discard_sec:.1f} 秒")
            run_logger.info("开始丢弃启动阶段数据 %.2f 秒", discard_sec)
            discarded_counts = discard_startup_frames(registry, discard_sec=discard_sec)
            startup_discard_notes["discarded_frames"] = discarded_counts
            run_logger.info("启动阶段数据丢弃完成: %s", json.dumps(discarded_counts, ensure_ascii=False))
            if not is_running:
                print("录制在启动阶段数据丢弃期间被中断")
                return 1

        shared_calibration = _build_shared_calibration_notes(calibration_notes, startup_discard_notes)
        record_run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        session_index = 0
        ft_sensor_name = _find_sensor_name_by_modality(registry, "force_torque")
        imu_sensor_name = _find_sensor_name_by_modality(registry, "imu")
        align_interval_ns = max(1, int(round(1_000_000_000.0 / max(config.align_rate_hz, 1e-6))))
        loop_interval = 1.0 / config.align_rate_hz if config.align_rate_hz > 0 else 0.05

        print("")
        print("所有传感器已就绪，校准完成。")
        print("操作说明: 空格开始/停止，Enter 保存当前 session，q 放弃当前 session，Ctrl+C 退出")
        state = STATE_IDLE_READY

        while True:
            loop_start = time.perf_counter()

            if not is_running:
                is_running = True
                exit_after_stopping = True
                exit_after_stopping_reason = "signal_interrupt"
                exit_after_stopping_code = 0
                if active_session is not None and stopping_session is None:
                    stop_requested_monotonic_time_ns = time.perf_counter_ns()
                    print("\n停止中，正在收尾写盘，请稍候...")
                    key_source.drain_pending_input()
                    stopping_session = _begin_stopping_session(
                        session=active_session,
                        registry=registry,
                        finalize_executor=finalize_executor,
                        stop_reason="signal_interrupt",
                        stop_requested_monotonic_time_ns=stop_requested_monotonic_time_ns,
                        compensator=compensator,
                        ft_sensor_name=ft_sensor_name,
                        imu_sensor_name=imu_sensor_name,
                        align_interval_ns=align_interval_ns,
                    )
                    active_session = None
                    state = STATE_STOPPING
                elif stopped_session is not None:
                    _discard_stopped_session(stopped_session, reason="signal_interrupt")
                    stopped_session = None
                    state = STATE_EXITING
                    exit_code = exit_after_stopping_code
                    print("\n结束本轮采集。")
                    break
                elif stopping_session is None:
                    state = STATE_EXITING
                    exit_code = exit_after_stopping_code
                    print("\n结束本轮采集。")
                    break

            failed_sensors = _find_failed_sensors(registry) if state != STATE_STOPPING else []
            if failed_sensors:
                message = f"检测到传感器停止工作: {', '.join(failed_sensors)}"
                print(message)
                run_logger.error(message)
                if active_session is not None and stopping_session is None:
                    stop_requested_monotonic_time_ns = time.perf_counter_ns()
                    print("\n停止中，正在收尾写盘，请稍候...")
                    key_source.drain_pending_input()
                    stopping_session = _begin_stopping_session(
                        session=active_session,
                        registry=registry,
                        finalize_executor=finalize_executor,
                        stop_reason="sensor_failure",
                        stop_requested_monotonic_time_ns=stop_requested_monotonic_time_ns,
                        compensator=compensator,
                        ft_sensor_name=ft_sensor_name,
                        imu_sensor_name=imu_sensor_name,
                        align_interval_ns=align_interval_ns,
                    )
                    active_session = None
                    state = STATE_STOPPING
                    exit_after_stopping = True
                    exit_after_stopping_reason = "sensor_failure"
                    exit_after_stopping_code = 1
                elif stopped_session is not None:
                    _discard_stopped_session(stopped_session, reason="sensor_failure")
                    stopped_session = None
                    exit_code = 1
                    break
                elif stopping_session is not None:
                    exit_after_stopping = True
                    exit_after_stopping_reason = "sensor_failure"
                    exit_after_stopping_code = 1
                else:
                    exit_code = 1
                    break

            if state == STATE_RECORDING and active_session is not None:
                current_status = _capture_sensor_status_snapshot(registry)
                _update_session_queue_peaks(active_session, current_status)
                last_aligned = _record_iteration(
                    session=active_session,
                    registry=registry,
                    compensator=compensator,
                    ft_sensor_name=ft_sensor_name,
                    imu_sensor_name=imu_sensor_name,
                    align_interval_ns=align_interval_ns,
                )
                _print_align_progress(last_aligned)
            elif state == STATE_STOPPING and stopping_session is not None:
                _drain_sensor_queues(registry)
                if stopping_session.finalize_future.done():
                    stopped_session = stopping_session.finalize_future.result()
                    stopping_session = None
                    state = STATE_REVIEW_STOPPED
                    print("")
                    _print_operator_summary(
                        session_info=stopped_session.session_info,
                        config=config,
                        sensor_status=stopped_session.session_sensor_stats,
                        writer_diagnostics=stopped_session.writer_diagnostics,
                        startup_discard_notes=shared_calibration["startup_discard"],
                    )
                    if exit_after_stopping:
                        _discard_stopped_session(stopped_session, reason=exit_after_stopping_reason)
                        stopped_session = None
                        exit_code = exit_after_stopping_code
                        print("\n结束本轮采集。")
                        break
                    print("session 已停止。按 Enter 保存，按 q 放弃，按 Ctrl+C 退出。")
            else:
                _drain_sensor_queues(registry)

            action = _normalize_key(key_source.poll_key(timeout_sec=0.0))
            if action == KEY_CTRL_C:
                exit_after_stopping = True
                exit_after_stopping_reason = "ctrl_c"
                exit_after_stopping_code = 0
                if active_session is not None and stopping_session is None:
                    stop_requested_monotonic_time_ns = time.perf_counter_ns()
                    print("\n停止中，正在收尾写盘，请稍候...")
                    key_source.drain_pending_input()
                    stopping_session = _begin_stopping_session(
                        session=active_session,
                        registry=registry,
                        finalize_executor=finalize_executor,
                        stop_reason="ctrl_c",
                        stop_requested_monotonic_time_ns=stop_requested_monotonic_time_ns,
                        compensator=compensator,
                        ft_sensor_name=ft_sensor_name,
                        imu_sensor_name=imu_sensor_name,
                        align_interval_ns=align_interval_ns,
                    )
                    active_session = None
                    state = STATE_STOPPING
                elif stopped_session is not None:
                    _discard_stopped_session(stopped_session, reason="ctrl_c")
                    stopped_session = None
                    print("\n结束本轮采集。")
                    exit_code = 0
                    break
                elif stopping_session is None:
                    print("\n结束本轮采集。")
                    exit_code = 0
                    break

            if state == STATE_IDLE_READY:
                if action == KEY_SPACE:
                    session_index += 1
                    active_session = _start_interactive_session(
                        config=config,
                        config_path=config_path,
                        registry=registry,
                        record_run_id=record_run_id,
                        shared_calibration=shared_calibration,
                        session_index=session_index,
                    )
                    state = STATE_RECORDING
                elif action == KEY_ENTER:
                    print("\n当前没有已停止待确认的 session。按空格开始录制。")
                elif action == KEY_QUIT_SESSION:
                    print("\n当前没有可放弃的 session。使用 Ctrl+C 退出。")
            elif state == STATE_RECORDING and active_session is not None:
                if action == KEY_SPACE:
                    stop_requested_monotonic_time_ns = time.perf_counter_ns()
                    print("\n停止中，正在收尾写盘，请稍候...")
                    key_source.drain_pending_input()
                    stopping_session = _begin_stopping_session(
                        session=active_session,
                        registry=registry,
                        finalize_executor=finalize_executor,
                        stop_reason="operator_stop",
                        stop_requested_monotonic_time_ns=stop_requested_monotonic_time_ns,
                        compensator=compensator,
                        ft_sensor_name=ft_sensor_name,
                        imu_sensor_name=imu_sensor_name,
                        align_interval_ns=align_interval_ns,
                    )
                    active_session = None
                    state = STATE_STOPPING
                elif action == KEY_ENTER:
                    print("\n录制尚未停止，请先按空格停止当前 session。")
                elif action == KEY_QUIT_SESSION:
                    print("\n录制尚未停止，请先按空格停止当前 session。")
            elif state == STATE_STOPPING and stopping_session is not None:
                if action in {KEY_SPACE, KEY_ENTER, KEY_QUIT_SESSION}:
                    continue
            elif state == STATE_REVIEW_STOPPED and stopped_session is not None:
                if action == KEY_ENTER:
                    print(f"已保存 session #{stopped_session.session_index}: {stopped_session.session_info.output_dir}")
                    stopped_session = None
                    state = STATE_IDLE_READY
                    print("按空格开始下一个 session，按 Ctrl+C 结束。")
                elif action == KEY_QUIT_SESSION:
                    _discard_stopped_session(stopped_session, reason="operator_discard")
                    stopped_session = None
                    state = STATE_IDLE_READY
                    print("已回到待机状态。按空格开始下一个 session，按 Ctrl+C 结束。")
                elif action == KEY_SPACE:
                    print("\n当前 session 已停止，请先按 Enter 保存或按 q 放弃。")

            elapsed = time.perf_counter() - loop_start
            time.sleep(max(0.0, loop_interval - elapsed))
    finally:
        if stopping_session is not None:
            stopping_session.finalize_future.result()
        finalize_executor.shutdown(wait=True)
        if owned_key_source:
            key_source.close()
        if registry_started:
            print("\n正在停止传感器...")
            registry.stop_all()
    print("完成。")
    return exit_code


def main(argv: list[str] | None = None, *, key_source: KeySource | None = None) -> int:
    global is_running
    is_running = True
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    config, config_path = parse_args(argv)
    if config.interactive:
        try:
            return _run_interactive(config=config, config_path=config_path, key_source=key_source)
        except RuntimeError as exc:
            print(f"交互式录制启动失败: {exc}")
            return 1
    return _run_noninteractive(config=config, config_path=config_path)


if __name__ == "__main__":
    sys.exit(main())
