from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.config import deep_merge, load_config_file
from sdk.core import BufferedFrameAligner, SensorRegistry, SystemClock, create_session_info
from sdk.logging import build_logger
from sdk.perception import OrbSlam3SessionProcessConfig, process_orbslam3_session
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


@dataclass(frozen=True)
class RecorderConfig:
    output_root: str = "sessions"
    sensor_source: str = "real"
    align_rate_hz: float = 30.0
    duration_sec: float = 0.0
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


is_running = True


def _handle_signal(_sig: int, _frame: object) -> None:
    global is_running
    is_running = False


def build_registry(config: RecorderConfig, clock: SystemClock) -> SensorRegistry:
    registry = SensorRegistry()
    if config.sensor_source == "fake":
        if config.enable_ft:
            registry.register(FakeFTAdapter(FakeFTSensorConfig(name=config.ft.name), clock))
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
        if config.enable_motors:
            registry.register(
                FakeMotorsAdapter(
                    FakeMotorsConfig(
                        name=config.motors.name,
                        sample_rate_hz=100.0,
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
    if config.enable_motors:
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

    config = RecorderConfig(
        output_root=payload["output_root"],
        sensor_source=payload.get("sensor_source", "real"),
        align_rate_hz=payload["align_rate_hz"],
        duration_sec=payload["duration_sec"],
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
        gelsight=GelSightSensorConfig(**payload.get("gelsight", {})),
        gravity_compensation=GravityCompensationConfig(**payload.get("gravity_compensation", {})),
        trajectory=OrbSlam3SessionProcessConfig(**payload.get("trajectory", {})),
    )
    return config, config_path


def main() -> None:
    global is_running
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    config, config_path = parse_args()
    if config.enable_trajectory and not config.trajectory.command:
        raise ValueError("enable_trajectory=true 时必须在 trajectory.command 中提供 ORB-SLAM3 命令")
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
        return
    session_info = create_session_info(
        config.output_root,
        sensors=registry.get_metadata(),
        config=asdict(config),
        notes={
            "entrypoint": "scripts/sdk_record.py",
            "config_file": str(config_path) if config_path is not None else None,
        },
    )
    logger = build_logger("sdk.record", log_file=session_info.output_dir / "logs" / "sdk_record.log")
    logger.info("SDK 录制启动，数据源=%s", config.sensor_source)
    if config_path is not None:
        logger.info("加载配置文件: %s", config_path)
    compensator, calibration_notes = run_static_calibration(registry, config.gravity_compensation, logger)
    session_info.notes["gravity_compensation"] = calibration_notes
    writer = SessionWriter(session_info)
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
        print(f"录制时长: {config.duration_sec:.1f} 秒")
    else:
        print("录制时长: 持续运行，按 Ctrl+C 停止")

    loop_interval = 1.0 / config.align_rate_hz
    start_time = time.time()

    try:
        while is_running:
            if config.duration_sec > 0 and (time.time() - start_time) >= config.duration_sec:
                break

            loop_start = time.perf_counter()
            aligned_time_ns = time.time_ns()
            aligned_monotonic_time_ns = time.perf_counter_ns()
            aligned_time = aligned_time_ns / 1_000_000_000.0

            for sensor_name, sensor in registry.sensors.items():
                frame = sensor.read_frame()
                if frame is None:
                    continue
                if frame.frame_id == last_written_frame_ids[sensor_name]:
                    continue

                aligner.add_frame(frame)
                writer.write_sensor_frame(frame)
                last_written_frame_ids[sensor_name] = frame.frame_id

            aligned = aligner.align(
                aligned_time,
                aligned_time_ns=aligned_time_ns,
                aligned_monotonic_time_ns=aligned_monotonic_time_ns,
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

            missing = ",".join(aligned.missing_sensors) if aligned.missing_sensors else "-"
            print(
                f"\r[Align] seq={aligned.sequence_id:06d} "
                f"missing={missing:<20} "
                f"frames={len(aligned.frames)}",
                end="",
            )

            elapsed = time.perf_counter() - loop_start
            time.sleep(max(0.0, loop_interval - elapsed))
    finally:
        print("\n正在停止传感器...")
        logger.info("正在停止传感器")
        registry.stop_all()
        writer.close()
        logger.info("录制结束")

    if config.enable_trajectory:
        print("开始轨迹解算...")
        logger.info("开始录制后轨迹解算，mode=%s", config.trajectory.mode)
        try:
            result = process_orbslam3_session(session_info.output_dir, config.trajectory)
        except Exception:
            logger.exception("录制后轨迹解算失败")
            print(f"轨迹解算失败，session 已保存在: {session_info.output_dir}")
            raise
        logger.info(
            "录制后轨迹解算完成，bundle=%s, frames=%d",
            result.bundle_dir,
            result.trajectory_frames,
        )
        print(f"轨迹写入完成: {session_info.output_dir / 'trajectory' / 'frames.jsonl'}")

    print("完成。")


if __name__ == "__main__":
    main()
