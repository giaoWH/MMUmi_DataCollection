from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.core import SensorRegistry, SystemClock
from sdk.core.frame import SensorFrame
from sdk.processors import GravityCompensator
from sdk.sensors.legacy import (
    FTSensorConfig,
    IMUSensorConfig,
    LegacyFTAdapter,
    LegacyIMUAdapter,
)
from scripts import sdk_record


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FT + IMU 重力补偿调试：打印原始 FT/IMU 数据和补偿后的 force。",
    )
    parser.add_argument("--config", help="录制配置文件路径，默认沿用 configs/record.yaml")
    parser.add_argument("--ft-port", help="覆盖配置中的 FT 串口")
    parser.add_argument("--imu-port", help="覆盖配置中的 IMU 串口")
    parser.add_argument("--ft-baudrate", type=int, help="覆盖配置中的 FT baudrate")
    parser.add_argument("--imu-baudrate", type=int, help="覆盖配置中的 IMU baudrate")
    parser.add_argument("--mass", type=float, help="末端负载质量 kg；默认读取 gravity_compensation.mass")
    parser.add_argument(
        "--com",
        type=float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        help="末端质心位置 m；默认读取 gravity_compensation.com",
    )
    parser.add_argument(
        "--stabilization-sec",
        type=float,
        help="开始静态 bias 校准前等待时间；默认读取 gravity_compensation.stabilization_sec",
    )
    parser.add_argument(
        "--calibration-duration-sec",
        type=float,
        help="静态 bias 校准采样时长；默认读取 gravity_compensation.calibration_duration_sec",
    )
    parser.add_argument(
        "--minimum-samples",
        type=int,
        help="静态 bias 校准最少 FT/IMU 配对样本数；默认读取 gravity_compensation.minimum_samples",
    )
    parser.add_argument("--rate-hz", type=float, default=10.0, help="打印频率，默认 10 Hz")
    parser.add_argument("--duration-sec", type=float, default=0.0, help="打印时长；0 表示持续到 Ctrl+C")
    parser.add_argument(
        "--use-ft-zero-calibration",
        action="store_true",
        help="使用 FT 传感器自身零点校准；默认跳过，以便输出原始 FT 数据",
    )
    parser.add_argument("--jsonl", action="store_true", help="每帧输出一行 JSON，便于保存和后处理")
    return parser.parse_args(argv)


def _load_recorder_config(config_path: str | None) -> tuple[sdk_record.RecorderConfig, Path | None]:
    argv = ["--config", config_path] if config_path else []
    return sdk_record.parse_args(argv)


def _build_registry(
    *,
    ft_config: FTSensorConfig,
    imu_config: IMUSensorConfig,
    clock: SystemClock,
) -> SensorRegistry:
    registry = SensorRegistry()
    registry.register(LegacyFTAdapter(ft_config, clock))
    registry.register(LegacyIMUAdapter(imu_config, clock))
    return registry


def _drain_latest_frame(sensor) -> SensorFrame | None:
    frames = sensor.read_available_frames()
    if frames:
        return frames[-1]
    return sensor.read_frame()


