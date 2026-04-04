from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import wave
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from sdk.annotations import AnnotationService
from sdk.core.frame import TrajectoryFrame, seconds_to_ns
from sdk.exporters.base import ExportResult, SessionExporter
from sdk.storage import SessionReader

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


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
        images_dir = target / "images"
        audio_dir = target / "audio"
        meta_dir.mkdir(parents=True, exist_ok=True)
        episodes_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        images_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)

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
        align_rate_hz = float(reader.manifest.config.get("align_rate_hz", 0) or 0)
        feature_overrides: dict[str, dict[str, Any]] = {
            "action": {
                "dtype": "float32",
                "shape": [8],
                "names": [
                    "ee_delta_x",
                    "ee_delta_y",
                    "ee_delta_z",
                    "ee_delta_qw",
                    "ee_delta_qx",
                    "ee_delta_qy",
                    "ee_delta_qz",
                    "gripper_delta",
                ],
            }
        }
        state_field_order: list[str] = []
        row_state_maps: list[dict[str, float]] = []
        rows: list[dict[str, Any]] = []
        for row_index, sample in enumerate(samples):
            record = sample.aligned_record
            current_compensation = record.get("metadata", {}).get("gravity_compensation")
            row: dict[str, Any] = {
                "index": row_index,
                "episode_index": 0,
                "frame_index": row_index,
                "timestamp": record["aligned_time"],
                "task_index": 0,
            }
            action_vector = [
                sample.next_trajectory.position[index] - sample.current_trajectory.position[index]
                for index in range(3)
            ]
            action_vector.extend(
                _relative_quaternion(
                    sample.current_trajectory.quaternion,
                    sample.next_trajectory.quaternion,
                )
            )
            action_vector.append(sample.next_gripper_position - sample.current_gripper_position)
            row["action"] = action_vector
            for sensor_name, frame_info in record.get("frames", {}).items():
                prefix = f"observation.{sensor_name}"
                row[f"{prefix}.frame_id"] = frame_info["frame_id"]
                row[f"{prefix}.host_time"] = frame_info["host_time"]
                row[f"{prefix}.device_time"] = frame_info.get("device_time")
                frame = frame_index.get(sensor_name, {}).get(frame_info["frame_id"])
                if frame is not None:
                    self._flatten_mapping(row, prefix, frame.payload)
                    _attach_training_friendly_media(
                        row=row,
                        reader=reader,
                        feature_overrides=feature_overrides,
                        sensor_name=sensor_name,
                        payload=frame.payload,
                        row_index=row_index,
                        frame_id=frame.frame_id,
                        images_dir=images_dir,
                        audio_dir=audio_dir,
                        fps=align_rate_hz,
                    )
            if current_compensation:
                self._flatten_mapping(row, "observation.gravity_compensation", current_compensation)
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
                        "position": action_vector[:3],
                        "quaternion": action_vector[3:7],
                    },
                    "gripper": {
                        "position_delta": action_vector[7],
                    },
                },
            )
            state_map = _build_observation_state_map(
                record=record,
                frame_index=frame_index,
                trajectory=sample.current_trajectory,
                compensation=current_compensation,
            )
            row_state_maps.append(state_map)
            for field_name in state_map:
                if field_name not in state_field_order:
                    state_field_order.append(field_name)
            if sample.aligned_index < len(row_annotations):
                self._flatten_mapping(row, "", row_annotations[sample.aligned_index])
            rows.append(row)

        if rows:
            feature_overrides["observation.state"] = {
                "dtype": "float32",
                "shape": [len(state_field_order)],
                "names": state_field_order,
            }
            for row, state_map in zip(rows, row_state_maps):
                row["observation.state"] = [float(state_map.get(field_name, 0.0)) for field_name in state_field_order]

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
            json.dumps(self._build_info(reader, rows, feature_overrides), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ExportResult(export_format=self.export_format, output_path=target)

    def _build_info(
        self,
        reader: SessionReader,
        rows: list[dict[str, Any]],
        feature_overrides: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        features = {}
        for key in rows[0].keys() if rows else []:
            if key in feature_overrides:
                features[key] = feature_overrides[key]
                continue
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


def _attach_training_friendly_media(
    *,
    row: dict[str, Any],
    reader: SessionReader,
    feature_overrides: dict[str, dict[str, Any]],
    sensor_name: str,
    payload: dict[str, Any],
    row_index: int,
    frame_id: int,
    images_dir: Path,
    audio_dir: Path,
    fps: float,
) -> None:
    for payload_key, value in payload.items():
        image_feature_key = _image_feature_key(sensor_name, payload_key)
        if image_feature_key is not None and _is_artifact_reference(value):
            array = reader.load_artifact(value)
            if isinstance(array, np.ndarray) and array.ndim in {2, 3}:
                relative_path = _export_image_feature(
                    feature_key=image_feature_key,
                    array=array,
                    row_index=row_index,
                    frame_id=frame_id,
                    images_dir=images_dir,
                    channel_order=value.get("channel_order"),
                )
                row[image_feature_key] = {"path": str(relative_path), "bytes": None}
                feature_overrides.setdefault(
                    image_feature_key,
                    {
                        "dtype": "image",
                        "shape": list(array.shape),
                        "names": _image_dimension_names(array),
                        "fps": fps,
                    },
                )
        audio_feature_key = _audio_feature_key(sensor_name, payload_key)
        if audio_feature_key is not None and _is_artifact_reference(value) and value.get("storage") == "mp4_audio":
            audio = reader.load_artifact(value)
            if isinstance(audio, np.ndarray):
                sample_rate = int(value.get("sample_rate") or 48000)
                relative_path = _export_audio_feature(
                    feature_key=audio_feature_key,
                    array=audio,
                    row_index=row_index,
                    frame_id=frame_id,
                    audio_dir=audio_dir,
                    sample_rate=sample_rate,
                )
                row[audio_feature_key] = {"path": str(relative_path), "bytes": None}
                feature_overrides.setdefault(
                    audio_feature_key,
                    {
                        "dtype": "audio",
                        "shape": list(audio.shape),
                        "sample_rate": sample_rate,
                    },
                )


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


def _build_observation_state_map(
    *,
    record: dict[str, Any],
    frame_index: dict[str, dict[int, Any]],
    trajectory: TrajectoryFrame,
    compensation: dict[str, Any] | None,
) -> dict[str, float]:
    state: dict[str, float] = {}
    for sensor_name, frame_info in record.get("frames", {}).items():
        if not isinstance(frame_info, dict):
            continue
        frame_id = frame_info.get("frame_id")
        if not isinstance(frame_id, int):
            continue
        frame = frame_index.get(sensor_name, {}).get(frame_id)
        if frame is None:
            continue
        _collect_numeric_fields(f"observation.{sensor_name}", frame.payload, state)
    if compensation:
        _collect_numeric_fields("observation.gravity_compensation", compensation, state)
    _collect_numeric_fields(
        "observation.trajectory",
        {
            "position": trajectory.position,
            "quaternion": trajectory.quaternion,
        },
        state,
    )
    return state


def _collect_numeric_fields(prefix: str, value: Any, output: dict[str, float]) -> None:
    if _is_artifact_reference(value):
        return
    if isinstance(value, np.ndarray):
        _collect_numeric_fields(prefix, value.tolist(), output)
        return
    if isinstance(value, bool):
        output[prefix] = float(value)
        return
    if isinstance(value, (int, float)):
        output[prefix] = float(value)
        return
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            child_prefix = f"{prefix}.{child_key}" if prefix else str(child_key)
            _collect_numeric_fields(child_prefix, child_value, output)
        return
    if isinstance(value, list) and all(isinstance(item, (int, float, bool)) for item in value):
        for index, item in enumerate(value):
            output[f"{prefix}.{index}"] = float(item)


def _image_feature_key(sensor_name: str, payload_key: str) -> str | None:
    mapping = {
        ("camera", "color"): "observation.images.camera",
        ("gelsight", "image"): "observation.images.gelsight",
        ("realsense", "color"): "observation.images.realsense_color",
        ("realsense", "depth"): "observation.images.realsense_depth",
        ("realsense", "aligned_depth_to_color"): "observation.images.realsense_aligned_depth_to_color",
        ("realsense", "ir1"): "observation.images.realsense_ir1",
        ("realsense", "ir2"): "observation.images.realsense_ir2",
    }
    return mapping.get((sensor_name, payload_key))


def _audio_feature_key(sensor_name: str, payload_key: str) -> str | None:
    if sensor_name == "microphone" and payload_key == "audio":
        return "observation.audios.microphone"
    return None


def _is_artifact_reference(value: Any) -> bool:
    return isinstance(value, dict) and {"path", "storage", "shape", "dtype"} <= set(value.keys())


def _image_dimension_names(array: np.ndarray) -> list[str]:
    if array.ndim == 2:
        return ["height", "width"]
    if array.ndim == 3:
        return ["height", "width", "channel"]
    return [f"dim_{index}" for index in range(array.ndim)]


def _export_image_feature(
    *,
    feature_key: str,
    array: np.ndarray,
    row_index: int,
    frame_id: int,
    images_dir: Path,
    channel_order: str | None,
) -> Path:
    if cv2 is None:
        raise RuntimeError("未安装 opencv-python，无法导出训练友好的 LeRobot 图像特征")
    suffix = ".png" if array.dtype == np.uint8 else ".tiff"
    relative_dir = Path("images") / feature_key.removeprefix("observation.images.").replace(".", "/")
    target_dir = images_dir / feature_key.removeprefix("observation.images.").replace(".", "/")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"row_{row_index:06d}_frame_{frame_id:06d}{suffix}"
    image = np.asarray(array)
    if image.ndim == 3 and image.shape[2] in {3, 4} and channel_order in {"rgb", "rgba"}:
        if image.shape[2] == 3:
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        else:
            image = cv2.cvtColor(image, cv2.COLOR_RGBA2BGRA)
    if not cv2.imwrite(str(target_path), image):
        raise RuntimeError(f"写入 LeRobot 图像特征失败: {target_path}")
    return relative_dir / target_path.name


def _export_audio_feature(
    *,
    feature_key: str,
    array: np.ndarray,
    row_index: int,
    frame_id: int,
    audio_dir: Path,
    sample_rate: int,
) -> Path:
    relative_dir = Path("audio") / feature_key.removeprefix("observation.audios.").replace(".", "/")
    target_dir = audio_dir / feature_key.removeprefix("observation.audios.").replace(".", "/")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"row_{row_index:06d}_frame_{frame_id:06d}.wav"
    audio = _normalize_audio_array(array)
    audio_int16 = np.clip(np.rint(audio * 32767.0), -32768, 32767).astype(np.int16)
    channels = 1 if audio_int16.ndim == 1 else audio_int16.shape[1]
    interleaved = audio_int16 if audio_int16.ndim == 1 else audio_int16.reshape(-1, channels)
    with wave.open(str(target_path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(interleaved.tobytes())
    return relative_dir / target_path.name


def _normalize_audio_array(array: np.ndarray) -> np.ndarray:
    audio = np.asarray(array)
    if audio.dtype.kind == "f":
        return np.clip(audio.astype(np.float32, copy=False), -1.0, 1.0)
    if audio.dtype.kind in {"i", "u"}:
        max_value = max(abs(np.iinfo(audio.dtype).min), np.iinfo(audio.dtype).max)
        return audio.astype(np.float32) / float(max_value)
    return audio.astype(np.float32)


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
