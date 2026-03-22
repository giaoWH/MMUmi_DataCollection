from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sdk.storage import SessionReader

from .schema import AnnotationSchema, load_annotation_schema
from .store import AnnotationStore, utc_now_iso


@dataclass(frozen=True)
class AnnotationBundle:
    manifest: dict[str, Any] | None
    schema: AnnotationSchema
    session: dict[str, Any] | None
    spans: list[dict[str, Any]]
    keyframes: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "schema": self.schema.to_dict(),
            "session": self.session,
            "spans": self.spans,
            "keyframes": self.keyframes,
        }


class AnnotationService:
    def __init__(
        self,
        session_dir: str | Path,
        *,
        schema: AnnotationSchema | None = None,
        annotator: str | None = None,
    ) -> None:
        self.session_dir = Path(session_dir)
        self.reader = SessionReader(self.session_dir)
        self.store = AnnotationStore(self.session_dir)
        self.annotator = annotator
        self._configured_schema = schema

    def ensure_initialized(self) -> AnnotationBundle:
        schema = self.store.load_schema_snapshot() or self._configured_schema or AnnotationSchema.default()
        if not self.store.load_schema_snapshot():
            self.store.save_schema_snapshot(schema)

        manifest = self.store.load_manifest()
        if manifest is None:
            self.store.initialize_manifest(
                session_id=self.reader.manifest.session_id,
                schema_version=schema.version,
                annotator=self.annotator,
            )
        return self.load_bundle()

    def load_bundle(self) -> AnnotationBundle:
        schema = self.store.load_schema_snapshot() or self._configured_schema or AnnotationSchema.default()
        return AnnotationBundle(
            manifest=self.store.load_manifest(),
            schema=schema,
            session=self.store.load_session_annotation(),
            spans=self.store.load_spans(),
            keyframes=self.store.load_keyframes(),
        )

    def load_schema(self) -> AnnotationSchema:
        return self.load_bundle().schema

    def upsert_session_annotation(self, data: dict[str, Any]) -> dict[str, Any]:
        bundle = self.ensure_initialized()
        normalized = bundle.schema.validate_annotation_data("session", data)
        existing = self.store.load_session_annotation()
        now = utc_now_iso()
        payload = {
            "session_id": self.reader.manifest.session_id,
            "updated_at": now,
            "created_at": existing.get("created_at", now) if existing else now,
            "data": normalized,
        }
        self.store.save_session_annotation(payload)
        self.store.touch_manifest(schema_version=bundle.schema.version, annotator=self.annotator)
        return payload

    def list_spans(self) -> list[dict[str, Any]]:
        return self.store.load_spans()

    def create_span(self, payload: dict[str, Any]) -> dict[str, Any]:
        bundle = self.ensure_initialized()
        item = self._normalize_span_payload(payload, schema=bundle.schema)
        spans = self.store.load_spans()
        spans.append(item)
        spans.sort(key=lambda record: (record["start_sequence_id"], record["end_sequence_id"], record["id"]))
        self.store.save_spans(spans)
        self.store.touch_manifest(schema_version=bundle.schema.version, annotator=self.annotator)
        return item

    def update_span(self, span_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        bundle = self.ensure_initialized()
        spans = self.store.load_spans()
        for index, item in enumerate(spans):
            if item["id"] != span_id:
                continue
            updated = self._normalize_span_payload(
                payload,
                schema=bundle.schema,
                existing=item,
                forced_id=span_id,
            )
            spans[index] = updated
            spans.sort(key=lambda record: (record["start_sequence_id"], record["end_sequence_id"], record["id"]))
            self.store.save_spans(spans)
            self.store.touch_manifest(schema_version=bundle.schema.version, annotator=self.annotator)
            return updated
        raise KeyError(f"未找到 span annotation: {span_id}")

    def delete_span(self, span_id: str) -> None:
        bundle = self.ensure_initialized()
        spans = self.store.load_spans()
        new_spans = [item for item in spans if item["id"] != span_id]
        if len(new_spans) == len(spans):
            raise KeyError(f"未找到 span annotation: {span_id}")
        self.store.save_spans(new_spans)
        self.store.touch_manifest(schema_version=bundle.schema.version, annotator=self.annotator)

    def list_keyframes(self) -> list[dict[str, Any]]:
        return self.store.load_keyframes()

    def create_keyframe(self, payload: dict[str, Any]) -> dict[str, Any]:
        bundle = self.ensure_initialized()
        item = self._normalize_keyframe_payload(payload, schema=bundle.schema)
        keyframes = self.store.load_keyframes()
        keyframes.append(item)
        keyframes.sort(key=lambda record: (record["sequence_id"], record["id"]))
        self.store.save_keyframes(keyframes)
        self.store.touch_manifest(schema_version=bundle.schema.version, annotator=self.annotator)
        return item

    def update_keyframe(self, keyframe_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        bundle = self.ensure_initialized()
        keyframes = self.store.load_keyframes()
        for index, item in enumerate(keyframes):
            if item["id"] != keyframe_id:
                continue
            updated = self._normalize_keyframe_payload(
                payload,
                schema=bundle.schema,
                existing=item,
                forced_id=keyframe_id,
            )
            keyframes[index] = updated
            keyframes.sort(key=lambda record: (record["sequence_id"], record["id"]))
            self.store.save_keyframes(keyframes)
            self.store.touch_manifest(schema_version=bundle.schema.version, annotator=self.annotator)
            return updated
        raise KeyError(f"未找到 keyframe annotation: {keyframe_id}")

    def delete_keyframe(self, keyframe_id: str) -> None:
        bundle = self.ensure_initialized()
        keyframes = self.store.load_keyframes()
        new_keyframes = [item for item in keyframes if item["id"] != keyframe_id]
        if len(new_keyframes) == len(keyframes):
            raise KeyError(f"未找到 keyframe annotation: {keyframe_id}")
        self.store.save_keyframes(new_keyframes)
        self.store.touch_manifest(schema_version=bundle.schema.version, annotator=self.annotator)

    def summary(self) -> dict[str, Any]:
        bundle = self.load_bundle()
        return {
            "exists": self.store.exists(),
            "schema_version": bundle.schema.version,
            "session_fields": sorted((bundle.session or {}).get("data", {}).keys()),
            "span_count": len(bundle.spans),
            "keyframe_count": len(bundle.keyframes),
        }

    def build_lerobot_annotations(self, aligned_records: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        bundle = self.load_bundle()
        session_data = (bundle.session or {}).get("data", {})
        session_annotations = {
            f"annotation.session.{field.id}": session_data.get(field.id)
            for field in bundle.schema.fields_for_scope("session")
        }
        row_annotations: list[dict[str, Any]] = []

        sorted_spans = sorted(
            bundle.spans,
            key=lambda item: (
                item["start_sequence_id"],
                item["end_sequence_id"],
                item.get("updated_at", ""),
                item["id"],
            ),
        )
        sorted_keyframes = sorted(
            bundle.keyframes,
            key=lambda item: (item["sequence_id"], item.get("updated_at", ""), item["id"]),
        )

        for record in aligned_records:
            sequence_id = int(record["sequence_id"])
            row_payload = dict(session_annotations)
            for field in bundle.schema.fields_for_scope("span"):
                row_payload[f"annotation.span.{field.id}"] = None
            for field in bundle.schema.fields_for_scope("keyframe"):
                row_payload[f"annotation.keyframe.{field.id}"] = None

            active_spans = [
                span for span in sorted_spans
                if int(span["start_sequence_id"]) <= sequence_id <= int(span["end_sequence_id"])
            ]
            if active_spans:
                active_span = active_spans[-1]
                for key, value in active_span.get("data", {}).items():
                    row_payload[f"annotation.span.{key}"] = value

            active_keyframes = [
                keyframe for keyframe in sorted_keyframes
                if int(keyframe["sequence_id"]) == sequence_id
            ]
            if active_keyframes:
                active_keyframe = active_keyframes[-1]
                for key, value in active_keyframe.get("data", {}).items():
                    row_payload[f"annotation.keyframe.{key}"] = value

            row_annotations.append(row_payload)

        return session_annotations, row_annotations

    def _normalize_span_payload(
        self,
        payload: dict[str, Any],
        *,
        schema: AnnotationSchema,
        existing: dict[str, Any] | None = None,
        forced_id: str | None = None,
    ) -> dict[str, Any]:
        start_sequence_id = int(payload["start_sequence_id"])
        end_sequence_id = int(payload["end_sequence_id"])
        if start_sequence_id > end_sequence_id:
            raise ValueError("span annotation start_sequence_id 不能大于 end_sequence_id")

        start_time = payload.get("start_time")
        end_time = payload.get("end_time")
        sensor_refs = payload.get("sensor_refs") or {}
        data = schema.validate_annotation_data("span", payload.get("data", {}))
        now = utc_now_iso()
        return {
            "id": forced_id or (existing.get("id") if existing else uuid.uuid4().hex),
            "start_sequence_id": start_sequence_id,
            "end_sequence_id": end_sequence_id,
            "start_time": float(start_time) if start_time is not None else None,
            "end_time": float(end_time) if end_time is not None else None,
            "sensor_refs": sensor_refs,
            "data": data,
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }

    def _normalize_keyframe_payload(
        self,
        payload: dict[str, Any],
        *,
        schema: AnnotationSchema,
        existing: dict[str, Any] | None = None,
        forced_id: str | None = None,
    ) -> dict[str, Any]:
        sequence_id = int(payload["sequence_id"])
        aligned_time = payload.get("aligned_time")
        sensor_refs = payload.get("sensor_refs") or {}
        data = schema.validate_annotation_data("keyframe", payload.get("data", {}))
        now = utc_now_iso()
        return {
            "id": forced_id or (existing.get("id") if existing else uuid.uuid4().hex),
            "sequence_id": sequence_id,
            "aligned_time": float(aligned_time) if aligned_time is not None else None,
            "sensor_refs": sensor_refs,
            "data": data,
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }


def create_annotation_service(
    session_dir: str | Path,
    *,
    schema_path: str | None = None,
    annotator: str | None = None,
) -> AnnotationService:
    schema = load_annotation_schema(schema_path)
    service = AnnotationService(session_dir, schema=schema, annotator=annotator)
    service.ensure_initialized()
    return service
