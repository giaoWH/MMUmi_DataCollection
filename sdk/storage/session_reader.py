from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from sdk.session_naming import stream_frames_path
from sdk.core.frame import FrameTime, SensorFrame, TrajectoryFrame
from sdk.storage.media_io import MediaArtifactReader, media_path_for_sensor, media_role_for_sensor
from sdk.storage.schema import SessionManifest, manifest_path_for
from sdk.time_utils import format_wall_time

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


class SessionReader:
    def __init__(self, session_dir: str | Path) -> None:
        self.session_dir = Path(session_dir)
        self.manifest = self._load_manifest()
        self._media_reader = MediaArtifactReader()

    def sensor_names(self) -> list[str]:
        return list(self.manifest.sensors.keys())

    def annotations_dir(self) -> Path:
        return self.session_dir / "annotations"

    def has_annotations(self) -> bool:
        return self.annotations_dir().exists()

    def quality_dir(self) -> Path:
        return self.session_dir / "quality"

    def trajectory_qc_report_path(self) -> Path | None:
        default_path = self.quality_dir() / "trajectory_qc.json"
        if default_path.exists():
            return default_path
        for candidate in self.session_dir.rglob("*.json"):
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if _looks_like_trajectory_qc_report(payload):
                return candidate
        return None

    def has_trajectory_qc_report(self) -> bool:
        return self.trajectory_qc_report_path() is not None

    def summary(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.manifest.schema_version,
            "session_id": self.manifest.session_id,
            "started_at": format_wall_time(self.manifest.started_at),
            "task_name": self.manifest.task_name,
            "task_slug": self.manifest.task_slug,
            "sensor_names": self.sensor_names(),
            "aligned_path": self.manifest.aligned_path,
            "trajectory_path": self.manifest.trajectory_path,
        }
        if self.has_annotations():
            payload["annotation_summary"] = self.annotation_summary()
        if self.has_trajectory_qc_report():
            payload["trajectory_qc_summary"] = self.trajectory_qc_summary()
        return payload

    def load_annotation_bundle(self) -> dict[str, Any] | None:
        if not self.has_annotations():
            return None
        from sdk.annotations import AnnotationService

        service = AnnotationService(self.session_dir)
        return service.load_bundle().to_dict()

    def load_annotation_schema(self) -> dict[str, Any] | None:
        bundle = self.load_annotation_bundle()
        if bundle is None:
            return None
        return bundle["schema"]

    def load_session_annotation(self) -> dict[str, Any] | None:
        bundle = self.load_annotation_bundle()
        if bundle is None:
            return None
        return bundle["session"]

    def iter_span_annotations(self) -> Iterator[dict[str, Any]]:
        bundle = self.load_annotation_bundle()
        if bundle is None:
            return
        yield from bundle["spans"]

    def iter_keyframe_annotations(self) -> Iterator[dict[str, Any]]:
        bundle = self.load_annotation_bundle()
        if bundle is None:
            return
        yield from bundle["keyframes"]

    def annotation_summary(self) -> dict[str, Any]:
        if not self.has_annotations():
            return {
                "exists": False,
                "schema_version": None,
                "session_fields": [],
                "span_count": 0,
                "keyframe_count": 0,
            }
        from sdk.annotations import AnnotationService

        service = AnnotationService(self.session_dir)
        return service.summary()

    def load_trajectory_qc_report(self) -> dict[str, Any] | None:
        path = self.trajectory_qc_report_path()
        if path is None:
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def trajectory_qc_summary(self) -> dict[str, Any]:
        report = self.load_trajectory_qc_report()
        report_path = self.trajectory_qc_report_path()
        if report is None:
            return {
                "exists": False,
                "path": None,
                "session_status": None,
                "summary": None,
                "session_flags": [],
            }
        return {
            "exists": True,
            "path": str(path_relative_to_session(self.session_dir, report_path)) if report_path is not None else None,
            "session_status": report.get("session_status"),
            "summary": report.get("summary"),
            "session_flags": report.get("session_flags", []),
        }

    def iter_sensor_frames(
        self,
        sensor_name: str,
        *,
        load_payload: bool = False,
    ) -> Iterator[SensorFrame]:
        stream = self.manifest.sensors[sensor_name]
        path = self.session_dir / stream.frames_path
        if not path.exists():
            return
        for record in self._iter_jsonl(path):
            yield self._build_sensor_frame(record, load_payload=load_payload)

    def iter_aligned_records(self) -> Iterator[dict[str, Any]]:
        path = self.session_dir / self.manifest.aligned_path
        if not path.exists():
            return
        yield from self._iter_jsonl(path)

    def iter_trajectory_frames(self) -> Iterator[TrajectoryFrame]:
        path = self.session_dir / self.manifest.trajectory_path
        if not path.exists():
            return
        for record in self._iter_jsonl(path):
            time_payload = record["time"]
            yield TrajectoryFrame(
                source=record["source"],
                frame_id=record["frame_id"],
                time=FrameTime(
                    host_time=time_payload["host_time"],
                    host_time_ns=time_payload.get("host_time_ns"),
                    monotonic_time=time_payload["monotonic_time"],
                    monotonic_time_ns=time_payload.get("monotonic_time_ns"),
                    device_time=time_payload.get("device_time"),
                    device_time_ns=time_payload.get("device_time_ns"),
                    aligned_time=time_payload.get("aligned_time"),
                    aligned_time_ns=time_payload.get("aligned_time_ns"),
                    host_arrival_time=time_payload.get("host_arrival_time"),
                    host_arrival_time_ns=time_payload.get("host_arrival_time_ns"),
                    host_read_start_time_ns=time_payload.get("host_read_start_time_ns"),
                    host_read_end_time_ns=time_payload.get("host_read_end_time_ns"),
                ),
                position=record["position"],
                quaternion=record["quaternion"],
                tracking_state=record["tracking_state"],
                metadata=record.get("metadata", {}),
            )

    def load_artifact(self, reference: dict[str, Any]) -> Any:
        path = self.session_dir / reference["path"]
        storage = reference.get("storage")
        if storage == "npy":
            return np.load(path, allow_pickle=False)
        if storage == "png":
            if cv2 is None:
                raise RuntimeError("未安装 opencv-python，无法读取 PNG artifact")
            image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if image is None:
                raise FileNotFoundError(f"无法读取 PNG artifact: {path}")
            if (
                reference.get("channel_order") == "rgb"
                and image.ndim == 3
                and image.shape[2] == 3
            ):
                return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            return image
        if storage in {"mp4_frame", "mp4_audio"}:
            return self._media_reader.load_reference({**reference, "path": str(path)})
        raise ValueError(f"不支持的 artifact 存储格式: {storage}")

    def _load_manifest(self) -> SessionManifest:
        path = manifest_path_for(self.session_dir)
        if path.exists():
            try:
                return SessionManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except KeyError as exc:
                raise FileNotFoundError(
                    f"session manifest 缺少 2.1.0 所需的 task_name/task_slug 字段，不支持旧命名 session: {self.session_dir}"
                ) from exc

        meta_path = self.session_dir / "meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"未找到 manifest.json 或 meta.json: {self.session_dir}")
        meta_payload = json.loads(meta_path.read_text(encoding="utf-8"))
        task_name = meta_payload.get("task_name")
        task_slug = meta_payload.get("task_slug")
        if not isinstance(task_name, str) or not isinstance(task_slug, str):
            raise FileNotFoundError(
                f"未找到可读取的新 schema manifest.json，且 meta.json 缺少 task_name/task_slug: {self.session_dir}"
            )
        sensors = {}
        for sensor_name, metadata in meta_payload.get("sensors", {}).items():
            modality = metadata.get("modality", "unknown")
            sensors[sensor_name] = {
                "sensor_name": sensor_name,
                "sensor_type": metadata.get("sensor_type", sensor_name),
                "modality": modality,
                "frames_path": stream_frames_path(sensor_name, modality, task_slug),
                "artifacts_dir": f"streams/{sensor_name}/artifacts",
                "storage_mode": (
                    "indexed_media"
                    if media_path_for_sensor(sensor_name, modality, task_slug)
                    else "artifact_stream"
                ),
                "media_path": media_path_for_sensor(sensor_name, modality, task_slug),
                "media_role": media_role_for_sensor(sensor_name, modality),
                "metadata": metadata,
            }
        payload = {
            "schema_version": meta_payload.get("schema_version", "1.0.0"),
            "session_id": meta_payload["session_id"],
            "started_at": meta_payload["started_at"],
            "session_dir": str(self.session_dir),
            "task_name": task_name,
            "task_slug": task_slug,
            "config": meta_payload.get("config", {}),
            "sensors": sensors,
            "notes": meta_payload.get("notes", {}),
        }
        return SessionManifest.from_dict(payload)

    def _iter_jsonl(self, path: Path) -> Iterator[dict[str, Any]]:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def _build_sensor_frame(
        self,
        record: dict[str, Any],
        *,
        load_payload: bool,
    ) -> SensorFrame:
        time_payload = record["time"]
        payload = self._deserialize_payload(record["payload"], load_payload=load_payload)
        return SensorFrame(
            sensor_name=record["sensor_name"],
            sensor_type=record["sensor_type"],
            modality=record["modality"],
            frame_id=record["frame_id"],
            time=FrameTime(
                host_time=time_payload["host_time"],
                host_time_ns=time_payload.get("host_time_ns"),
                monotonic_time=time_payload["monotonic_time"],
                monotonic_time_ns=time_payload.get("monotonic_time_ns"),
                device_time=time_payload.get("device_time"),
                device_time_ns=time_payload.get("device_time_ns"),
                aligned_time=time_payload.get("aligned_time"),
                aligned_time_ns=time_payload.get("aligned_time_ns"),
                host_arrival_time=time_payload.get("host_arrival_time"),
                host_arrival_time_ns=time_payload.get("host_arrival_time_ns"),
                host_read_start_time_ns=time_payload.get("host_read_start_time_ns"),
                host_read_end_time_ns=time_payload.get("host_read_end_time_ns"),
            ),
            payload=payload,
            metadata=record.get("metadata", {}),
        )

    def _deserialize_payload(self, payload: dict[str, Any], *, load_payload: bool) -> dict[str, Any]:
        return {
            key: self._deserialize_value(value, load_payload=load_payload)
            for key, value in payload.items()
        }

    def _deserialize_value(self, value: Any, *, load_payload: bool) -> Any:
        if isinstance(value, dict) and {"path", "storage", "shape", "dtype"} <= set(value.keys()):
            if not load_payload:
                return value
            return self.load_artifact(value)
        if isinstance(value, dict):
            return {
                child_key: self._deserialize_value(child_value, load_payload=load_payload)
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [self._deserialize_value(item, load_payload=load_payload) for item in value]
        return value


def path_relative_to_session(session_dir: Path, path: Path) -> Path:
    try:
        return path.relative_to(session_dir)
    except ValueError:
        return path


def _looks_like_trajectory_qc_report(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and {"session_status", "summary", "thresholds", "step_results"} <= set(payload.keys())
    )
