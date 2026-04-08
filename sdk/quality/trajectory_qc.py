from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
from typing import Any

from sdk.exporters.lerobot_exporter import (
    _build_frame_index,
    _build_trajectory_index,
    _relative_quaternion,
    _resolve_gripper_position,
    _resolve_record_trajectory,
)
from sdk.storage import SessionReader


@dataclass(frozen=True)
class TrajectoryQcThresholds:
    min_trajectory_coverage_ratio: float = 0.95
    min_action_eligible_ratio: float = 0.9
    max_fail_step_ratio: float = 0.1
    max_translation_delta_m: float = 0.15
    max_rotation_delta_deg: float = 45.0
    max_translation_speed_mps: float = 1.0
    max_rotation_speed_degps: float = 360.0
    max_translation_accel_mps2: float = 10.0
    max_rotation_accel_degps2: float = 2_000.0


@dataclass(frozen=True)
class TrajectoryQcConfig:
    enabled: bool = True
    output_dir: str = "quality"
    output_filename: str = "trajectory_qc.json"
    thresholds: TrajectoryQcThresholds = field(default_factory=TrajectoryQcThresholds)


@dataclass(frozen=True)
class TrajectoryQcResult:
    session_dir: Path
    output_path: Path
    report: dict[str, Any]

    @property
    def session_status(self) -> str:
        return str(self.report["session_status"])


