from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .schema import AnnotationSchema, ANNOTATION_SCHEMA_VERSION


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnnotationStore:
    def __init__(self, session_dir: str | Path) -> None:
        self.session_dir = Path(session_dir)
        self.base_dir = self.session_dir / "annotations"
        self.manifest_path = self.base_dir / "manifest.json"
        self.schema_snapshot_path = self.base_dir / "schema.snapshot.json"
        self.session_annotation_path = self.base_dir / "session.json"
        self.spans_path = self.base_dir / "spans.jsonl"
        self.keyframes_path = self.base_dir / "keyframes.jsonl"

    def exists(self) -> bool:
        return self.base_dir.exists()

    def ensure_layout(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def load_manifest(self) -> dict[str, Any] | None:
        if not self.manifest_path.exists():
            return None
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def save_manifest(self, payload: dict[str, Any]) -> None:
        self.ensure_layout()
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def initialize_manifest(
        self,
        *,
        session_id: str,
        schema_version: str,
        annotator: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        payload = {
            "annotation_version": ANNOTATION_SCHEMA_VERSION,
            "session_id": session_id,
            "schema_version": schema_version,
            "annotator": annotator,
            "created_at": now,
            "updated_at": now,
        }
        self.save_manifest(payload)
        return payload

    def touch_manifest(self, *, schema_version: str | None = None, annotator: str | None = None) -> dict[str, Any]:
        manifest = self.load_manifest() or {}
        now = utc_now_iso()
        manifest.setdefault("annotation_version", ANNOTATION_SCHEMA_VERSION)
        manifest.setdefault("created_at", now)
        manifest["updated_at"] = now
        if schema_version is not None:
            manifest["schema_version"] = schema_version
        if annotator is not None:
            manifest["annotator"] = annotator
        self.save_manifest(manifest)
        return manifest

    def load_schema_snapshot(self) -> AnnotationSchema | None:
        if not self.schema_snapshot_path.exists():
            return None
        return AnnotationSchema.from_dict(json.loads(self.schema_snapshot_path.read_text(encoding="utf-8")))

    def save_schema_snapshot(self, schema: AnnotationSchema) -> None:
        self.ensure_layout()
        self.schema_snapshot_path.write_text(
            json.dumps(schema.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_session_annotation(self) -> dict[str, Any] | None:
        if not self.session_annotation_path.exists():
            return None
        return json.loads(self.session_annotation_path.read_text(encoding="utf-8"))

    def save_session_annotation(self, payload: dict[str, Any]) -> None:
        self.ensure_layout()
        self.session_annotation_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def iter_jsonl(self, path: Path) -> Iterator[dict[str, Any]]:
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def load_spans(self) -> list[dict[str, Any]]:
        return list(self.iter_jsonl(self.spans_path))

    def save_spans(self, payload: list[dict[str, Any]]) -> None:
        self._write_jsonl(self.spans_path, payload)

    def load_keyframes(self) -> list[dict[str, Any]]:
        return list(self.iter_jsonl(self.keyframes_path))

    def save_keyframes(self, payload: list[dict[str, Any]]) -> None:
        self._write_jsonl(self.keyframes_path, payload)

    def _write_jsonl(self, path: Path, payload: list[dict[str, Any]]) -> None:
        self.ensure_layout()
        with path.open("w", encoding="utf-8") as handle:
            for item in payload:
                handle.write(json.dumps(item, ensure_ascii=False))
                handle.write("\n")
