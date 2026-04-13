from __future__ import annotations

from bisect import bisect_left
import json
import os
import shlex
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sdk.core.frame import FrameTime, SensorFrame, TrajectoryFrame, seconds_to_ns
from sdk.storage import SessionReader
from sdk.storage.schema import manifest_path_for

from .pipeline import OrbSlam3Pipeline, OrbSlam3PipelineConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
OFFICIAL_ORBSLAM3_MODE = "stereo_inertial"
OFFICIAL_OUTPUT_MODE = "jsonl_file"
DEFAULT_TRAJECTORY_MATCH_TOLERANCE_NS = 2_000_000
MIN_TRAJECTORY_MATCHED_RATIO = 0.99
MAX_TRAJECTORY_UNMATCHED_RATIO = 0.01


@dataclass(frozen=True)
class OrbSlam3SessionProcessConfig:
    command: str | None = None
    mode: str = OFFICIAL_ORBSLAM3_MODE
    output_mode: str = OFFICIAL_OUTPUT_MODE
    source_name: str = "orbslam3"
    working_dir: str | None = None
    bundle_dir: str | None = None
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class OrbSlam3SessionProcessResult:
    mode: str
    source_name: str
    bundle_dir: Path
    trajectory_frames: int


@dataclass(frozen=True)
class _AlignedMatch:
    sequence_id: int
    aligned_time_ns: int


@dataclass(frozen=True)
class _FrameMatch:
    sensor_frame: SensorFrame
    error_ns: int
    matched_exactly: bool


def load_orbslam3_process_config(config_payload: dict[str, Any]) -> OrbSlam3SessionProcessConfig:
    payload = config_payload.get("trajectory", config_payload)
    if not isinstance(payload, dict):
        raise ValueError("trajectory 配置必须是对象")

    return OrbSlam3SessionProcessConfig(
        command=payload.get("command") or build_default_stereo_inertial_command(),
        mode=payload.get("mode", OFFICIAL_ORBSLAM3_MODE),
        output_mode=payload.get("output_mode", OFFICIAL_OUTPUT_MODE),
        source_name=payload.get("source_name", "orbslam3"),
        working_dir=payload.get("working_dir"),
        bundle_dir=payload.get("bundle_dir"),
        env=payload.get("env"),
    )


def process_orbslam3_session(
    session_dir: str | Path,
    config: OrbSlam3SessionProcessConfig,
) -> OrbSlam3SessionProcessResult:
    session_path = Path(session_dir)
    normalized_config = normalize_orbslam3_process_config(config)

    pipeline = OrbSlam3Pipeline(
        OrbSlam3PipelineConfig(
            command=normalized_config.command,
            mode=normalized_config.mode,
            output_mode=normalized_config.output_mode,
            source_name=normalized_config.source_name,
            working_dir=normalized_config.working_dir,
            bundle_dir=normalized_config.bundle_dir,
            env=normalized_config.env,
        )
    )
    bundle, trajectory_frames = pipeline.run(session_dir)
    if not trajectory_frames:
        raise RuntimeError("ORB-SLAM3 未生成有效轨迹")
    rebound_frames, alignment_summary, source_time_semantics = _rebind_trajectory_frames(
        session_path,
        trajectory_frames,
    )
    _replace_trajectory_frames(session_path, rebound_frames)

    notes_update = {
        "trajectory_source": normalized_config.source_name,
        "orbslam3_mode": normalized_config.mode,
        "orbslam3_bundle": str(bundle.bundle_dir),
        "trajectory_source_time_semantics": source_time_semantics,
        "trajectory_time_alignment": {
            "matched_sensor": "realsense",
            "match_strategy": "device_time_exact_then_nearest_with_tolerance",
            "device_time_tolerance_ns": DEFAULT_TRAJECTORY_MATCH_TOLERANCE_NS,
            "output_time_fields": [
                "time.host_time_ns",
                "time.device_time_ns",
                "time.aligned_time_ns",
            ],
        },
        "trajectory_time_alignment_summary": alignment_summary,
    }
    _update_session_notes(session_path, notes_update)

    return OrbSlam3SessionProcessResult(
        mode=normalized_config.mode,
        source_name=normalized_config.source_name,
        bundle_dir=bundle.bundle_dir,
        trajectory_frames=len(rebound_frames),
    )


