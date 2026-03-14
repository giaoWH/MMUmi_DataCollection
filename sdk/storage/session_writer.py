from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from sdk.core.frame import AlignedFrame, SensorFrame
from sdk.core.frame import TrajectoryFrame
from sdk.core.session import SessionInfo
from sdk.storage.schema import SessionManifest, StreamManifest, manifest_path_for

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


class SessionWriter:
    def __init__(self, session_info: SessionInfo) -> None:
        self.session_info = session_info
        self.base_dir = session_info.output_dir
        self.streams_dir = self.base_dir / "streams"
        self.aligned_dir = self.base_dir / "aligned"
        self.trajectory_dir = self.base_dir / "trajectory"
        self.exports_dir = self.base_dir / "exports"
        self.sensor_dirs: dict[str, Path] = {}
        self.manifest = self._build_manifest()
        self._prepare_layout()
        self._write_meta()
        self._write_manifest()

    @classmethod
    def open_existing(cls, session_dir: str | Path) -> "SessionWriter":
        session_dir = Path(session_dir)
        manifest = SessionManifest.from_dict(
            json.loads(manifest_path_for(session_dir).read_text(encoding="utf-8"))
        )
        writer = cls.__new__(cls)
        writer.session_info = None
        writer.base_dir = session_dir
        writer.streams_dir = session_dir / "streams"
        writer.aligned_dir = session_dir / "aligned"
        writer.trajectory_dir = session_dir / "trajectory"
        writer.exports_dir = session_dir / "exports"
        writer.sensor_dirs = {
            sensor_name: writer.streams_dir / sensor_name
            for sensor_name in manifest.sensors
        }
        writer.manifest = manifest
        writer._prepare_existing_layout()
        return writer

    def write_sensor_frame(self, frame: SensorFrame) -> None:
        sensor_dir = self._ensure_sensor_dir(frame.sensor_name)
        record = {
            "sensor_name": frame.sensor_name,
            "sensor_type": frame.sensor_type,
            "modality": frame.modality,
            "frame_id": frame.frame_id,
            "time": {
                "host_time": frame.time.host_time,
                "monotonic_time": frame.time.monotonic_time,
                "device_time": frame.time.device_time,
                "aligned_time": frame.time.aligned_time,
            },
            "payload": self._serialize_payload(
                sensor_dir=sensor_dir,
                frame_id=frame.frame_id,
                payload=frame.payload,
            ),
            "metadata": self._to_jsonable(frame.metadata),
        }
        self._append_jsonl(sensor_dir / "frames.jsonl", record)

    def write_aligned_frame(self, aligned: AlignedFrame) -> None:
        record = {
            "sequence_id": aligned.sequence_id,
            "aligned_time": aligned.aligned_time,
            "missing_sensors": aligned.missing_sensors,
            "age_by_sensor": aligned.age_by_sensor,
            "frames": {
                name: {
                    "frame_id": frame.frame_id,
                    "host_time": frame.time.host_time,
                    "device_time": frame.time.device_time,
                }
                for name, frame in aligned.frames.items()
            },
            "metadata": self._to_jsonable(aligned.metadata),
        }
        self._append_jsonl(self.aligned_dir / "frames.jsonl", record)

    def write_trajectory_frame(self, frame: TrajectoryFrame) -> None:
        record = {
            "source": frame.source,
            "frame_id": frame.frame_id,
            "time": {
                "host_time": frame.time.host_time,
                "monotonic_time": frame.time.monotonic_time,
                "device_time": frame.time.device_time,
                "aligned_time": frame.time.aligned_time,
            },
            "position": frame.position,
            "quaternion": frame.quaternion,
            "tracking_state": frame.tracking_state,
            "metadata": self._to_jsonable(frame.metadata),
        }
        self._append_jsonl(self.trajectory_dir / "frames.jsonl", record)

    def close(self) -> None:
        pass

    def _prepare_existing_layout(self) -> None:
        self.streams_dir.mkdir(parents=True, exist_ok=True)
        self.aligned_dir.mkdir(parents=True, exist_ok=True)
        self.trajectory_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        for sensor_dir in self.sensor_dirs.values():
            (sensor_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    def _prepare_layout(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.streams_dir.mkdir(parents=True, exist_ok=True)
        self.aligned_dir.mkdir(parents=True, exist_ok=True)
        self.trajectory_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        for sensor_name in self.session_info.sensors:
            self._ensure_sensor_dir(sensor_name)

    def _ensure_sensor_dir(self, sensor_name: str) -> Path:
        if sensor_name not in self.sensor_dirs:
            sensor_dir = self.streams_dir / sensor_name
            (sensor_dir / "artifacts").mkdir(parents=True, exist_ok=True)
            self.sensor_dirs[sensor_name] = sensor_dir
        return self.sensor_dirs[sensor_name]

    def _write_meta(self) -> None:
        meta_path = self.base_dir / "meta.json"
        payload = {
            "schema_version": self.session_info.schema_version,
            "session_id": self.session_info.session_id,
            "started_at": self.session_info.started_at,
            "config": self._to_jsonable(self.session_info.config),
            "sensors": self._to_jsonable(self.session_info.sensors),
            "notes": self._to_jsonable(self.session_info.notes),
        }
        meta_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_manifest(self) -> None:
        path = manifest_path_for(self.base_dir)
        path.write_text(
            json.dumps(self.manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _build_manifest(self) -> SessionManifest:
        streams = {
            sensor_name: StreamManifest(
                sensor_name=sensor_name,
                sensor_type=str(sensor_meta.get("sensor_type", sensor_name)),
                modality=str(sensor_meta.get("modality", "unknown")),
                frames_path=f"streams/{sensor_name}/frames.jsonl",
                artifacts_dir=f"streams/{sensor_name}/artifacts",
                metadata=self._to_jsonable(sensor_meta),
            )
            for sensor_name, sensor_meta in self.session_info.sensors.items()
        }
        return SessionManifest(
            schema_version=self.session_info.schema_version,
            session_id=self.session_info.session_id,
            started_at=self.session_info.started_at,
            session_dir=str(self.base_dir),
            config=self._to_jsonable(self.session_info.config),
            sensors=streams,
            notes=self._to_jsonable(self.session_info.notes),
            aligned_path="aligned/frames.jsonl",
            trajectory_path="trajectory/frames.jsonl",
            exports_dir="exports",
        )

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")

    def _serialize_payload(
        self,
        *,
        sensor_dir: Path,
        frame_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            key: self._serialize_value(sensor_dir, frame_id, key, value)
            for key, value in payload.items()
        }

    def _serialize_value(
        self,
        sensor_dir: Path,
        frame_id: int,
        key: str,
        value: Any,
    ) -> Any:
        if isinstance(value, np.ndarray):
            if value.ndim <= 1:
                return value.tolist()
            return self._store_array(sensor_dir, frame_id, key, value)
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {
                child_key: self._serialize_value(sensor_dir, frame_id, child_key, child_value)
                for child_key, child_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._serialize_value(sensor_dir, frame_id, key, item) for item in value]
        return value

    def _store_array(
        self,
        sensor_dir: Path,
        frame_id: int,
        key: str,
        value: np.ndarray,
    ) -> dict[str, Any]:
        artifacts_dir = sensor_dir / "artifacts"
        base_name = f"{frame_id:06d}_{key}"
        if cv2 is not None and value.dtype == np.uint8 and value.ndim in {2, 3}:
            target = artifacts_dir / f"{base_name}.png"
            cv2.imwrite(str(target), value)
            return {
                "path": str(target.relative_to(self.base_dir)),
                "storage": "png",
                "shape": list(value.shape),
                "dtype": str(value.dtype),
            }

        target = artifacts_dir / f"{base_name}.npy"
        np.save(target, value)
        return {
            "path": str(target.relative_to(self.base_dir)),
            "storage": "npy",
            "shape": list(value.shape),
            "dtype": str(value.dtype),
        }

    def _to_jsonable(self, value: Any) -> Any:
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {key: self._to_jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._to_jsonable(item) for item in value]
        return value
