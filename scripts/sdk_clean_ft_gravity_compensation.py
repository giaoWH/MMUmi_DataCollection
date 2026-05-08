from __future__ import annotations

import argparse
from bisect import bisect_left
from dataclasses import dataclass
import json
import sys
from pathlib import Path
from typing import Iterable

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.core.frame import SensorFrame
from sdk.processors import GravityCompensator
from sdk.storage import SessionReader, discover_session_dirs
from sdk.storage.simplified_stream import (
    DEFAULT_SIMPLIFIED_JSONL_TIMEZONE,
    format_simplified_host_time,
)


@dataclass(frozen=True)
class MatchedFTIMUFrame:
    ft_frame: SensorFrame
    imu_frame: SensorFrame
    ft_time_ns: int
    imu_time_ns: int
    match_age_ns: int


@dataclass(frozen=True)
class CleanSummary:
    session_dir: Path
    output_path: Path
    ft_sensor: str
    imu_sensor: str
    time_domain: str
    input_ft_frames: int
    input_imu_frames: int
    matched_frames: int
    written_frames: int
    calibration_samples: int
    mass: float
    bias: list[float]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "离线清洗已有 session 的 FT 数据：用已有 IMU 姿态做重力补偿，"
            "在 streams/ft 下生成独立的补偿后 JSONL。"
        )
    )
    parser.add_argument(
        "path",
        help="单个 session 目录，或包含多个 session 的根目录",
    )
    parser.add_argument(
        "--mass",
        type=float,
        required=True,
        help="独立指定的末端负载质量 kg，不读取 record.yaml 中的 mass",
    )
    parser.add_argument(
        "--com",
        type=float,
        nargs=3,
        default=[0.0, 0.0, 0.0],
        metavar=("X", "Y", "Z"),
        help="末端质心位置 m；当前补偿输出只使用 force，默认 0 0 0",
    )
    parser.add_argument(
        "--calibration-start-sec",
        type=float,
        default=0.0,
        help="从 session 首个匹配 FT 帧起算，静态 bias 校准窗口的起点，默认 0",
    )
    parser.add_argument(
        "--calibration-duration-sec",
        type=float,
        default=3.0,
        help="静态 bias 校准窗口时长，默认 3 秒；请确保这段时间末端静止",
    )
    parser.add_argument(
        "--minimum-samples",
        type=int,
        default=10,
        help="bias 校准最少样本数，内部至少要求 10，默认 10",
    )
    parser.add_argument(
        "--max-match-age-sec",
        type=float,
        default=0.2,
        help="FT 帧匹配最近 IMU 帧允许的最大时间差，默认 0.2 秒",
    )
    parser.add_argument(
        "--ft-sensor",
        default=None,
        help="FT sensor 名称；默认自动寻找 modality=force_torque",
    )
    parser.add_argument(
        "--imu-sensor",
        default=None,
        help="IMU sensor 名称；默认自动寻找 modality=imu",
    )
    parser.add_argument(
        "--output-suffix",
        default="_gravity_compensated_s",
        help="输出文件后缀，默认 _gravity_compensated_s",
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_SIMPLIFIED_JSONL_TIMEZONE,
        help=f"host_time 字符串时区，默认 {DEFAULT_SIMPLIFIED_JSONL_TIMEZONE}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖已存在的输出 JSONL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查和打印摘要，不写文件",
    )
    return parser.parse_args(argv)


def _find_sensor_by_modality(reader: SessionReader, modality: str) -> str | None:
    for sensor_name, stream in reader.manifest.sensors.items():
        if stream.modality == modality:
            return sensor_name
    return None


def _time_ns(frame: SensorFrame, domain: str) -> int | None:
    if domain == "monotonic_time_ns":
        return frame.time.monotonic_time_ns
    if domain == "host_time_ns":
        return frame.time.host_time_ns
    if domain == "device_time_ns":
        return frame.time.device_time_ns
    raise ValueError(f"不支持的时间域: {domain}")