def build_default_stereo_inertial_command(python_executable: str | None = None) -> str:
    python_path = python_executable or sys.executable
    wrapper_path = REPO_ROOT / "scripts" / "orbslam3_wrapper.py"
    return " ".join(
        [
            shlex.quote(python_path),
            shlex.quote(str(wrapper_path)),
            "--mode",
            OFFICIAL_ORBSLAM3_MODE,
            "--bundle-manifest",
            "{bundle_manifest}",
            "--output",
            "{output_jsonl}",
        ]
    )


def normalize_orbslam3_process_config(
    config: OrbSlam3SessionProcessConfig,
) -> OrbSlam3SessionProcessConfig:
    if config.mode != OFFICIAL_ORBSLAM3_MODE:
        raise ValueError(
            f"当前官方轨迹处理仅支持 {OFFICIAL_ORBSLAM3_MODE}，收到 {config.mode}"
        )
    if config.output_mode != OFFICIAL_OUTPUT_MODE:
        raise ValueError(
            f"当前官方轨迹处理仅支持 {OFFICIAL_OUTPUT_MODE} 输出，收到 {config.output_mode}"
        )
    return replace(
        config,
        command=config.command or build_default_stereo_inertial_command(),
        output_mode=OFFICIAL_OUTPUT_MODE,
    )


def _update_session_notes(session_dir: Path, notes_update: dict[str, Any]) -> None:
    manifest_path = manifest_path_for(session_dir)
    _update_json_notes_file(manifest_path, notes_update)

    meta_path = session_dir / "meta.json"
    if meta_path.exists():
        _update_json_notes_file(meta_path, notes_update)


