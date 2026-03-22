from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from sdk.annotations import AnnotationService
from sdk.exporters.base import ExportResult, SessionExporter
from sdk.storage import SessionReader


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

        frame_index = {
            sensor_name: {
                frame.frame_id: frame
                for frame in reader.iter_sensor_frames(sensor_name, load_payload=False)
            }
            for sensor_name in reader.sensor_names()
        }
        trajectory_frames = list(reader.iter_trajectory_frames())
        aligned_records = list(reader.iter_aligned_records())
        annotation_service = AnnotationService(session_dir)
        session_annotations, row_annotations = annotation_service.build_lerobot_annotations(aligned_records)
        rows: list[dict[str, Any]] = []
        for row_index, record in enumerate(aligned_records):
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
            if row_index < len(trajectory_frames):
                trajectory = trajectory_frames[row_index]
                self._flatten_mapping(
                    row,
                    "observation.trajectory",
                    {
                        "position": trajectory.position,
                        "quaternion": trajectory.quaternion,
                        "tracking_state": trajectory.tracking_state,
                    },
                )
            if row_index < len(row_annotations):
                self._flatten_mapping(row, "", row_annotations[row_index])
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
            "chunks_size": max(len(rows), 1),
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