def _choose_time_domain(ft_frames: list[SensorFrame], imu_frames: list[SensorFrame]) -> str:
    for domain in ("monotonic_time_ns", "host_time_ns", "device_time_ns"):
        if any(_time_ns(frame, domain) is not None for frame in ft_frames) and any(
            _time_ns(frame, domain) is not None for frame in imu_frames
        ):
            return domain
    raise RuntimeError("FT 和 IMU 没有可共同使用的时间戳字段")


def _as_vector(payload_value: object, *, length: int) -> np.ndarray:
    array = np.asarray(payload_value if payload_value is not None else [], dtype=np.float64).reshape(-1)
    if array.size < length:
        raise RuntimeError(f"payload 数值长度不足: {array.size} < {length}")
    return array[:length]


def _ft_force(frame: SensorFrame) -> np.ndarray:
    if "force" in frame.payload:
        return _as_vector(frame.payload.get("force"), length=3)
    if "force_torque" in frame.payload:
        return _as_vector(frame.payload.get("force_torque"), length=6)[:3]
    raise RuntimeError(f"FT frame 缺少 force/force_torque: frame_id={frame.frame_id}")


def _ft_torque(frame: SensorFrame) -> np.ndarray | None:
    if "torque" in frame.payload:
        return _as_vector(frame.payload.get("torque"), length=3)
    if "force_torque" in frame.payload:
        return _as_vector(frame.payload.get("force_torque"), length=6)[3:6]
    return None


def _match_frames(
    ft_frames: list[SensorFrame],
    imu_frames: list[SensorFrame],
    *,
    time_domain: str,
    max_match_age_ns: int,
) -> list[MatchedFTIMUFrame]:
    indexed_imu: list[tuple[int, SensorFrame]] = []
    for imu_frame in imu_frames:
        timestamp_ns = _time_ns(imu_frame, time_domain)
        if timestamp_ns is not None:
            indexed_imu.append((int(timestamp_ns), imu_frame))
    indexed_imu.sort(key=lambda item: item[0])
    imu_times = [item[0] for item in indexed_imu]
    matches: list[MatchedFTIMUFrame] = []

    for ft_frame in ft_frames:
        ft_time_ns = _time_ns(ft_frame, time_domain)
        if ft_time_ns is None:
            continue
        position = bisect_left(imu_times, int(ft_time_ns))
        candidate_indices: list[int] = []
        if position < len(imu_times):
            candidate_indices.append(position)
        if position > 0:
            candidate_indices.append(position - 1)
        if not candidate_indices:
            continue
        best_index = min(candidate_indices, key=lambda index: abs(imu_times[index] - int(ft_time_ns)))
        imu_time_ns, imu_frame = indexed_imu[best_index]
        match_age_ns = int(imu_time_ns) - int(ft_time_ns)
        if abs(match_age_ns) > max_match_age_ns:
            continue
        matches.append(
            MatchedFTIMUFrame(
                ft_frame=ft_frame,
                imu_frame=imu_frame,
                ft_time_ns=int(ft_time_ns),
                imu_time_ns=int(imu_time_ns),
                match_age_ns=match_age_ns,
            )
        )
    return matches


def _calibration_matches(
    matches: list[MatchedFTIMUFrame],
    *,
    calibration_start_sec: float,
    calibration_duration_sec: float,
) -> list[MatchedFTIMUFrame]:
    if not matches:
        return []
    first_time_ns = matches[0].ft_time_ns
    start_ns = first_time_ns + int(round(max(0.0, calibration_start_sec) * 1_000_000_000.0))
    end_ns = start_ns + int(round(max(0.0, calibration_duration_sec) * 1_000_000_000.0))
    return [match for match in matches if start_ns <= match.ft_time_ns <= end_ns]