def _update_json_notes_file(path: Path, notes_update: dict[str, Any]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    notes = payload.setdefault("notes", {})
    notes.update(notes_update)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _replace_trajectory_frames(session_dir: Path, frames: list[TrajectoryFrame]) -> None:
    trajectory_dir = session_dir / "trajectory"
    trajectory_dir.mkdir(parents=True, exist_ok=True)
    target_path = trajectory_dir / "frames.jsonl"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=trajectory_dir,
            prefix="frames.",
            suffix=".jsonl.tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            for frame in frames:
                handle.write(json.dumps(_trajectory_record(frame), ensure_ascii=False))
                handle.write("\n")
        os.replace(temp_path, target_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _trajectory_record(frame: TrajectoryFrame) -> dict[str, Any]:
    return {
        "source": frame.source,
        "frame_id": frame.frame_id,
        "time": {
            "host_time": frame.time.host_time,
            "host_time_ns": frame.time.host_time_ns,
            "monotonic_time": frame.time.monotonic_time,
            "monotonic_time_ns": frame.time.monotonic_time_ns,
            "device_time": frame.time.device_time,
            "device_time_ns": frame.time.device_time_ns,
            "aligned_time": frame.time.aligned_time,
            "aligned_time_ns": frame.time.aligned_time_ns,
        },
        "position": frame.position,
        "quaternion": frame.quaternion,
        "tracking_state": frame.tracking_state,
        "metadata": frame.metadata,
    }


def _rebind_trajectory_frames(
    session_dir: Path,
    frames: list[TrajectoryFrame],
) -> tuple[list[TrajectoryFrame], dict[str, Any], str | list[str] | None]:
    reader = SessionReader(session_dir)
    realsense_sensor = _find_realsense_sensor_name(reader)
    realsense_frames = list(reader.iter_sensor_frames(realsense_sensor, load_payload=False))
    if not realsense_frames:
        raise RuntimeError("session 中不存在可用于轨迹回绑的 RealSense 原始帧")

    exact_index, sorted_device_times = _build_realsense_device_time_index(realsense_frames)
    aligned_candidates = _build_aligned_candidates(reader, realsense_sensor)
    if not aligned_candidates:
        raise RuntimeError("session 中不存在 aligned/frames.jsonl 或其中没有可用的 RealSense 对齐记录")

    session_start_ns, session_end_ns = _resolve_session_time_window_ns(reader)
    rebound_frames: list[TrajectoryFrame] = []
    source_time_semantics: set[str] = set()
    matched_exact_count = 0
    matched_tolerance_count = 0
    unmatched_count = 0
    match_errors_ns: list[int] = []

    for frame in frames:
        source_time_semantics.add(str(frame.metadata.get("time_semantics", "trajectory_result_timestamp")))
        source_device_time_ns = _trajectory_source_device_time_ns(frame)
        if source_device_time_ns is None:
            unmatched_count += 1
            continue
        match = _match_realsense_frame(
            source_device_time_ns=source_device_time_ns,
            exact_index=exact_index,
            sorted_device_times=sorted_device_times,
            tolerance_ns=DEFAULT_TRAJECTORY_MATCH_TOLERANCE_NS,
        )
        if match is None:
            unmatched_count += 1
            continue
        aligned_match = _match_aligned_record(match.sensor_frame, aligned_candidates)
        if aligned_match is None:
            unmatched_count += 1
            continue

        host_time_ns = match.sensor_frame.time.host_time_ns
        monotonic_time_ns = match.sensor_frame.time.monotonic_time_ns
        if host_time_ns is None:
            unmatched_count += 1
            continue
        if session_start_ns is not None and host_time_ns < session_start_ns:
            unmatched_count += 1
            continue
        if session_end_ns is not None and host_time_ns > session_end_ns:
            unmatched_count += 1
            continue

        if match.matched_exactly:
            matched_exact_count += 1
        else:
            matched_tolerance_count += 1
        match_errors_ns.append(match.error_ns)
        rebound_frames.append(
            _build_rebound_trajectory_frame(
                frame=frame,
                matched_sensor_frame=match.sensor_frame,
                aligned_match=aligned_match,
                source_device_time_ns=source_device_time_ns,
                match_error_ns=match.error_ns,
                matched_exactly=match.matched_exactly,
                monotonic_time_ns=monotonic_time_ns,
                host_time_ns=host_time_ns,
            )
        )

    summary = _build_alignment_summary(
        trajectory_frame_count=len(frames),
        matched_exact_count=matched_exact_count,
        matched_tolerance_count=matched_tolerance_count,
        unmatched_count=unmatched_count,
        match_errors_ns=match_errors_ns,
        written_frame_count=len(rebound_frames),
    )
    _validate_alignment_summary(summary, rebound_frames)
    semantics = sorted(source_time_semantics)
    if not semantics:
        normalized_semantics: str | list[str] | None = None
    elif len(semantics) == 1:
        normalized_semantics = semantics[0]
    else:
        normalized_semantics = semantics
    return rebound_frames, summary, normalized_semantics


def _find_realsense_sensor_name(reader: SessionReader) -> str:
    for sensor_name, stream in reader.manifest.sensors.items():
        if "realsense" in str(stream.sensor_type):
            return sensor_name
    raise RuntimeError("session 中不存在 RealSense 传感器，无法回绑 ORB-SLAM3 轨迹")


def _build_realsense_device_time_index(
    frames: list[SensorFrame],
) -> tuple[dict[int, SensorFrame], list[int]]:
    exact_index: dict[int, SensorFrame] = {}
    sorted_device_times: list[int] = []
    for frame in frames:
        device_time_ns = _sensor_frame_device_time_ns(frame)
        if device_time_ns is None:
            continue
        exact_index[int(device_time_ns)] = frame
        sorted_device_times.append(int(device_time_ns))
    sorted_device_times.sort()
    if not exact_index:
        raise RuntimeError("RealSense 原始帧缺少 device_time_ns，无法回绑 ORB-SLAM3 轨迹")
    return exact_index, sorted_device_times


def _build_aligned_candidates(
    reader: SessionReader,
    realsense_sensor: str,
) -> dict[int, list[_AlignedMatch]]:
    candidates: dict[int, list[_AlignedMatch]] = {}
    for record in reader.iter_aligned_records():
        if not isinstance(record, dict):
            continue
        frames_payload = record.get("frames", {})
        if not isinstance(frames_payload, dict):
            continue
        sensor_payload = frames_payload.get(realsense_sensor)
        if not isinstance(sensor_payload, dict):
            continue
        frame_id = sensor_payload.get("frame_id")
        aligned_time_ns = _record_time_ns(record, "aligned_time_ns", "aligned_time")
        sequence_id = record.get("sequence_id")
        if not isinstance(frame_id, int) or aligned_time_ns is None or not isinstance(sequence_id, int):
            continue
        candidates.setdefault(frame_id, []).append(
            _AlignedMatch(sequence_id=int(sequence_id), aligned_time_ns=int(aligned_time_ns))
        )
    return candidates


def _resolve_session_time_window_ns(reader: SessionReader) -> tuple[int | None, int | None]:
    notes = reader.manifest.notes
    window = notes.get("session_time_window", {})
    start_ns = _value_to_ns(window.get("start_host_time_ns"), window.get("start_host_time"))
    end_ns = _value_to_ns(window.get("end_host_time_ns"), window.get("end_host_time"))
    if start_ns is not None and end_ns is not None:
        return start_ns, end_ns

    first_times: list[int] = []
    last_times: list[int] = []
    sensor_summary = notes.get("sensor_time_summary", {})
    if isinstance(sensor_summary, dict):
        for summary in sensor_summary.values():
            if not isinstance(summary, dict):
                continue
            first_ns = _value_to_ns(
                summary.get("first_kept_host_time_ns"),
                summary.get("first_kept_host_time"),
            )
            last_ns = _value_to_ns(
                summary.get("last_kept_host_time_ns"),
                summary.get("last_kept_host_time"),
            )
            if first_ns is not None:
                first_times.append(first_ns)
            if last_ns is not None:
                last_times.append(last_ns)
    return (min(first_times) if first_times else None, max(last_times) if last_times else None)


def _trajectory_source_device_time_ns(frame: TrajectoryFrame) -> int | None:
    if frame.time.device_time_ns is not None:
        return int(frame.time.device_time_ns)
    if frame.time.host_time_ns is not None:
        return int(frame.time.host_time_ns)
    if frame.time.device_time is not None:
        return seconds_to_ns(frame.time.device_time)
    return seconds_to_ns(frame.time.host_time)


def _sensor_frame_device_time_ns(frame: SensorFrame) -> int | None:
    if frame.time.device_time_ns is not None:
        return int(frame.time.device_time_ns)
    return seconds_to_ns(frame.time.device_time)


def _match_realsense_frame(
    *,
    source_device_time_ns: int,
    exact_index: dict[int, SensorFrame],
    sorted_device_times: list[int],
    tolerance_ns: int,
) -> _FrameMatch | None:
    exact = exact_index.get(int(source_device_time_ns))
    if exact is not None:
        return _FrameMatch(sensor_frame=exact, error_ns=0, matched_exactly=True)

    position = bisect_left(sorted_device_times, int(source_device_time_ns))
    candidate_times: list[int] = []
    if position < len(sorted_device_times):
        candidate_times.append(sorted_device_times[position])
    if position > 0:
        candidate_times.append(sorted_device_times[position - 1])
    if not candidate_times:
        return None
    best_time = min(candidate_times, key=lambda value: abs(value - int(source_device_time_ns)))
    error_ns = abs(best_time - int(source_device_time_ns))
    if error_ns > int(tolerance_ns):
        return None
    matched = exact_index.get(best_time)
    if matched is None:
        return None
    return _FrameMatch(sensor_frame=matched, error_ns=error_ns, matched_exactly=False)


def _match_aligned_record(
    sensor_frame: SensorFrame,
    aligned_candidates: dict[int, list[_AlignedMatch]],
) -> _AlignedMatch | None:
    candidates = aligned_candidates.get(int(sensor_frame.frame_id), [])
    if not candidates:
        return None
    host_time_ns = sensor_frame.time.host_time_ns
    if host_time_ns is None:
        return candidates[0]
    return min(candidates, key=lambda candidate: abs(candidate.aligned_time_ns - int(host_time_ns)))


def _build_rebound_trajectory_frame(
    *,
    frame: TrajectoryFrame,
    matched_sensor_frame: SensorFrame,
    aligned_match: _AlignedMatch,
    source_device_time_ns: int,
    match_error_ns: int,
    matched_exactly: bool,
    monotonic_time_ns: int | None,
    host_time_ns: int,
) -> TrajectoryFrame:
    metadata = dict(frame.metadata)
    source_time_semantics = metadata.pop("time_semantics", "trajectory_result_timestamp")
    metadata["source_time_semantics"] = source_time_semantics
    metadata["time_semantics"] = {
        "device_time_ns": "orbslam3_source_realsense_device_time",
        "host_time_ns": "session_host_time",
        "aligned_time_ns": "session_aligned_time",
    }
    metadata["time_alignment"] = {
        "match_strategy": (
            "device_time_exact"
            if matched_exactly
            else "device_time_nearest_within_tolerance"
        ),
        "matched_sensor": matched_sensor_frame.sensor_name,
        "matched_frame_id": int(matched_sensor_frame.frame_id),
        "matched_sequence_id": int(aligned_match.sequence_id),
        "match_error_ns": int(match_error_ns),
        "matched_exactly": bool(matched_exactly),
    }
    return replace(
        frame,
        time=FrameTime(
            host_time_ns=int(host_time_ns),
            monotonic_time_ns=(
                int(monotonic_time_ns)
                if monotonic_time_ns is not None
                else int(host_time_ns)
            ),
            device_time_ns=int(source_device_time_ns),
            aligned_time_ns=int(aligned_match.aligned_time_ns),
        ),
        metadata=metadata,
    )


def _build_alignment_summary(
    *,
    trajectory_frame_count: int,
    matched_exact_count: int,
    matched_tolerance_count: int,
    unmatched_count: int,
    match_errors_ns: list[int],
    written_frame_count: int,
) -> dict[str, Any]:
    matched_count = matched_exact_count + matched_tolerance_count
    max_match_error_ns = max(match_errors_ns) if match_errors_ns else None
    mean_match_error_ns = (
        int(round(sum(match_errors_ns) / len(match_errors_ns)))
        if match_errors_ns
        else None
    )
    return {
        "trajectory_frame_count": int(trajectory_frame_count),
        "written_trajectory_frame_count": int(written_frame_count),
        "matched_exact_count": int(matched_exact_count),
        "matched_tolerance_count": int(matched_tolerance_count),
        "unmatched_count": int(unmatched_count),
        "matched_ratio": _safe_ratio(matched_count, trajectory_frame_count),
        "max_match_error_ns": max_match_error_ns,
        "mean_match_error_ns": mean_match_error_ns,
        "aligned_coverage_ratio": _safe_ratio(written_frame_count, trajectory_frame_count),
    }


def _validate_alignment_summary(
    summary: dict[str, Any],
    rebound_frames: list[TrajectoryFrame],
) -> None:
    matched_ratio = float(summary.get("matched_ratio", 0.0) or 0.0)
    unmatched_count = int(summary.get("unmatched_count", 0) or 0)
    total_count = int(summary.get("trajectory_frame_count", 0) or 0)
    unmatched_ratio = _safe_ratio(unmatched_count, total_count)
    max_match_error_ns = summary.get("max_match_error_ns")
    errors: list[str] = []
    if matched_ratio < MIN_TRAJECTORY_MATCHED_RATIO:
        errors.append(
            f"轨迹回绑成功率不足: matched_ratio={matched_ratio:.6f} < {MIN_TRAJECTORY_MATCHED_RATIO:.2f}"
        )
    if unmatched_ratio > MAX_TRAJECTORY_UNMATCHED_RATIO:
        errors.append(
            f"轨迹未匹配比例过高: unmatched_ratio={unmatched_ratio:.6f} > {MAX_TRAJECTORY_UNMATCHED_RATIO:.2f}"
        )
    if max_match_error_ns is not None and int(max_match_error_ns) > DEFAULT_TRAJECTORY_MATCH_TOLERANCE_NS:
        errors.append(
            f"轨迹匹配误差超限: max_match_error_ns={int(max_match_error_ns)} > {DEFAULT_TRAJECTORY_MATCH_TOLERANCE_NS}"
        )
    if not rebound_frames:
        errors.append("轨迹回绑后没有任何有效轨迹点")
    for frame in rebound_frames:
        if frame.time.host_time_ns is None:
            errors.append("存在未绑定 host_time_ns 的轨迹点")
            break
        if frame.time.aligned_time_ns is None:
            errors.append("存在未绑定 aligned_time_ns 的轨迹点")
            break
    if errors:
        raise RuntimeError("轨迹未能可靠挂到 session 统一时间轴:\n" + "\n".join(errors))


def _record_time_ns(record: dict[str, Any], ns_key: str, seconds_key: str) -> int | None:
    return _value_to_ns(record.get(ns_key), record.get(seconds_key))


def _value_to_ns(ns_value: Any, seconds_value: Any) -> int | None:
    if ns_value is not None:
        try:
            return int(ns_value)
        except (TypeError, ValueError):
            return None
    if seconds_value is None:
        return None
    try:
        return seconds_to_ns(float(seconds_value))
    except (TypeError, ValueError):
        return None


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)
