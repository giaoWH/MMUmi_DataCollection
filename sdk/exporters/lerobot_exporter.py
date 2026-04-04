from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from sdk.annotations import AnnotationService
from sdk.core.frame import TrajectoryFrame, seconds_to_ns
from sdk.exporters.base import ExportResult, SessionExporter
from sdk.storage import SessionReader


@dataclass(frozen=True)
class LeRobotActionSample:
    aligned_index: int
    aligned_record: dict[str, Any]
    current_trajectory: TrajectoryFrame
    next_trajectory: TrajectoryFrame
    current_gripper_position: float
    next_gripper_position: float


def count_lerobot_eligible_rows(reader: SessionReader) -> int:
    frame_index = _build_frame_index(reader)
    aligned_records = list(reader.iter_aligned_records())
    trajectory_frames = list(reader.iter_trajectory_frames())
    samples = _collect_lerobot_action_samples(
        aligned_records=aligned_records,
        trajectory_frames=trajectory_frames,
        frame_index=frame_index,
    )
    return len(samples)


class LeRobotSessionExporter(SessionExporter):
    export_format = "lerobot"

    def export(self, session_dir: str | Path, output_path: str | Path | None = None) -> ExportResult:
        reader = SessionReader(session_dir)
        target = Path(output_path) if output_path else Path(session_dir) / "exports" / "lerobot"
        meta_dir = target / "meta"
        episodes_dir = meta_dir / "episodes" / "chunk-000"
        data_dir = target / "data" / "chunk-000"
        meta_dir.mkdir(parents=True, exist_ok=True)
        episodes_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        frame_index = _build_frame_index(reader)
        trajectory_frames = list(reader.iter_trajectory_frames())
        aligned_records = list(reader.iter_aligned_records())
        samples = _collect_lerobot_action_samples(
            aligned_records=aligned_records,
            trajectory_frames=trajectory_frames,
            frame_index=frame_index,
        )
        annotation_service = AnnotationService(session_dir)
        session_annotations, row_annotations = annotation_service.build_lerobot_annotations(aligned_records)
        rows: list[dict[str, Any]] = []
        for row_index, sample in enumerate(samples):
            record = sample.aligned_record
            row: dict[str, Any] = {
                "index": row_index,
                "episode_index": 0,
                "frame_index": row_index,
                "timestamp": record["aligned_time"],
                "task_index": 0,
            }
            for sensor_name, frame_info in record.get("frames", {}).items():
                prefix = f"observation.{sensor_name}"
                row[f"{prefix}.frame_id"] = frame_info["frame_id"]
                row[f"{prefix}.host_time"] = frame_info["host_time"]
                row[f"{prefix}.device_time"] = frame_info.get("device_time")
                frame = frame_index.get(sensor_name, {}).get(frame_info["frame_id"])
                if frame is not None:
                    self._flatten_mapping(row, prefix, frame.payload)
            compensation = record.get("metadata", {}).get("gravity_compensation")
            if compensation:
                self._flatten_mapping(row, "observation.gravity_compensation", compensation)
            self._flatten_mapping(
                row,
                "observation.trajectory",
                {
                    "position": sample.current_trajectory.position,
                    "quaternion": sample.current_trajectory.quaternion,
                    "tracking_state": sample.current_trajectory.tracking_state,
                },
            )
            self._flatten_mapping(
                row,
                "action",
                {
                    "ee_delta": {
                        "position": [
                            sample.next_trajectory.position[index] - sample.current_trajectory.position[index]
                            for index in range(3)
                        ],
                        "quaternion": _relative_quaternion(
                            sample.current_trajectory.quaternion,
                            sample.next_trajectory.quaternion,
                        ),
                    },
                    "gripper": {
                        "position_delta": sample.next_gripper_position - sample.current_gripper_position,
                    },
                },
            )
            if sample.aligned_index < len(row_annotations):
                self._flatten_mapping(row, "", row_annotations[sample.aligned_index])
            rows.append(row)

        data_table = pa.Table.from_pylist(rows) if rows else pa.table({"index": pa.array([], type=pa.int64())})
        pq.write_table(data_table, data_dir / "file-000.parquet")

        task_name = self._resolve_task_name(session_annotations)
        episode_table = pa.Table.from_pylist(
            [
                {
                    "episode_index": 0,
                    "length": len(rows),
                    "task_index": 0,
                    "from_index": 0,
                    "to_index": len(rows),
                    **session_annotations,
                }
            ]
        )
        pq.write_table(episode_table, episodes_dir / "file-000.parquet")

        (meta_dir / "tasks.jsonl").write_text(
            json.dumps({"task_index": 0, "task": task_name}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (meta_dir / "stats.json").write_text(
            json.dumps(self._build_stats(rows), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (meta_dir / "info.json").write_text(
            json.dumps(self._build_info(reader, rows), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ExportResult(export_format=self.export_format, output_path=target)

    def _build_info(self, reader: SessionReader, rows: list[dict[str, Any]]) -> dict[str, Any]:
        features = {}
        for key in rows[0].keys() if rows else []:
            sample = next((row[key] for row in rows if key in row and row[key] is not None), None)
            features[key] = {"dtype": self._infer_feature_dtype(key, sample), "shape": [1]}

        return {
            "codebase_version": "v3.0",
            "robot_type": "unknown",
            "total_episodes": 1,
            "total_frames": len(rows),
            "total_tasks": 1,
            "chunks_size": len(rows),
            "fps": float(reader.manifest.config.get("align_rate_hz", 0) or 0),
            "splits": {"train": "0:1"},
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
            "features": features,
        }

    def _build_stats(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        stats: dict[str, dict[str, float]] = {}
        numeric_keys = {}
        for row in rows:
            for key, value in row.items():
                if isinstance(value, (int, float)) and key not in {"index", "episode_index", "frame_index", "task_index"}:
                    numeric_keys.setdefault(key, []).append(float(value))
        for key, values in numeric_keys.items():
            if values:
                stats[key] = {
                    "min": min(values),
                    "max": max(values),
                    "mean": sum(values) / len(values),
                }
        return stats

    def _resolve_task_name(self, session_annotations: dict[str, Any]) -> str:
        task_name = session_annotations.get("annotation.session.task_name")
        if isinstance(task_name, str) and task_name.strip():
            return task_name
        instruction = session_annotations.get("annotation.session.instruction")
        if isinstance(instruction, str) and instruction.strip():
            return instruction
        return "unspecified"

    def _infer_feature_dtype(self, key: str, sample: Any) -> str:
        if key in {"index", "episode_index", "frame_index", "task_index"}:
            return "int64"
        if isinstance(sample, bool):
            return "bool"
        if isinstance(sample, int):
            return "int64"
        if isinstance(sample, float):
            return "float64"
        if isinstance(sample, str):
            return "string"
        return "string"

    def _flatten_mapping(self, row: dict[str, Any], prefix: str, value: Any) -> None:
        if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
            self._flatten_mapping(row, prefix, value.tolist())
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                child_prefix = f"{prefix}.{child_key}" if prefix else child_key
                self._flatten_mapping(row, child_prefix, child_value)
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                child_prefix = f"{prefix}.{index}" if prefix else str(index)
                self._flatten_mapping(row, child_prefix, item)
            return
        row[prefix] = value


def _build_frame_index(reader: SessionReader) -> dict[str, dict[int, Any]]:
    return {
        sensor_name: {
            frame.frame_id: frame
            for frame in reader.iter_sensor_frames(sensor_name, load_payload=False)
        }
        for sensor_name in reader.sensor_names()
    }


def _collect_lerobot_action_samples(
    *,
    aligned_records: list[dict[str, Any]],
    trajectory_frames: list[TrajectoryFrame],
    frame_index: dict[str, dict[int, Any]],
) -> list[LeRobotActionSample]:
    samples: list[LeRobotActionSample] = []
    trajectory_index = _build_trajectory_index(trajectory_frames)
    for aligned_index in range(max(0, len(aligned_records) - 1)):
        current_record = aligned_records[aligned_index]
        next_record = aligned_records[aligned_index + 1]
        current_trajectory = _resolve_record_trajectory(current_record, trajectory_index)
        next_trajectory = _resolve_record_trajectory(next_record, trajectory_index)
        if current_trajectory is None or next_trajectory is None:
            continue
        if current_trajectory.tracking_state != "OK" or next_trajectory.tracking_state != "OK":
            continue
        current_gripper_position = _resolve_gripper_position(current_record, frame_index)
        next_gripper_position = _resolve_gripper_position(next_record, frame_index)
        if current_gripper_position is None or next_gripper_position is None:
            continue
        samples.append(
            LeRobotActionSample(
                aligned_index=aligned_index,
                aligned_record=current_record,
                current_trajectory=current_trajectory,
                next_trajectory=next_trajectory,
                current_gripper_position=current_gripper_position,
                next_gripper_position=next_gripper_position,
            )
        )
    return samples


def _build_trajectory_index(trajectory_frames: list[TrajectoryFrame]) -> dict[int, TrajectoryFrame]:
    index: dict[int, TrajectoryFrame] = {}
    for frame in trajectory_frames:
        timestamp_ns = frame.time.host_time_ns
        if timestamp_ns is None:
            timestamp_ns = seconds_to_ns(frame.time.host_time)
        if timestamp_ns is None:
            continue
        index[int(timestamp_ns)] = frame
    return index


def _resolve_record_trajectory(
    record: dict[str, Any],
    trajectory_index: dict[int, TrajectoryFrame],
) -> TrajectoryFrame | None:
    record_frames = record.get("frames", {})
    if not isinstance(record_frames, dict):
        return None
    realsense_info = record_frames.get("realsense")
    if not isinstance(realsense_info, dict):
        return None
    timestamp_ns = realsense_info.get("device_time_ns")
    if timestamp_ns is None:
        raw_timestamp = realsense_info.get("device_time")
        if raw_timestamp is None:
            return None
        timestamp_ns = seconds_to_ns(float(raw_timestamp))
    if timestamp_ns is None:
        return None
    return trajectory_index.get(int(timestamp_ns))


def _resolve_gripper_position(
    record: dict[str, Any],
    frame_index: dict[str, dict[int, Any]],
) -> float | None:
    record_frames = record.get("frames", {})
    if not isinstance(record_frames, dict):
        return None
    motors_info = record_frames.get("motors")
    if not isinstance(motors_info, dict):
        return None
    frame_id = motors_info.get("frame_id")
    if not isinstance(frame_id, int):
        return None
    motors_frame = frame_index.get("motors", {}).get(frame_id)
    if motors_frame is None or not isinstance(motors_frame.payload, dict):
        return None
    motor_2 = motors_frame.payload.get("motor_2")
    if not isinstance(motor_2, dict):
        return None
    position = motor_2.get("position")
    if not isinstance(position, (int, float)):
        return None
    return float(position)


def _relative_quaternion(current: list[float], target: list[float]) -> list[float]:
    inverse_current = _quaternion_inverse(current)
    delta = _quaternion_multiply(target, inverse_current)
    norm = math.sqrt(sum(value * value for value in delta))
    if math.isclose(norm, 0.0, abs_tol=1e-12):
        return [1.0, 0.0, 0.0, 0.0]
    return [value / norm for value in delta]


def _quaternion_inverse(quaternion: list[float]) -> list[float]:
    norm_sq = sum(value * value for value in quaternion)
    if math.isclose(norm_sq, 0.0, abs_tol=1e-12):
        return [1.0, 0.0, 0.0, 0.0]
    qw, qx, qy, qz = quaternion
    return [qw / norm_sq, -qx / norm_sq, -qy / norm_sq, -qz / norm_sq]


def _quaternion_multiply(left: list[float], right: list[float]) -> list[float]:
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return [
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    ]
