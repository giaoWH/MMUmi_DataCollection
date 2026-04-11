from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdk.constants import SDK_SCHEMA_VERSION


@dataclass(frozen=True)
class StreamManifest:
    sensor_name: str
    sensor_type: str
    modality: str
    frames_path: str
    artifacts_dir: str
    storage_mode: str = "artifact_stream"
    media_path: str | None = None
    media_role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_name": self.sensor_name,
            "sensor_type": self.sensor_type,
            "modality": self.modality,
            "frames_path": self.frames_path,
            "artifacts_dir": self.artifacts_dir,
            "storage_mode": self.storage_mode,
            "media_path": self.media_path,
            "media_role": self.media_role,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StreamManifest":
        return cls(
            sensor_name=payload["sensor_name"],
            sensor_type=payload["sensor_type"],
            modality=payload["modality"],
            frames_path=payload["frames_path"],
            artifacts_dir=payload["artifacts_dir"],
            storage_mode=payload.get("storage_mode", "artifact_stream"),
            media_path=payload.get("media_path"),
            media_role=payload.get("media_role"),
            metadata=payload.get("metadata", {}),
        )


@dataclass(frozen=True)
class SessionManifest:
    schema_version: str
    session_id: str
    started_at: float
    session_dir: str
    task_name: str
    task_slug: str
    config: dict[str, Any]
    sensors: dict[str, StreamManifest]
    notes: dict[str, Any] = field(default_factory=dict)
    aligned_path: str = "aligned/frames.jsonl"
    trajectory_path: str = "trajectory/frames.jsonl"
    exports_dir: str = "exports"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "session_dir": self.session_dir,
            "task_name": self.task_name,
            "task_slug": self.task_slug,
            "config": self.config,
            "sensors": {
                name: stream.to_dict() for name, stream in self.sensors.items()
            },
            "notes": self.notes,
            "aligned_path": self.aligned_path,
            "trajectory_path": self.trajectory_path,
            "exports_dir": self.exports_dir,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionManifest":
        return cls(
            schema_version=payload.get("schema_version", SDK_SCHEMA_VERSION),
            session_id=payload["session_id"],
            started_at=payload["started_at"],
            session_dir=payload.get("session_dir", ""),
            task_name=payload["task_name"],
            task_slug=payload["task_slug"],
            config=payload.get("config", {}),
            sensors={
                name: StreamManifest.from_dict(stream_payload)
                for name, stream_payload in payload.get("sensors", {}).items()
            },
            notes=payload.get("notes", {}),
            aligned_path=payload.get("aligned_path", "aligned/frames.jsonl"),
            trajectory_path=payload.get("trajectory_path", "trajectory/frames.jsonl"),
            exports_dir=payload.get("exports_dir", "exports"),
        )


def manifest_path_for(session_dir: str | Path) -> Path:
    return Path(session_dir) / "manifest.json"
