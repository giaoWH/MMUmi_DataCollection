from __future__ import annotations

from dataclasses import dataclass
import hashlib
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
from sdk.storage import SessionReader, discover_session_dirs, looks_like_session_dir

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


@dataclass
class LeRobotEpisodeFragment:
    session_dir: Path
    session_id: str
    task_name: str
    align_rate_hz: float
    episode_index: int
    session_annotations: dict[str, Any]
    rows: list[dict[str, Any]]
    row_state_maps: list[dict[str, float]]
    feature_overrides: dict[str, dict[str, Any]]
    split: str | None = None


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
        session_dir = Path(session_dir)
        target = Path(output_path) if output_path else session_dir / "exports" / "lerobot"
        paths = _prepare_lerobot_output_dirs(target)

        reader = SessionReader(session_dir)
        fragment = self._build_episode_fragment(
            reader=reader,
            session_dir=session_dir,
            images_dir=paths.images_dir,
            audio_dir=paths.audio_dir,
            global_row_start=0,
            episode_index=0,
            include_source_session_id=False,
            split=None,
        )
        payload = self._finalize_dataset([fragment])
        self._write_dataset(
            target=target,
            rows=payload.rows,
            episodes=payload.episodes,
            tasks=payload.tasks,
            feature_overrides=payload.feature_overrides,
            fps=payload.fps,
            splits={"train": "0:1"},
        )
        return ExportResult(export_format=self.export_format, output_path=target)

    def export_sessions_dir(self, sessions_dir: str | Path, output_path: str | Path | None = None) -> ExportResult:
        sessions_dir = Path(sessions_dir)
        target = Path(output_path) if output_path else sessions_dir / "exports" / "lerobot_merged"
        scanned_dirs = discover_session_dirs(sessions_dir)
        excluded_dirs = {target}
        if target.parent.parent == sessions_dir:
            excluded_dirs.add(target.parent)
        scanned_dirs = [
            path for path in scanned_dirs
            if path not in excluded_dirs and not any(parent in excluded_dirs for parent in path.parents)
        ]
        paths = _prepare_lerobot_output_dirs(target)
        skipped: list[dict[str, Any]] = []
        candidates: list[tuple[str, str, Path, SessionReader]] = []
        for path in scanned_dirs:
            if not looks_like_session_dir(path):
                skipped.append(
                    {
                        "session_dir": str(path.relative_to(sessions_dir)),
                        "session_id": None,
                        "reason": "not_a_session_dir",
                    }
                )
                continue
            try:
                reader = SessionReader(path)
            except Exception as exc:
                skipped.append(
                    {
                        "session_dir": path.name,
                        "session_id": None,
                        "reason": f"session_read_error: {exc}",
                    }
                )
                continue
            session_id = reader.manifest.session_id
            missing_reason = _missing_required_session_component(path, reader)
            if missing_reason is not None:
                skipped.append(
                    {
                        "session_dir": path.name,
                        "session_id": session_id,
                        "reason": missing_reason,
                    }
                )
                continue
            candidates.append((_split_for_session_id(session_id), session_id, path, reader))

        split_order = {"train": 0, "val": 1, "test": 2}
        candidates.sort(key=lambda item: (split_order[item[0]], item[1], item[2].name))

        fragments: list[LeRobotEpisodeFragment] = []
        next_row_index = 0
        next_episode_index = 0
        for split_name, session_id, path, reader in candidates:
            try:
                fragment = self._build_episode_fragment(
                    reader=reader,
                    session_dir=path,
                    images_dir=paths.images_dir,
                    audio_dir=paths.audio_dir,
                    global_row_start=next_row_index,
                    episode_index=next_episode_index,
                    include_source_session_id=True,
                    split=split_name,
                )
            except Exception as exc:
                skipped.append(
                    {
                        "session_dir": path.name,
                        "session_id": session_id,
                        "reason": f"export_error: {exc}",
                    }
                )
                continue

            if not fragment.rows:
                skipped.append(
                    {
                        "session_dir": path.name,
                        "session_id": session_id,
                        "reason": "no_valid_rows",
                    }
                )
                continue

            fragments.append(fragment)
            next_row_index += len(fragment.rows)
            next_episode_index += 1

        payload = self._finalize_dataset(fragments)
        split_counts = _build_split_counts(fragments)
        splits = _build_split_ranges(split_counts)
        self._write_dataset(
            target=target,
            rows=payload.rows,
            episodes=payload.episodes,
            tasks=payload.tasks,
            feature_overrides=payload.feature_overrides,
            fps=payload.fps,
            splits=splits,
        )
        merge_report = {
            "scanned_directories": len(scanned_dirs),
            "valid_sessions": len(fragments),
            "skipped_sessions": len(skipped),
            "skipped": skipped,
            "splits": {
                split_name: {
                    "episodes": split_counts[split_name]["episodes"],
                    "rows": split_counts[split_name]["rows"],
                }
                for split_name in ("train", "val", "test")
            },
        }
        (paths.meta_dir / "merge_report.json").write_text(
            json.dumps(merge_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ExportResult(export_format=self.export_format, output_path=target)

    def _build_episode_fragment(
        self,
        *,
        reader: SessionReader,
        session_dir: Path,
        images_dir: Path,
        audio_dir: Path,
        global_row_start: int,
        episode_index: int,
        include_source_session_id: bool,
        split: str | None,
    ) -> LeRobotEpisodeFragment:
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

        rows: list[dict[str, Any]] = []
        row_state_maps: list[dict[str, float]] = []
        session_id = reader.manifest.session_id
        task_name = self._resolve_task_name(session_annotations)
        for local_row_index, sample in enumerate(samples):
            record = sample.aligned_record
            current_compensation = record.get("metadata", {}).get("gravity_compensation")
            row_index = global_row_start + local_row_index
            row: dict[str, Any] = {
                "index": row_index,
                "episode_index": episode_index,
                "frame_index": local_row_index,
                "timestamp": record["aligned_time"],
                "task_index": 0,
            }
            if include_source_session_id:
                row["source_session_id"] = session_id
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
            if sample.aligned_index < len(row_annotations):
                self._flatten_mapping(row, "", row_annotations[sample.aligned_index])
            rows.append(row)

        return LeRobotEpisodeFragment(
            session_dir=session_dir,
            session_id=session_id,
            task_name=task_name,
            align_rate_hz=align_rate_hz,
            episode_index=episode_index,
            session_annotations=session_annotations,
            rows=rows,
            row_state_maps=row_state_maps,
            feature_overrides=feature_overrides,
            split=split,
        )

    def _finalize_dataset(self, fragments: list[LeRobotEpisodeFragment]) -> "_LeRobotDatasetPayload":
        feature_overrides: dict[str, dict[str, Any]] = {}
        global_state_field_order: list[str] = []
        task_names: list[str] = []
        for fragment in fragments:
            for key, value in fragment.feature_overrides.items():
                feature_overrides.setdefault(key, value)
            for state_map in fragment.row_state_maps:
                for field_name in state_map:
                    if field_name not in global_state_field_order:
                        global_state_field_order.append(field_name)
            if fragment.task_name not in task_names:
                task_names.append(fragment.task_name)

        if global_state_field_order:
            feature_overrides["observation.state"] = {
                "dtype": "float32",
                "shape": [len(global_state_field_order)],
                "names": global_state_field_order,
            }

        task_to_index = {task_name: index for index, task_name in enumerate(task_names)}
        rows: list[dict[str, Any]] = []
        episodes: list[dict[str, Any]] = []
        for fragment in fragments:
            task_index = task_to_index[fragment.task_name]
            from_index = rows[-1]["index"] + 1 if rows else 0
            for row, state_map in zip(fragment.rows, fragment.row_state_maps):
                row["task_index"] = task_index
                if global_state_field_order:
                    row["observation.state"] = [
                        float(state_map.get(field_name, 0.0))
                        for field_name in global_state_field_order
                    ]
                rows.append(row)
            episode_record = {
                "episode_index": fragment.episode_index,
                "length": len(fragment.rows),
                "task_index": task_index,
                "from_index": from_index,
                "to_index": from_index + len(fragment.rows),
                **fragment.session_annotations,
            }
            if fragment.rows and "source_session_id" in fragment.rows[0]:
                episode_record["source_session_id"] = fragment.session_id
            episodes.append(episode_record)

        tasks = [
            {
                "task_index": task_to_index[task_name],
                "task": task_name,
            }
            for task_name in task_names
        ]
        fps = fragments[0].align_rate_hz if fragments else 0.0
        return _LeRobotDatasetPayload(
            rows=rows,
            episodes=episodes,
            tasks=tasks,
            feature_overrides=feature_overrides,
            fps=fps,
        )

    def _write_dataset(
        self,
        *,
        target: Path,
        rows: list[dict[str, Any]],
        episodes: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        feature_overrides: dict[str, dict[str, Any]],
        fps: float,
        splits: dict[str, str],
    ) -> None:
        paths = _prepare_lerobot_output_dirs(target)
        data_table = pa.Table.from_pylist(rows) if rows else pa.table({"index": pa.array([], type=pa.int64())})
        pq.write_table(data_table, paths.data_dir / "file-000.parquet")

        if episodes:
            episode_table = pa.Table.from_pylist(episodes)
        else:
            episode_table = pa.table({"episode_index": pa.array([], type=pa.int64())})
        pq.write_table(episode_table, paths.episodes_dir / "file-000.parquet")

        tasks_payload = "\n".join(json.dumps(task, ensure_ascii=False) for task in tasks)
        if tasks_payload:
            tasks_payload += "\n"
        (paths.meta_dir / "tasks.jsonl").write_text(tasks_payload, encoding="utf-8")
        (paths.meta_dir / "stats.json").write_text(
            json.dumps(self._build_stats(rows), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (paths.meta_dir / "info.json").write_text(
            json.dumps(
                self._build_info(
                    rows=rows,
                    feature_overrides=feature_overrides,
                    fps=fps,
                    total_episodes=len(episodes),
                    total_tasks=len(tasks),
                    splits=splits,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _build_info(
        self,
        *,
        rows: list[dict[str, Any]],
        feature_overrides: dict[str, dict[str, Any]],
        fps: float,
        total_episodes: int,
        total_tasks: int,
        splits: dict[str, str],
    ) -> dict[str, Any]:
        features: dict[str, dict[str, Any]] = {}
        ordered_keys: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in ordered_keys:
                    ordered_keys.append(key)
        for key in ordered_keys:
            if key in feature_overrides:
                features[key] = feature_overrides[key]
                continue
            sample = next((row[key] for row in rows if key in row and row[key] is not None), None)
            features[key] = {"dtype": self._infer_feature_dtype(key, sample), "shape": [1]}

        return {
            "codebase_version": "v3.0",
            "robot_type": "unknown",
            "total_episodes": total_episodes,
            "total_frames": len(rows),
            "total_tasks": total_tasks,
            "chunks_size": len(rows),
            "fps": float(fps or 0.0),
            "splits": splits,
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
            "features": features,
        }

    def _build_stats(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        stats: dict[str, dict[str, float | list[float]]] = {}
        numeric_keys: dict[str, list[np.ndarray]] = {}
        for row in rows:
            for key, value in row.items():
                if key in {"index", "episode_index", "frame_index", "task_index"}:
                    continue
                sample = self._normalize_stats_sample(value)
                if sample is None:
                    continue
                numeric_keys.setdefault(key, []).append(sample)
        for key, values in numeric_keys.items():
            if values:
                stacked = np.stack(values, axis=0)
                min_values = stacked.min(axis=0)
                max_values = stacked.max(axis=0)
                mean_values = stacked.mean(axis=0)
                std_values = stacked.std(axis=0)
                stats[key] = {
                    "min": self._serialize_stats_value(min_values),
                    "max": self._serialize_stats_value(max_values),
                    "mean": self._serialize_stats_value(mean_values),
                    "std": self._serialize_stats_value(std_values),
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

    def _normalize_stats_sample(self, value: Any) -> np.ndarray | None:
        if isinstance(value, bool):
            return np.asarray([float(value)], dtype=np.float64)
        if isinstance(value, (int, float)):
            return np.asarray([float(value)], dtype=np.float64)
        if isinstance(value, np.ndarray):
            if value.ndim != 1 or value.dtype.kind not in {"b", "i", "u", "f"}:
                return None
            return value.astype(np.float64, copy=False)
        if isinstance(value, (list, tuple)):
            if not value or not all(isinstance(item, (bool, int, float)) for item in value):
                return None
            return np.asarray([float(item) for item in value], dtype=np.float64)
        return None

    def _serialize_stats_value(self, value: np.ndarray) -> float | list[float]:
        if value.ndim == 0:
            return float(value.item())
        if value.shape == (1,):
            return float(value[0])
        return value.astype(np.float64).tolist()

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


@dataclass(frozen=True)
class _LeRobotOutputPaths:
    meta_dir: Path
    episodes_dir: Path
    data_dir: Path
    images_dir: Path
    audio_dir: Path


@dataclass(frozen=True)
class _LeRobotDatasetPayload:
    rows: list[dict[str, Any]]
    episodes: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    feature_overrides: dict[str, dict[str, Any]]
    fps: float


def _prepare_lerobot_output_dirs(target: Path) -> _LeRobotOutputPaths:
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
    return _LeRobotOutputPaths(
        meta_dir=meta_dir,
        episodes_dir=episodes_dir,
        data_dir=data_dir,
        images_dir=images_dir,
        audio_dir=audio_dir,
    )


def _looks_like_session_dir(path: Path) -> bool:
    return looks_like_session_dir(path)


def _missing_required_session_component(path: Path, reader: SessionReader) -> str | None:
    aligned_path = path / reader.manifest.aligned_path
    if not aligned_path.exists():
        return "missing_aligned"
    trajectory_path = path / reader.manifest.trajectory_path
    if not trajectory_path.exists():
        return "missing_trajectory"
    return None


def _split_for_session_id(session_id: str) -> str:
    digest = hashlib.sha1(session_id.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], byteorder="big", signed=False) % 10
    if bucket < 8:
        return "train"
    if bucket == 8:
        return "val"
    return "test"


def _build_split_counts(fragments: list[LeRobotEpisodeFragment]) -> dict[str, dict[str, int]]:
    counts = {
        "train": {"episodes": 0, "rows": 0},
        "val": {"episodes": 0, "rows": 0},
        "test": {"episodes": 0, "rows": 0},
    }
    for fragment in fragments:
        split_name = fragment.split or "train"
        counts[split_name]["episodes"] += 1
        counts[split_name]["rows"] += len(fragment.rows)
    return counts


def _build_split_ranges(split_counts: dict[str, dict[str, int]]) -> dict[str, str]:
    cursor = 0
    ranges: dict[str, str] = {}
    for split_name in ("train", "val", "test"):
        start = cursor
        cursor += split_counts[split_name]["episodes"]
        ranges[split_name] = f"{start}:{cursor}"
    return ranges


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