def _collect_static_samples(
    *,
    registry: SensorRegistry,
    ft_name: str,
    imu_name: str,
    duration_sec: float,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    ft_sensor = registry.sensors[ft_name]
    imu_sensor = registry.sensors[imu_name]
    ft_samples: list[np.ndarray] = []
    quat_samples: list[np.ndarray] = []
    latest_ft: SensorFrame | None = None
    latest_imu: SensorFrame | None = None
    last_pair: tuple[int, int] | None = None
    deadline = time.perf_counter() + max(0.0, duration_sec)

    while time.perf_counter() < deadline:
        next_ft = _drain_latest_frame(ft_sensor)
        next_imu = _drain_latest_frame(imu_sensor)
        if next_ft is not None:
            latest_ft = next_ft
        if next_imu is not None:
            latest_imu = next_imu
        if latest_ft is not None and latest_imu is not None:
            pair = (latest_ft.frame_id, latest_imu.frame_id)
            if pair != last_pair:
                ft_samples.append(np.asarray(latest_ft.payload["force"], dtype=np.float64))
                quat_samples.append(np.asarray(latest_imu.payload["quaternion"], dtype=np.float64))
                last_pair = pair
        time.sleep(0.002)

    return ft_samples, quat_samples


def _vec(value: object, *, length: int | None = None) -> np.ndarray:
    array = np.asarray(value if value is not None else [], dtype=np.float64).reshape(-1)
    if length is not None and array.size < length:
        padded = np.zeros(length, dtype=np.float64)
        padded[: array.size] = array
        return padded
    return array


def _format_vec(value: np.ndarray, digits: int = 4) -> str:
    return "[" + ", ".join(f"{item:.{digits}f}" for item in value.tolist()) + "]"


def _print_record(
    *,
    ft_frame: SensorFrame,
    imu_frame: SensorFrame,
    pure_force: np.ndarray,
    gravity_force: np.ndarray,
    bias: np.ndarray,
    jsonl: bool,
) -> None:
    force_raw = _vec(ft_frame.payload.get("force"), length=3)
    torque_raw = _vec(ft_frame.payload.get("torque"), length=3)
    acceleration = _vec(imu_frame.payload.get("acceleration"), length=3)
    angular_velocity = _vec(imu_frame.payload.get("angular_velocity"), length=3)
    quaternion = _vec(imu_frame.payload.get("quaternion"), length=4)

    if jsonl:
        print(
            json.dumps(
                {
                    "host_time": time.time(),
                    "ft": {
                        "frame_id": ft_frame.frame_id,
                        "force_raw": force_raw.tolist(),
                        "torque_raw": torque_raw.tolist(),
                    },
                    "imu": {
                        "frame_id": imu_frame.frame_id,
                        "acceleration": acceleration.tolist(),
                        "angular_velocity": angular_velocity.tolist(),
                        "quaternion": quaternion.tolist(),
                    },
                    "gravity_compensation": {
                        "pure_force": pure_force.tolist(),
                        "gravity_force": gravity_force.tolist(),
                        "bias": bias.tolist(),
                    },
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return

    print(
        " | ".join(
            [
                f"t={time.strftime('%H:%M:%S')}",
                f"FT#{ft_frame.frame_id} Fraw={_format_vec(force_raw)} N",
                f"Traw={_format_vec(torque_raw)} Nm",
                f"IMU#{imu_frame.frame_id} acc={_format_vec(acceleration)}",
                f"gyro={_format_vec(angular_velocity)}",
                f"quat={_format_vec(quaternion)}",
                f"G={_format_vec(gravity_force)} N",
                f"bias={_format_vec(bias)} N",
                f"Fpure={_format_vec(pure_force)} N",
            ]
        ),
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    record_config, loaded_path = _load_recorder_config(args.config)
    gravity_config = record_config.gravity_compensation

    ft_config = replace(
        record_config.ft,
        port=args.ft_port or record_config.ft.port,
        baudrate=args.ft_baudrate or record_config.ft.baudrate,
        enable_torque=True,
        skip_calibration=not args.use_ft_zero_calibration,
    )
    imu_config = replace(
        record_config.imu,
        port=args.imu_port or record_config.imu.port,
        baudrate=args.imu_baudrate or record_config.imu.baudrate,
    )
    mass = float(args.mass if args.mass is not None else gravity_config.mass)
    com = list(args.com if args.com is not None else gravity_config.com)
    stabilization_sec = float(
        args.stabilization_sec
        if args.stabilization_sec is not None
        else gravity_config.stabilization_sec
    )
    calibration_duration_sec = float(
        args.calibration_duration_sec
        if args.calibration_duration_sec is not None
        else gravity_config.calibration_duration_sec
    )
    minimum_samples = int(
        args.minimum_samples
        if args.minimum_samples is not None
        else gravity_config.minimum_samples
    )

    clock = SystemClock()
    registry = _build_registry(ft_config=ft_config, imu_config=imu_config, clock=clock)
    compensator = GravityCompensator(mass=mass, com_pos=com)

    print("=== FT + IMU 重力补偿调试 ===")
    if loaded_path is not None:
        print(f"配置文件: {loaded_path}")
    print(f"FT: port={ft_config.port}, baudrate={ft_config.baudrate}, skip_calibration={ft_config.skip_calibration}")
    print(f"IMU: port={imu_config.port}, baudrate={imu_config.baudrate}")
    print(f"Gravity: mass={mass}, com={com}")
    print("说明: Fraw/Traw 为 FT 输入数据；Fpure 为 GravityCompensator 输出的补偿后 force，torque 当前不做重力补偿。")

    try:
        registry.start_all()
        print("等待 FT/IMU 就绪...")
        registry.wait_until_ready(timeout=None)

        if stabilization_sec > 0:
            print(f"等待传感器稳定 {stabilization_sec:.2f} 秒...")
            time.sleep(stabilization_sec)

        print(f"开始静态 bias 校准 {calibration_duration_sec:.2f} 秒，请保持末端静止...")
        ft_samples, quat_samples = _collect_static_samples(
            registry=registry,
            ft_name=ft_config.name,
            imu_name=imu_config.name,
            duration_sec=calibration_duration_sec,
        )
        required_samples = max(minimum_samples, 10)
        if len(ft_samples) < required_samples:
            print(f"静态校准样本不足: {len(ft_samples)} < {required_samples}")
            return 1
        if not compensator.calibrate_bias(ft_samples, quat_samples):
            print("静态 bias 校准失败")
            return 1
        print(f"静态校准完成: samples={len(ft_samples)}, bias={_format_vec(compensator.bias)} N")
        print("开始输出数据，按 Ctrl+C 停止。")

        loop_interval = 1.0 / max(float(args.rate_hz), 1e-6)
        deadline = (
            time.perf_counter() + float(args.duration_sec)
            if args.duration_sec > 0
            else None
        )
        ft_sensor = registry.sensors[ft_config.name]
        imu_sensor = registry.sensors[imu_config.name]
        latest_ft: SensorFrame | None = None
        latest_imu: SensorFrame | None = None
        last_printed_pair: tuple[int, int] | None = None

        while True:
            loop_start = time.perf_counter()
            if deadline is not None and loop_start >= deadline:
                break

            next_ft = _drain_latest_frame(ft_sensor)
            next_imu = _drain_latest_frame(imu_sensor)
            if next_ft is not None:
                latest_ft = next_ft
            if next_imu is not None:
                latest_imu = next_imu

            if latest_ft is not None and latest_imu is not None:
                pair = (latest_ft.frame_id, latest_imu.frame_id)
                if pair != last_printed_pair:
                    force_raw = _vec(latest_ft.payload.get("force"), length=3)
                    quaternion = _vec(latest_imu.payload.get("quaternion"), length=4)
                    pure_force, gravity_force = compensator.process(force_raw, quaternion)
                    _print_record(
                        ft_frame=latest_ft,
                        imu_frame=latest_imu,
                        pure_force=pure_force,
                        gravity_force=gravity_force,
                        bias=compensator.bias,
                        jsonl=bool(args.jsonl),
                    )
                    last_printed_pair = pair

            elapsed = time.perf_counter() - loop_start
            time.sleep(max(0.0, loop_interval - elapsed))
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，停止调试。")
    finally:
        registry.stop_all()

    return 0


if __name__ == "__main__":
    sys.exit(main())