def _output_path_for_session(
    reader: SessionReader,
    *,
    session_dir: Path,
    ft_sensor: str,
    output_suffix: str,
) -> Path:
    stream = reader.manifest.sensors[ft_sensor]
    frames_path = session_dir / stream.frames_path
    return frames_path.with_name(f"{frames_path.stem}{output_suffix}.jsonl")


def _build_output_record(
    match: MatchedFTIMUFrame,
    *,
    pure_force: np.ndarray,
    gravity_force: np.ndarray,
    bias: np.ndarray,
    mass: float,
    com: list[float],
    timezone: str,
) -> dict[str, object]:
    ft_frame = match.ft_frame
    raw_force = _ft_force(ft_frame)
    raw_torque = _ft_torque(ft_frame)
    payload: dict[str, object] = {"force": pure_force.tolist()}
    if raw_torque is not None:
        payload["torque"] = raw_torque.tolist()

    return {
        "host_time": format_simplified_host_time(ft_frame.time.host_time, tz_name=timezone),
        "payload": payload,
        "gravity_compensation": {
            "applied": True,
            "source": "offline_clean_ft_gravity_compensation",
            "mass": float(mass),
            "com": [float(value) for value in com],
            "force_raw": raw_force.tolist(),
            "gravity_force": gravity_force.tolist(),
            "bias": bias.tolist(),
            "source_ft_frame_id": int(ft_frame.frame_id),
            "matched_imu_frame_id": int(match.imu_frame.frame_id),
            "match_age_sec": match.match_age_ns / 1_000_000_000.0,
        },
    }


def clean_session(
    session_dir: str | Path,
    *,
    mass: float,
    com: Iterable[float] = (0.0, 0.0, 0.0),
    calibration_start_sec: float = 0.0,
    calibration_duration_sec: float = 3.0,
    minimum_samples: int = 10,
    max_match_age_sec: float = 0.2,
    ft_sensor: str | None = None,
    imu_sensor: str | None = None,
    output_suffix: str = "_gravity_compensated_s",
    timezone: str = DEFAULT_SIMPLIFIED_JSONL_TIMEZONE,
    overwrite: bool = False,
    dry_run: bool = False,
) -> CleanSummary:
    session_dir = Path(session_dir)
    reader = SessionReader(session_dir)
    resolved_ft_sensor = ft_sensor or _find_sensor_by_modality(reader, "force_torque")
    resolved_imu_sensor = imu_sensor or _find_sensor_by_modality(reader, "imu")
    if resolved_ft_sensor is None:
        raise RuntimeError(f"未找到 FT sensor: {session_dir}")
    if resolved_imu_sensor is None:
        raise RuntimeError(f"未找到 IMU sensor: {session_dir}")

    ft_frames = list(reader.iter_sensor_frames(resolved_ft_sensor, load_payload=False))
    imu_frames = list(reader.iter_sensor_frames(resolved_imu_sensor, load_payload=False))
    if not ft_frames:
        raise RuntimeError(f"FT stream 为空: {session_dir}")
    if not imu_frames:
        raise RuntimeError(f"IMU stream 为空: {session_dir}")

    time_domain = _choose_time_domain(ft_frames, imu_frames)
    max_match_age_ns = int(round(max(0.0, max_match_age_sec) * 1_000_000_000.0))
    matches = _match_frames(
        ft_frames,
        imu_frames,
        time_domain=time_domain,
        max_match_age_ns=max_match_age_ns,
    )
    if not matches:
        raise RuntimeError(f"FT/IMU 没有可用匹配帧: {session_dir}")

    calibration = _calibration_matches(
        matches,
        calibration_start_sec=calibration_start_sec,
        calibration_duration_sec=calibration_duration_sec,
    )
    required_samples = max(10, int(minimum_samples))
    if len(calibration) < required_samples:
        raise RuntimeError(
            f"静态校准样本不足: {len(calibration)} < {required_samples} ({session_dir})"
        )

    com_values = [float(value) for value in com]
    compensator = GravityCompensator(mass=float(mass), com_pos=com_values)
    ft_samples = [
        _ft_force(match.ft_frame)
        for match in calibration
    ]
    quat_samples = [
        _as_vector(match.imu_frame.payload.get("quaternion"), length=4)
        for match in calibration
    ]
    if not compensator.calibrate_bias(ft_samples, quat_samples):
        raise RuntimeError(f"重力补偿 bias 校准失败: {session_dir}")

    output_path = _output_path_for_session(
        reader,
        session_dir=session_dir,
        ft_sensor=resolved_ft_sensor,
        output_suffix=output_suffix,
    )
    if output_path.exists() and not overwrite and not dry_run:
        raise FileExistsError(f"输出文件已存在，使用 --overwrite 覆盖: {output_path}")

    written_frames = 0
    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            for match in matches:
                raw_force = _ft_force(match.ft_frame)
                quaternion = _as_vector(match.imu_frame.payload.get("quaternion"), length=4)
                pure_force, gravity_force = compensator.process(raw_force, quaternion)
                record = _build_output_record(
                    match,
                    pure_force=pure_force,
                    gravity_force=gravity_force,
                    bias=compensator.bias,
                    mass=float(mass),
                    com=com_values,
                    timezone=timezone,
                )
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                written_frames += 1
    else:
        written_frames = len(matches)

    return CleanSummary(
        session_dir=session_dir,
        output_path=output_path,
        ft_sensor=resolved_ft_sensor,
        imu_sensor=resolved_imu_sensor,
        time_domain=time_domain,
        input_ft_frames=len(ft_frames),
        input_imu_frames=len(imu_frames),
        matched_frames=len(matches),
        written_frames=written_frames,
        calibration_samples=len(calibration),
        mass=float(mass),
        bias=compensator.bias.tolist(),
    )


