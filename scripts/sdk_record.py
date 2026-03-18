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
from sdk.processors import GravityCompensationConfig, GravityCompensator
from sdk.sensors.fake import (
    FakeCameraAdapter,
    FakeCameraConfig,
    FakeFTAdapter,
    FakeFTSensorConfig,
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
    IMUSensorConfig,
    LegacyFTAdapter,
    LegacyIMUAdapter,
    LegacyCameraAdapter,
    LegacyMicrophoneAdapter,
    LegacyMotorsAdapter,
    MicrophoneSensorConfig,
    MotorsSensorConfig,
)
from sdk.sensors.realsense import RealSenseConfig, RealSenseRGBDAdapter
from sdk.storage import SessionWriter


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
    ft: FTSensorConfig = FTSensorConfig()
    imu: IMUSensorConfig = IMUSensorConfig()
    realsense: RealSenseConfig = RealSenseConfig()
    motors: MotorsSensorConfig = MotorsSensorConfig()
    microphone: MicrophoneSensorConfig = MicrophoneSensorConfig()
    camera: CameraSensorConfig = CameraSensorConfig()
    gravity_compensation: GravityCompensationConfig = GravityCompensationConfig()


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


def parse_args() -> RecorderConfig:
    parser = argparse.ArgumentParser(description="多模态 SDK 录制入口")
    parser.add_argument("--config", default=None)
    parser.add_argument("--output-root", default="sessions")
    parser.add_argument("--sensor-source", choices=["real", "fake"], default="real")
    parser.add_argument("--align-rate", type=float, default=30.0)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--max-frame-age", type=float, default=0.2)
    parser.add_argument("--disable-ft", action="store_true")
    parser.add_argument("--disable-imu", action="store_true")
    parser.add_argument("--disable-realsense", action="store_true")
    parser.add_argument("--enable-motors", action="store_true")
    parser.add_argument("--enable-microphone", action="store_true")
    parser.add_argument("--enable-camera", action="store_true")
    parser.add_argument("--ft-port", default="COM3")
    parser.add_argument("--imu-port", default="COM4")
    parser.add_argument("--motors-port", default="/dev/ttyUSB0")
    parser.add_argument("--microphone-device-index", type=int, default=None)
    parser.add_argument("--microphone-channels", type=int, default=1)
    parser.add_argument("--microphone-rate", type=int, default=44100)
    parser.add_argument("--microphone-chunk", type=int, default=1024)
    parser.add_argument("--camera-device-index", type=int, default=0)
    parser.add_argument("--camera-width", type=int, default=640)
    parser.add_argument("--camera-height", type=int, default=480)
    parser.add_argument("--camera-fps", type=int, default=30)
    parser.add_argument("--realsense-width", type=int, default=640)
    parser.add_argument("--realsense-height", type=int, default=480)
    parser.add_argument("--realsense-fps", type=int, default=30)
    args = parser.parse_args()

    payload = {
        "output_root": args.output_root,
        "sensor_source": args.sensor_source,
        "align_rate_hz": args.align_rate,
        "duration_sec": args.duration,
        "max_frame_age": args.max_frame_age,
        "enable_ft": not args.disable_ft,
        "enable_imu": not args.disable_imu,
        "enable_realsense": not args.disable_realsense,
        "enable_motors": args.enable_motors,
        "enable_microphone": args.enable_microphone,
        "enable_camera": args.enable_camera,
        "ft": {"port": args.ft_port},
        "imu": {"port": args.imu_port},
        "motors": {"port": args.motors_port},
        "microphone": {
            "device_index": args.microphone_device_index,
            "channels": args.microphone_channels,
            "rate": args.microphone_rate,
            "chunk": args.microphone_chunk,
        },
        "camera": {
            "device_index": args.camera_device_index,
            "width": args.camera_width,
            "height": args.camera_height,
            "fps": args.camera_fps,
        },
        "realsense": {
            "width": args.realsense_width,
            "height": args.realsense_height,
            "fps": args.realsense_fps,
        },
        "gravity_compensation": {
            "enabled": False,
            "mass": 0.25,
            "com": [0.0, 0.0, 0.0],
            "stabilization_sec": 2.0,
            "calibration_duration_sec": 3.0,
            "minimum_samples": 10,
        },
    }
    if args.config:
        payload = deep_merge(payload, load_config_file(args.config))

    return RecorderConfig(
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
        ft=FTSensorConfig(**payload.get("ft", {})),
        imu=IMUSensorConfig(**payload.get("imu", {})),
        realsense=RealSenseConfig(**payload.get("realsense", {})),
        motors=MotorsSensorConfig(**payload.get("motors", {})),
        microphone=MicrophoneSensorConfig(**payload.get("microphone", {})),
        camera=CameraSensorConfig(**payload.get("camera", {})),
        gravity_compensation=GravityCompensationConfig(**payload.get("gravity_compensation", {})),
    )


def main() -> None:
    global is_running
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    config = parse_args()
    clock = SystemClock()
    registry = build_registry(config, clock)

    print("=== SDK 录制启动中 ===")
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
        notes={"entrypoint": "scripts/sdk_record.py"},
    )
    logger = build_logger("sdk.record", log_file=session_info.output_dir / "logs" / "sdk_record.log")
    logger.info("SDK 录制启动，数据源=%s", config.sensor_source)
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
            aligned_time = time.time()

            for sensor_name, sensor in registry.sensors.items():
                frame = sensor.read_frame()
                if frame is None:
                    continue
                if frame.frame_id == last_written_frame_ids[sensor_name]:
                    continue

                aligner.add_frame(frame)
                writer.write_sensor_frame(frame)
                last_written_frame_ids[sensor_name] = frame.frame_id

            aligned = aligner.align(aligned_time)
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
        print("完成。")


if __name__ == "__main__":
    main()