def load_trajectory_qc_config(config_payload: dict[str, Any]) -> TrajectoryQcConfig:
    payload = config_payload.get("trajectory_qc", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("trajectory_qc 配置必须是对象")
    thresholds_payload = payload.get("thresholds", {})
    if thresholds_payload is None:
        thresholds_payload = {}
    if not isinstance(thresholds_payload, dict):
        raise ValueError("trajectory_qc.thresholds 配置必须是对象")
    return TrajectoryQcConfig(
        enabled=bool(payload.get("enabled", True)),
        output_dir=str(payload.get("output_dir", "quality")),
        output_filename=str(payload.get("output_filename", "trajectory_qc.json")),
        thresholds=TrajectoryQcThresholds(**thresholds_payload),
    )


def run_trajectory_qc(
    session_dir: str | Path,
    config: TrajectoryQcConfig | None = None,
) -> TrajectoryQcResult:
    resolved_session_dir = Path(session_dir)
    resolved_config = config or TrajectoryQcConfig()
    reader = SessionReader(resolved_session_dir)
    aligned_records = list(reader.iter_aligned_records())
    trajectory_frames = list(reader.iter_trajectory_frames())
    frame_index = _build_frame_index(reader)
    trajectory_index = _build_trajectory_index(trajectory_frames)
    trajectory_path = resolved_session_dir / reader.manifest.trajectory_path

    matched_trajectory_records = sum(
        1 for record in aligned_records if _resolve_record_trajectory(record, trajectory_index) is not None
    )
    step_results = _build_step_results(
        aligned_records=aligned_records,
        trajectory_index=trajectory_index,
        frame_index=frame_index,
        thresholds=resolved_config.thresholds,
    )

    transition_step_count = len(step_results)
    pass_step_count = sum(1 for item in step_results if item["status"] == "pass")
    warn_step_count = sum(1 for item in step_results if item["status"] == "warn")
    fail_step_count = sum(1 for item in step_results if item["status"] == "fail")
    action_eligible_step_count = pass_step_count + warn_step_count

    aligned_step_count = len(aligned_records)
    coverage_ratio = _safe_ratio(matched_trajectory_records, aligned_step_count)
    action_eligible_ratio = _safe_ratio(action_eligible_step_count, transition_step_count)
    fail_step_ratio = _safe_ratio(fail_step_count, transition_step_count)

    thresholds_dict = asdict(resolved_config.thresholds)
    summary = {
        "aligned_step_count": aligned_step_count,
        "transition_step_count": transition_step_count,
        "trajectory_frame_count": len(trajectory_frames),
        "matched_trajectory_record_count": matched_trajectory_records,
        "coverage_ratio": coverage_ratio,
        "action_eligible_step_count": action_eligible_step_count,
        "action_eligible_ratio": action_eligible_ratio,
        "pass_step_count": pass_step_count,
        "warn_step_count": warn_step_count,
        "fail_step_count": fail_step_count,
        "fail_step_ratio": fail_step_ratio,
    }
    session_flags: list[str] = []
    if not trajectory_path.exists():
        session_flags.append("missing_trajectory_file")
    elif not trajectory_frames:
        session_flags.append("empty_trajectory")
    if not _trajectory_is_monotonic(trajectory_frames):
        session_flags.append("non_monotonic_trajectory")
    if coverage_ratio < resolved_config.thresholds.min_trajectory_coverage_ratio:
        session_flags.append("low_trajectory_coverage")
    if action_eligible_ratio < resolved_config.thresholds.min_action_eligible_ratio:
        session_flags.append("low_action_eligible_ratio")
    if fail_step_ratio > resolved_config.thresholds.max_fail_step_ratio:
        session_flags.append("high_fail_step_ratio")

    session_status = _session_status(session_flags=session_flags, warn_step_count=warn_step_count)
    report = {
        "session_status": session_status,
        "summary": summary,
        "thresholds": thresholds_dict,
        "session_flags": session_flags,
        "step_results": step_results,
    }
    output_path = resolved_session_dir / resolved_config.output_dir / resolved_config.output_filename
    return TrajectoryQcResult(
        session_dir=resolved_session_dir,
        output_path=output_path,
        report=report,
    )


def write_trajectory_qc_report(result: TrajectoryQcResult) -> Path:
    result.output_path.parent.mkdir(parents=True, exist_ok=True)
    result.output_path.write_text(
        json.dumps(result.report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result.output_path


def _build_step_results(
    *,
    aligned_records: list[dict[str, Any]],
    trajectory_index: dict[int, Any],
    frame_index: dict[str, dict[int, Any]],
    thresholds: TrajectoryQcThresholds,
) -> list[dict[str, Any]]:
    step_results: list[dict[str, Any]] = []
    previous_valid_metrics: dict[str, float] | None = None
    for index in range(max(0, len(aligned_records) - 1)):
        current_record = aligned_records[index]
        next_record = aligned_records[index + 1]
        current_trajectory = _resolve_record_trajectory(current_record, trajectory_index)
        next_trajectory = _resolve_record_trajectory(next_record, trajectory_index)
        current_gripper = _resolve_gripper_position(current_record, frame_index)
        next_gripper = _resolve_gripper_position(next_record, frame_index)
        metrics = _empty_metrics()
        fail_flags: list[str] = []
        warn_flags: list[str] = []

        if current_trajectory is None:
            fail_flags.append("missing_current_trajectory")
        if next_trajectory is None:
            fail_flags.append("missing_next_trajectory")
        if current_trajectory is not None and current_trajectory.tracking_state != "OK":
            fail_flags.append("current_tracking_not_ok")
        if next_trajectory is not None and next_trajectory.tracking_state != "OK":
            fail_flags.append("next_tracking_not_ok")
        if current_gripper is None:
            fail_flags.append("missing_current_gripper")
        if next_gripper is None:
            fail_flags.append("missing_next_gripper")

        if current_trajectory is not None and next_trajectory is not None:
            dt_sec = float(next_record["aligned_time"]) - float(current_record["aligned_time"])
            metrics["dt_sec"] = dt_sec
            if not math.isfinite(dt_sec) or dt_sec <= 0.0:
                fail_flags.append("invalid_dt")
            else:
                translation_delta = _translation_delta(
                    current_trajectory.position,
                    next_trajectory.position,
                )
                rotation_delta_deg = _rotation_delta_deg(
                    current_trajectory.quaternion,
                    next_trajectory.quaternion,
                )
                metrics["translation_delta_m"] = translation_delta
                metrics["rotation_delta_deg"] = rotation_delta_deg
                metrics["translation_speed_mps"] = translation_delta / dt_sec
                metrics["rotation_speed_degps"] = rotation_delta_deg / dt_sec
                if current_gripper is not None and next_gripper is not None:
                    metrics["gripper_delta"] = next_gripper - current_gripper

                if previous_valid_metrics is not None:
                    previous_translation_speed = previous_valid_metrics.get("translation_speed_mps")
                    previous_rotation_speed = previous_valid_metrics.get("rotation_speed_degps")
                    if previous_translation_speed is not None:
                        metrics["translation_accel_mps2"] = abs(
                            metrics["translation_speed_mps"] - previous_translation_speed
                        ) / dt_sec
                    if previous_rotation_speed is not None:
                        metrics["rotation_accel_degps2"] = abs(
                            metrics["rotation_speed_degps"] - previous_rotation_speed
                        ) / dt_sec

                warn_flags.extend(_warn_flags_for_metrics(metrics, thresholds))
                if not warn_flags:
                    previous_valid_metrics = dict(metrics)
                else:
                    previous_valid_metrics = None
        status = "fail" if fail_flags else "warn" if warn_flags else "pass"
        if fail_flags:
            previous_valid_metrics = None
        step_results.append(
            {
                "sequence_id": int(current_record["sequence_id"]),
                "aligned_time": float(current_record["aligned_time"]),
                "status": status,
                "flags": fail_flags + warn_flags,
                "metrics": metrics,
            }
        )
    return step_results


def _empty_metrics() -> dict[str, float | None]:
    return {
        "dt_sec": None,
        "translation_delta_m": None,
        "rotation_delta_deg": None,
        "translation_speed_mps": None,
        "rotation_speed_degps": None,
        "translation_accel_mps2": None,
        "rotation_accel_degps2": None,
        "gripper_delta": None,
    }


def _warn_flags_for_metrics(
    metrics: dict[str, float | None],
    thresholds: TrajectoryQcThresholds,
) -> list[str]:
    flags: list[str] = []
    if _exceeds(metrics["translation_delta_m"], thresholds.max_translation_delta_m):
        flags.append("translation_delta_exceeds_threshold")
    if _exceeds(metrics["rotation_delta_deg"], thresholds.max_rotation_delta_deg):
        flags.append("rotation_delta_exceeds_threshold")
    if _exceeds(metrics["translation_speed_mps"], thresholds.max_translation_speed_mps):
        flags.append("translation_speed_exceeds_threshold")
    if _exceeds(metrics["rotation_speed_degps"], thresholds.max_rotation_speed_degps):
        flags.append("rotation_speed_exceeds_threshold")
    if _exceeds(metrics["translation_accel_mps2"], thresholds.max_translation_accel_mps2):
        flags.append("translation_accel_exceeds_threshold")
    if _exceeds(metrics["rotation_accel_degps2"], thresholds.max_rotation_accel_degps2):
        flags.append("rotation_accel_exceeds_threshold")
    return flags


def _exceeds(value: float | None, threshold: float) -> bool:
    return value is not None and math.isfinite(value) and value > threshold


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _translation_delta(current: list[float], target: list[float]) -> float:
    return math.sqrt(sum((float(target[index]) - float(current[index])) ** 2 for index in range(3)))


def _rotation_delta_deg(current: list[float], target: list[float]) -> float:
    delta = _relative_quaternion(current, target)
    qw = max(-1.0, min(1.0, abs(float(delta[0]))))
    return math.degrees(2.0 * math.acos(qw))


def _trajectory_is_monotonic(trajectory_frames: list[Any]) -> bool:
    last_time: float | None = None
    for frame in trajectory_frames:
        timestamp = frame.time.host_time
        if timestamp is None or not math.isfinite(timestamp):
            return False
        if last_time is not None and timestamp < last_time:
            return False
        last_time = timestamp
    return True


def _session_status(*, session_flags: list[str], warn_step_count: int) -> str:
    if session_flags:
        return "fail"
    if warn_step_count > 0:
        return "warn"
    return "pass"