def _summary_to_jsonable(summary: CleanSummary) -> dict[str, object]:
    return {
        "session_dir": str(summary.session_dir),
        "output_path": str(summary.output_path),
        "ft_sensor": summary.ft_sensor,
        "imu_sensor": summary.imu_sensor,
        "time_domain": summary.time_domain,
        "input_ft_frames": summary.input_ft_frames,
        "input_imu_frames": summary.input_imu_frames,
        "matched_frames": summary.matched_frames,
        "written_frames": summary.written_frames,
        "calibration_samples": summary.calibration_samples,
        "mass": summary.mass,
        "bias": summary.bias,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    session_dirs = discover_session_dirs(args.path)
    if not session_dirs:
        print(f"未找到 session: {args.path}", file=sys.stderr)
        return 1

    summaries: list[CleanSummary] = []
    errors: list[str] = []
    for session_dir in session_dirs:
        try:
            summary = clean_session(
                session_dir,
                mass=args.mass,
                com=args.com,
                calibration_start_sec=args.calibration_start_sec,
                calibration_duration_sec=args.calibration_duration_sec,
                minimum_samples=args.minimum_samples,
                max_match_age_sec=args.max_match_age_sec,
                ft_sensor=args.ft_sensor,
                imu_sensor=args.imu_sensor,
                output_suffix=args.output_suffix,
                timezone=args.timezone,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
            summaries.append(summary)
            status = "DRY-RUN" if args.dry_run else "OK"
            print(
                f"[{status}] {summary.session_dir} -> {summary.output_path} "
                f"written={summary.written_frames} matched={summary.matched_frames} "
                f"calib={summary.calibration_samples} bias={summary.bias}"
            )
        except Exception as exc:
            message = f"[ERROR] {session_dir}: {exc}"
            print(message, file=sys.stderr)
            errors.append(message)

    print(
        json.dumps(
            {
                "processed": len(summaries),
                "failed": len(errors),
                "summaries": [_summary_to_jsonable(summary) for summary in summaries],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
