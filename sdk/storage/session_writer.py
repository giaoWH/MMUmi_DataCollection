from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from sdk.constants import SDK_SCHEMA_VERSION
from sdk.core.frame import AlignedFrame, SensorFrame, ns_to_seconds
from sdk.core.frame import TrajectoryFrame
from sdk.core.session import SessionInfo
from sdk.session_naming import stream_artifact_stem, stream_frames_path, stream_simplified_frames_path
from sdk.storage.media_io import (
    MediaEncodingConfig,
    MediaStreamWriter,
    PlannedAudioStream,
    media_path_for_sensor,
    media_role_for_sensor,
)
from sdk.storage.schema import SessionManifest, StreamManifest, manifest_path_for
from sdk.storage.simplified_stream import (
    DEFAULT_SIMPLIFIED_JSONL_TIMEZONE,
    build_simplified_sensor_record,
    should_write_simplified_stream,
    simplified_frames_path,
)

try:
    import av
except ImportError:  # pragma: no cover
    av = None

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


class SessionWriter:
    _STOP = object()

    def __init__(
        self,
        session_info: SessionInfo,
        *,
        async_writes: bool = False,
        write_queue_size: int = 0,
        artifact_worker_count: int = 4,
    ) -> None:
        self.session_info = session_info
        self.base_dir = session_info.output_dir
        self.streams_dir = self.base_dir / "streams"
        self.aligned_dir = self.base_dir / "aligned"
        self.trajectory_dir = self.base_dir / "trajectory"
        self.exports_dir = self.base_dir / "exports"
        self.sensor_dirs: dict[str, Path] = {}
        self.async_writes = async_writes
        self.write_queue_size = max(0, int(write_queue_size))
        self.artifact_worker_count = max(1, int(artifact_worker_count))
        self._write_queue: queue.Queue[object] | None = None
        self._writer_thread: threading.Thread | None = None
        self._writer_error: Exception | None = None
        self._artifact_executor: ThreadPoolExecutor | None = None
        self._artifact_futures: list[Future] = []
        self._artifact_submitted_count = 0
        self._artifact_completed_count = 0
        self._artifact_max_pending = 0
        self._jsonl_handles: dict[Path, object] = {}
        self._closed = False
        self._enqueued_counts = {"sensor": 0, "aligned": 0, "trajectory": 0}
        self._written_counts = {"sensor": 0, "aligned": 0, "trajectory": 0}
        self._max_queue_depth = 0
        self._write_latency_total_sec = 0.0
        self._write_latency_samples = 0
        self._max_write_latency_sec = 0.0
        self._media_writers: dict[Path, MediaStreamWriter] = {}
        self._pending_video_frames: dict[str, SensorFrame] = {}
        self._session_frame_id_counters: dict[str, int] = {}
        self._session_frame_id_overrides: dict[str, dict[int, int]] = {}
        self._sensor_time_summary: dict[str, dict[str, Any]] = {}
        self._trim_diagnostics: dict[str, Any] = {
            "sensor_counts": {},
        }
        self._session_window = self._extract_session_window()
        self._media_encoding_config = self._build_media_encoding_config()
        self._simplified_jsonl_timezone = self._resolve_simplified_jsonl_timezone()
        self.manifest = self._build_manifest()
        self._initialize_media_writers()
        self._prepare_layout()
        self._write_meta()
        self._write_manifest()
        if self.async_writes:
            self._write_queue = queue.Queue(maxsize=self.write_queue_size)
            self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
            self._artifact_executor = ThreadPoolExecutor(max_workers=self.artifact_worker_count)
            self._writer_thread.start()

    @classmethod
    def open_existing(cls, session_dir: str | Path) -> "SessionWriter":
        session_dir = Path(session_dir)
        try:
            manifest = SessionManifest.from_dict(
                json.loads(manifest_path_for(session_dir).read_text(encoding="utf-8"))
            )
        except KeyError as exc:
            raise FileNotFoundError(
                f"session manifest 缺少 {SDK_SCHEMA_VERSION} 所需的 task_name/task_slug 字段，不支持旧命名 session: {session_dir}"
            ) from exc
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
        writer.async_writes = False
        writer.write_queue_size = 0
        writer.artifact_worker_count = 1
        writer._write_queue = None
        writer._writer_thread = None
        writer._writer_error = None
        writer._artifact_executor = None
        writer._artifact_futures = []
        writer._artifact_submitted_count = 0
        writer._artifact_completed_count = 0
        writer._artifact_max_pending = 0
        writer._jsonl_handles = {}
        writer._closed = False
        writer._enqueued_counts = {"sensor": 0, "aligned": 0, "trajectory": 0}
        writer._written_counts = {"sensor": 0, "aligned": 0, "trajectory": 0}
        writer._max_queue_depth = 0
        writer._write_latency_total_sec = 0.0
        writer._write_latency_samples = 0
        writer._max_write_latency_sec = 0.0
        writer._media_writers = {}
        writer._pending_video_frames = {}
        writer._session_frame_id_counters = {}
        writer._session_frame_id_overrides = {}
        writer._sensor_time_summary = {}
        writer._trim_diagnostics = {"sensor_counts": {}}
        writer._session_window = {
            "start_host_time_ns": None,
            "end_host_time_ns": None,
            "start_monotonic_time_ns": None,
            "end_monotonic_time_ns": None,
            "duration_ns": None,
        }
        writer._media_encoding_config = MediaEncodingConfig()
        writer._simplified_jsonl_timezone = str(
            manifest.config.get("simplified_jsonl_timezone", DEFAULT_SIMPLIFIED_JSONL_TIMEZONE)
            or DEFAULT_SIMPLIFIED_JSONL_TIMEZONE
        )
        writer.manifest = manifest
        writer._prepare_existing_layout()
        return writer

    def write_sensor_frame(self, frame: SensorFrame) -> None:
        self._submit_write("sensor", frame)

    def write_aligned_frame(self, aligned: AlignedFrame) -> None:
        self._submit_write("aligned", aligned)

    def write_trajectory_frame(self, frame: TrajectoryFrame) -> None:
        self._submit_write("trajectory", frame)

    def __enter__(self) -> "SessionWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover
        if getattr(self, "_closed", True):
            return
        try:
            self.close()
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._finish_async_writes()
        self._flush_pending_video_frames()
        self._finish_artifact_writes()
        self._close_media_writers()
        if self.session_info is not None:
            self._finalize_timing_notes()
            self._validate_session_time_alignment()
            self.session_info.notes["writer_diagnostics"] = self.get_diagnostics()
            self.manifest = self._build_manifest()
            self._write_meta()
            self._write_manifest()
        self._close_jsonl_handles()
        self._raise_if_writer_failed()

    def get_diagnostics(self) -> dict[str, Any]:
        pending_items = 0
        if self._write_queue is not None:
            pending_items = self._write_queue.qsize()
        return {
            "async_writes": self.async_writes,
            "write_queue_size": self.write_queue_size,
            "artifact_worker_count": self.artifact_worker_count,
            "pending_items": pending_items,
            "max_queue_depth": self._max_queue_depth,
            "enqueued_counts": dict(self._enqueued_counts),
            "written_counts": dict(self._written_counts),
            "artifacts": {
                "submitted": self._artifact_submitted_count,
                "completed": self._artifact_completed_count,
                "pending": len(self._artifact_futures),
                "max_pending": self._artifact_max_pending,
            },
            "write_latency": {
                "samples": self._write_latency_samples,
                "mean_sec": (
                    self._write_latency_total_sec / self._write_latency_samples
                    if self._write_latency_samples
                    else 0.0
                ),
                "max_sec": self._max_write_latency_sec,
            },
            "error": str(self._writer_error) if self._writer_error is not None else None,
            "trim_diagnostics": self._to_jsonable(self._trim_diagnostics),
        }

    def _extract_session_window(self) -> dict[str, int | None]:
        if self.session_info is None:
            return {
                "start_host_time_ns": None,
                "end_host_time_ns": None,
                "start_monotonic_time_ns": None,
                "end_monotonic_time_ns": None,
                "duration_ns": None,
            }
        raw = dict(self.session_info.notes.get("session_time_window", {}) or {})
        window = {
            "start_host_time_ns": self._to_int_or_none(raw.get("start_host_time_ns")),
            "end_host_time_ns": self._to_int_or_none(raw.get("end_host_time_ns")),
            "start_monotonic_time_ns": self._to_int_or_none(raw.get("start_monotonic_time_ns")),
            "end_monotonic_time_ns": self._to_int_or_none(raw.get("end_monotonic_time_ns")),
            "duration_ns": self._to_int_or_none(raw.get("duration_ns")),
        }
        if window["duration_ns"] is None:
            if window["start_host_time_ns"] is not None and window["end_host_time_ns"] is not None:
                window["duration_ns"] = max(0, window["end_host_time_ns"] - window["start_host_time_ns"])
            elif (
                window["start_monotonic_time_ns"] is not None
                and window["end_monotonic_time_ns"] is not None
            ):
                window["duration_ns"] = max(
                    0,
                    window["end_monotonic_time_ns"] - window["start_monotonic_time_ns"],
                )
        return window

    def _to_int_or_none(self, value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def set_session_time_window(
        self,
        *,
        start_host_time_ns: int | None = None,
        end_host_time_ns: int | None = None,
        start_monotonic_time_ns: int | None = None,
        end_monotonic_time_ns: int | None = None,
    ) -> None:
        notes_window = self._extract_session_window()
        updated = {
            "start_host_time_ns": (
                int(start_host_time_ns)
                if start_host_time_ns is not None
                else notes_window.get("start_host_time_ns")
            ),
            "end_host_time_ns": (
                int(end_host_time_ns)
                if end_host_time_ns is not None
                else notes_window.get("end_host_time_ns")
            ),
            "start_monotonic_time_ns": (
                int(start_monotonic_time_ns)
                if start_monotonic_time_ns is not None
                else notes_window.get("start_monotonic_time_ns")
            ),
            "end_monotonic_time_ns": (
                int(end_monotonic_time_ns)
                if end_monotonic_time_ns is not None
                else notes_window.get("end_monotonic_time_ns")
            ),
        }
        if (
            updated["start_host_time_ns"] is not None
            and updated["end_host_time_ns"] is not None
        ):
            updated["duration_ns"] = max(
                0,
                int(updated["end_host_time_ns"]) - int(updated["start_host_time_ns"]),
            )
        elif (
            updated["start_monotonic_time_ns"] is not None
            and updated["end_monotonic_time_ns"] is not None
        ):
            updated["duration_ns"] = max(
                0,
                int(updated["end_monotonic_time_ns"]) - int(updated["start_monotonic_time_ns"]),
            )
        else:
            updated["duration_ns"] = notes_window.get("duration_ns")
        self._session_window = updated
        if self.session_info is not None:
            self.session_info.notes["session_time_window"] = self._to_jsonable(updated)
        for writer in self._media_writers.values():
            writer.update_session_start_ns(updated.get("start_host_time_ns"))

    def _write_sensor_frame_sync(self, frame: SensorFrame) -> int:
        if not self._should_keep_sensor_frame(frame):
            return 0
        self._ensure_record_frame_id(frame)
        if self._sensor_frame_has_video_media(frame):
            pending = self._pending_video_frames.get(frame.sensor_name)
            self._pending_video_frames[frame.sensor_name] = frame
            if pending is None:
                return 0
            return self._flush_sensor_frame_sync(
                pending,
                media_end_time_ns=self._frame_host_time_ns(frame),
            )
        return self._flush_sensor_frame_sync(frame)

    def _flush_sensor_frame_sync(
        self,
        frame: SensorFrame,
        *,
        media_end_time_ns: int | None = None,
    ) -> int:
        self._sync_stream_metadata_from_frame(frame)
        sensor_dir = self._ensure_sensor_dir(frame.sensor_name)
        record_frame_id = self._record_frame_id(frame)
        record = {
            "sensor_name": frame.sensor_name,
            "sensor_type": frame.sensor_type,
            "modality": frame.modality,
            "frame_id": record_frame_id,
            "time": {
                "host_time": frame.time.host_time,
                "host_time_ns": frame.time.host_time_ns,
                "monotonic_time": frame.time.monotonic_time,
                "monotonic_time_ns": frame.time.monotonic_time_ns,
                "device_time": frame.time.device_time,
                "device_time_ns": frame.time.device_time_ns,
                "aligned_time": frame.time.aligned_time,
                "aligned_time_ns": frame.time.aligned_time_ns,
            },
            "payload": self._serialize_payload(
                sensor_dir=sensor_dir,
                frame=frame,
                frame_id=record_frame_id,
                payload=frame.payload,
                media_end_time_ns=media_end_time_ns,
            ),
            "metadata": self._to_jsonable(frame.metadata),
        }
        stream = self.manifest.sensors[frame.sensor_name]
        self._append_jsonl(self.base_dir / stream.frames_path, record)
        self._append_simplified_sensor_jsonl(stream=stream, record=record)
        self._update_sensor_time_summary(frame)
        return 1

    def _record_frame_id(self, frame: SensorFrame) -> int:
        if frame.sensor_name in {"camera", "gelsight"}:
            return self._ensure_record_frame_id(frame)
        return frame.frame_id

    def _ensure_record_frame_id(self, frame: SensorFrame) -> int:
        if frame.sensor_name not in {"camera", "gelsight"}:
            return frame.frame_id
        overrides = self._session_frame_id_overrides.setdefault(frame.sensor_name, {})
        existing = overrides.get(frame.frame_id)
        if existing is not None:
            return existing
        next_frame_id = self._session_frame_id_counters.get(frame.sensor_name, 0)
        self._session_frame_id_counters[frame.sensor_name] = next_frame_id + 1
        overrides[frame.frame_id] = next_frame_id
        return next_frame_id

    def _sync_stream_metadata_from_frame(self, frame: SensorFrame) -> None:
        self._merge_stream_metadata(frame.sensor_name, frame.metadata)

    def _merge_stream_metadata(self, sensor_name: str, metadata: dict[str, Any] | None) -> None:
        stream = self.manifest.sensors.get(sensor_name)
        if stream is None:
            return
        metadata = metadata or {}
        merged_metadata = dict(stream.metadata)
        merged_metadata.update(self._to_jsonable(metadata))
        if merged_metadata != stream.metadata:
            sensors = dict(self.manifest.sensors)
            sensors[sensor_name] = StreamManifest(
                sensor_name=stream.sensor_name,
                sensor_type=stream.sensor_type,
                modality=stream.modality,
                frames_path=stream.frames_path,
                simplified_frames_path=stream.simplified_frames_path,
                artifacts_dir=stream.artifacts_dir,
                storage_mode=stream.storage_mode,
                media_path=stream.media_path,
                media_role=stream.media_role,
                metadata=merged_metadata,
            )
            self.manifest = SessionManifest(
                schema_version=self.manifest.schema_version,
                session_id=self.manifest.session_id,
                started_at=self.manifest.started_at,
                session_dir=self.manifest.session_dir,
                task_name=self.manifest.task_name,
                task_slug=self.manifest.task_slug,
                task_dir=self.manifest.task_dir,
                item_index=self.manifest.item_index,
                item_name=self.manifest.item_name,
                config=self.manifest.config,
                sensors=sensors,
                notes=self.manifest.notes,
                aligned_path=self.manifest.aligned_path,
                trajectory_path=self.manifest.trajectory_path,
                exports_dir=self.manifest.exports_dir,
            )
        if self.session_info is not None and sensor_name in self.session_info.sensors:
            self.session_info.sensors[sensor_name].update(self._to_jsonable(metadata))

    def _should_keep_sensor_frame(self, frame: SensorFrame) -> bool:
        window = self._session_window
        frame_time_ns, start_ns, end_ns = self._frame_window_bounds(frame)
        if frame_time_ns is None:
            return True
        sensor_trim = self._trim_diagnostics["sensor_counts"].setdefault(
            frame.sensor_name,
            {
                "dropped_before_window": 0,
                "dropped_after_window": 0,
            },
        )
        if start_ns is not None and frame_time_ns < start_ns:
            sensor_trim["dropped_before_window"] += 1
            return False
        if end_ns is not None and frame_time_ns > end_ns:
            sensor_trim["dropped_after_window"] += 1
            return False
        if window.get("start_host_time_ns") is not None and frame.time.host_time_ns is not None:
            host_time_ns = int(frame.time.host_time_ns)
            if host_time_ns < int(window["start_host_time_ns"]):
                sensor_trim["dropped_before_window"] += 1
                return False
        if window.get("end_host_time_ns") is not None and frame.time.host_time_ns is not None:
            host_time_ns = int(frame.time.host_time_ns)
            if host_time_ns > int(window["end_host_time_ns"]):
                sensor_trim["dropped_after_window"] += 1
                return False
        return True

    def _frame_window_bounds(self, frame: SensorFrame) -> tuple[int | None, int | None, int | None]:
        window = self._session_window
        if frame.time.monotonic_time_ns is not None:
            return (
                int(frame.time.monotonic_time_ns),
                window.get("start_monotonic_time_ns"),
                window.get("end_monotonic_time_ns"),
            )
        if frame.time.host_time_ns is not None:
            return (
                int(frame.time.host_time_ns),
                window.get("start_host_time_ns"),
                window.get("end_host_time_ns"),
            )
        return None, None, None

    def _sensor_frame_has_video_media(self, frame: SensorFrame) -> bool:
        return any(
            isinstance(value, np.ndarray)
            and self._should_store_visual_in_media(frame.sensor_name, frame.modality, key, value)
            for key, value in frame.payload.items()
        )

    def _update_sensor_time_summary(self, frame: SensorFrame) -> None:
        summary = self._sensor_time_summary.setdefault(
            frame.sensor_name,
            {
                "kept_frame_count": 0,
                "first_kept_host_time_ns": None,
                "last_kept_host_time_ns": None,
                "first_kept_monotonic_time_ns": None,
                "last_kept_monotonic_time_ns": None,
                "dropped_before_window": 0,
                "dropped_after_window": 0,
            },
        )
        summary["kept_frame_count"] += 1
        if frame.time.host_time_ns is not None:
            host_ns = int(frame.time.host_time_ns)
            if summary["first_kept_host_time_ns"] is None:
                summary["first_kept_host_time_ns"] = host_ns
            summary["last_kept_host_time_ns"] = host_ns
        if frame.time.monotonic_time_ns is not None:
            mono_ns = int(frame.time.monotonic_time_ns)
            if summary["first_kept_monotonic_time_ns"] is None:
                summary["first_kept_monotonic_time_ns"] = mono_ns
            summary["last_kept_monotonic_time_ns"] = mono_ns

    def _flush_pending_video_frames(self) -> None:
        if not self._pending_video_frames:
            return
        final_end_host_ns = self._session_window.get("end_host_time_ns")
        pending = list(self._pending_video_frames.items())
        self._pending_video_frames = {}
        for sensor_name, frame in pending:
            media_end_time_ns = final_end_host_ns
            if media_end_time_ns is None:
                nominal_duration_ns = int(
                    round(
                        1_000_000_000.0
                        / max(float(frame.metadata.get("fps", self.session_info.config.get("align_rate_hz", 30.0) or 30.0)), 1.0)
                    )
                )
                frame_host_ns = self._frame_host_time_ns(frame)
                if frame_host_ns is not None:
                    media_end_time_ns = frame_host_ns + nominal_duration_ns
            self._written_counts["sensor"] += self._flush_sensor_frame_sync(
                frame,
                media_end_time_ns=media_end_time_ns,
            )
            trim = self._trim_diagnostics["sensor_counts"].get(sensor_name)
            if trim is None:
                continue

    def _frame_host_time_ns(self, frame: SensorFrame) -> int | None:
        return int(frame.time.host_time_ns) if frame.time.host_time_ns is not None else None

    def _frame_media_duration_ns(
        self,
        frame_time_ns: int | None,
        media_end_time_ns: int | None,
        *,
        sensor_name: str,
    ) -> int | None:
        if frame_time_ns is not None and media_end_time_ns is not None:
            return max(1, int(media_end_time_ns) - int(frame_time_ns))
        stream = self.manifest.sensors.get(sensor_name)
        fps = None
        if stream is not None:
            fps = stream.metadata.get("fps")
        if fps in (None, 0):
            fps = self.session_info.config.get("align_rate_hz", 30.0) if self.session_info is not None else 30.0
        try:
            return max(1, int(round(1_000_000_000.0 / max(float(fps), 1.0))))
        except (TypeError, ValueError):
            return None

    def _build_media_encoding_config(self) -> MediaEncodingConfig:
        media_config = {}
        if self.session_info is not None:
            media_config = dict(self.session_info.config.get("media_encoding", {}) or {})
        return MediaEncodingConfig(
            video_codec=str(media_config.get("video_codec", "libx264")),
            video_pixel_format=str(media_config.get("video_pixel_format", "yuv420p")),
            video_profile=str(media_config.get("video_profile", "high")),
            video_preset=str(media_config.get("video_preset", "veryfast")),
            video_crf=str(media_config.get("video_crf", "20")),
            audio_codec=str(media_config.get("audio_codec", "alac")),
            movflags=str(media_config.get("movflags", "+faststart")),
        )

    def _resolve_simplified_jsonl_timezone(self) -> str:
        if self.session_info is None:
            return DEFAULT_SIMPLIFIED_JSONL_TIMEZONE
        raw_value = self.session_info.config.get(
            "simplified_jsonl_timezone",
            DEFAULT_SIMPLIFIED_JSONL_TIMEZONE,
        )
        if not isinstance(raw_value, str) or not raw_value.strip():
            return DEFAULT_SIMPLIFIED_JSONL_TIMEZONE
        return raw_value.strip()

    def _append_simplified_sensor_jsonl(
        self,
        *,
        stream: StreamManifest,
        record: dict[str, Any],
    ) -> None:
        target_path = self._simplified_frames_path_for_stream(stream)
        if target_path is None:
            return
        simplified_record = build_simplified_sensor_record(
            stream.sensor_name,
            record,
            tz_name=self._simplified_jsonl_timezone,
        )
        if simplified_record is None:
            return
        self._append_jsonl(self.base_dir / target_path, simplified_record)

    def _simplified_frames_path_for_stream(self, stream: StreamManifest) -> str | None:
        if stream.simplified_frames_path:
            return stream.simplified_frames_path
        if not should_write_simplified_stream(stream.sensor_name):
            return None
        return simplified_frames_path(stream.frames_path)

    def _write_aligned_frame_sync(self, aligned: AlignedFrame) -> None:
        record = {
            "sequence_id": aligned.sequence_id,
            "aligned_time": aligned.aligned_time,
            "aligned_time_ns": aligned.aligned_time_ns,
            "missing_sensors": aligned.missing_sensors,
            "age_by_sensor": aligned.age_by_sensor,
            "frames": {
                name: {
                    "frame_id": self._record_frame_id(frame),
                    "host_time": frame.time.host_time,
                    "host_time_ns": frame.time.host_time_ns,
                    "monotonic_time": frame.time.monotonic_time,
                    "monotonic_time_ns": frame.time.monotonic_time_ns,
                    "device_time": frame.time.device_time,
                    "device_time_ns": frame.time.device_time_ns,
                }
                for name, frame in aligned.frames.items()
            },
            "metadata": self._to_jsonable(aligned.metadata),
        }
        self._append_jsonl(self.aligned_dir / "frames.jsonl", record)

    def _write_trajectory_frame_sync(self, frame: TrajectoryFrame) -> None:
        record = {
            "source": frame.source,
            "frame_id": frame.frame_id,
            "time": {
                "host_time": frame.time.host_time,
                "host_time_ns": frame.time.host_time_ns,
                "monotonic_time": frame.time.monotonic_time,
                "monotonic_time_ns": frame.time.monotonic_time_ns,
                "device_time": frame.time.device_time,
                "device_time_ns": frame.time.device_time_ns,
                "aligned_time": frame.time.aligned_time,
                "aligned_time_ns": frame.time.aligned_time_ns,
            },
            "position": frame.position,
            "quaternion": frame.quaternion,
            "tracking_state": frame.tracking_state,
            "metadata": self._to_jsonable(frame.metadata),
        }
        self._append_jsonl(self.trajectory_dir / "frames.jsonl", record)

    def _prepare_existing_layout(self) -> None:
        self.streams_dir.mkdir(parents=True, exist_ok=True)
        self.aligned_dir.mkdir(parents=True, exist_ok=True)
        self.trajectory_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        for sensor_dir in self.sensor_dirs.values():
            (sensor_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        for stream in self.manifest.sensors.values():
            if stream.media_path:
                (self.base_dir / stream.media_path).parent.mkdir(parents=True, exist_ok=True)

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
            "task_name": self.session_info.task_name,
            "task_slug": self.session_info.task_slug,
            "task_dir": self.session_info.task_dir,
            "item_index": self.session_info.item_index,
            "item_name": self.session_info.item_name,
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
                frames_path=stream_frames_path(
                    sensor_name,
                    str(sensor_meta.get("modality", "unknown")),
                    self.session_info.task_slug,
                    self.session_info.item_name,
                ),
                simplified_frames_path=(
                    stream_simplified_frames_path(
                        sensor_name,
                        str(sensor_meta.get("modality", "unknown")),
                        self.session_info.task_slug,
                        self.session_info.item_name,
                    )
                    if should_write_simplified_stream(sensor_name)
                    else None
                ),
                artifacts_dir=f"streams/{sensor_name}/artifacts",
                storage_mode=self._storage_mode_for_sensor(
                    sensor_name,
                    str(sensor_meta.get("modality", "unknown")),
                ),
                media_path=media_path_for_sensor(
                    sensor_name,
                    str(sensor_meta.get("modality", "unknown")),
                    self.session_info.task_slug,
                    self.session_info.item_name,
                ),
                media_role=media_role_for_sensor(
                    sensor_name,
                    str(sensor_meta.get("modality", "unknown")),
                ),
                metadata=self._to_jsonable(sensor_meta),
            )
            for sensor_name, sensor_meta in self.session_info.sensors.items()
        }
        return SessionManifest(
            schema_version=self.session_info.schema_version,
            session_id=self.session_info.session_id,
            started_at=self.session_info.started_at,
            session_dir=str(self.base_dir),
            task_name=self.session_info.task_name,
            task_slug=self.session_info.task_slug,
            task_dir=self.session_info.task_dir,
            item_index=self.session_info.item_index,
            item_name=self.session_info.item_name,
            config=self._to_jsonable(self.session_info.config),
            sensors=streams,
            notes=self._to_jsonable(self.session_info.notes),
            aligned_path="aligned/frames.jsonl",
            trajectory_path="trajectory/frames.jsonl",
            exports_dir="exports",
        )

    def _initialize_media_writers(self) -> None:
        if self.session_info is None:
            return
        planned_audio: PlannedAudioStream | None = None
        microphone_meta = self.session_info.sensors.get("microphone") or {}
        if "microphone" in self.session_info.sensors:
            if "camera" not in self.session_info.sensors:
                raise RuntimeError("启用 microphone 时必须同时启用 camera 作为主视频")
            planned_audio = PlannedAudioStream(
                channels=int(microphone_meta.get("channels", 1) or 1),
                sample_rate=int(
                    microphone_meta.get("sample_rate")
                    or microphone_meta.get("rate")
                    or 48000
                ),
                dtype=str(microphone_meta.get("dtype", "int16")),
            )

        for stream in self.manifest.sensors.values():
            if not stream.media_path:
                continue
            media_path = self.base_dir / stream.media_path
            self._media_writers[media_path] = MediaStreamWriter(
                media_path,
                fps=float(stream.metadata.get("fps", self.session_info.config.get("align_rate_hz", 30.0) or 30.0)),
                expected_audio=(planned_audio if stream.media_role == "primary_av" else None),
                encoding_config=self._media_encoding_config,
                session_start_ns=self._session_window.get("start_host_time_ns"),
            )
            self._merge_stream_metadata(
                stream.sensor_name,
                {
                    "media_encoding": self._media_encoding_config.to_metadata(
                        has_audio=stream.media_role == "primary_av"
                    )
                },
            )

    def _storage_mode_for_sensor(self, sensor_name: str, modality: str) -> str:
        if media_path_for_sensor(sensor_name, modality, self.session_info.task_slug, self.session_info.item_name):
            return "indexed_media"
        return "artifact_stream"

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        handle = self._jsonl_handles.get(path)
        if handle is None:
            handle = path.open("a", encoding="utf-8")
            self._jsonl_handles[path] = handle
        handle.write(json.dumps(record, ensure_ascii=False))
        handle.write("\n")
        handle.flush()

    def _submit_write(self, kind: str, payload: SensorFrame | AlignedFrame | TrajectoryFrame) -> None:
        self._raise_if_writer_failed()
        if not self.async_writes or self._write_queue is None:
            self._process_write(kind, payload)
            return
        self._write_queue.put((kind, payload))
        self._enqueued_counts[kind] += 1
        self._max_queue_depth = max(self._max_queue_depth, self._write_queue.qsize())

    def _process_write(self, kind: str, payload: SensorFrame | AlignedFrame | TrajectoryFrame) -> None:
        started_at = time.perf_counter()
        self._collect_completed_artifact_futures(wait=False)
        written_count = 0
        if kind == "sensor":
            written_count = self._write_sensor_frame_sync(payload)
        elif kind == "aligned":
            self._write_aligned_frame_sync(payload)
            written_count = 1
        elif kind == "trajectory":
            self._write_trajectory_frame_sync(payload)
            written_count = 1
        else:  # pragma: no cover
            raise ValueError(f"不支持的写入类型: {kind}")
        latency_sec = time.perf_counter() - started_at
        self._written_counts[kind] += written_count
        self._write_latency_samples += 1
        self._write_latency_total_sec += latency_sec
        self._max_write_latency_sec = max(self._max_write_latency_sec, latency_sec)

    def _writer_loop(self) -> None:
        if self._write_queue is None:
            return
        while True:
            item = self._write_queue.get()
            try:
                if item is self._STOP:
                    return
                kind, payload = item
                self._process_write(kind, payload)
            except Exception as exc:  # pragma: no cover
                self._writer_error = exc
                return
            finally:
                self._write_queue.task_done()

    def _finish_async_writes(self) -> None:
        if not self.async_writes or self._write_queue is None:
            return
        self._raise_if_writer_failed()
        self._write_queue.put(self._STOP)
        if self._writer_thread is not None:
            self._writer_thread.join()
        self._raise_if_writer_failed()

    def _finish_artifact_writes(self) -> None:
        self._collect_completed_artifact_futures(wait=True)
        if self._artifact_executor is not None:
            self._artifact_executor.shutdown(wait=True)
            self._artifact_executor = None
        self._raise_if_writer_failed()

    def _raise_if_writer_failed(self) -> None:
        if self._writer_error is not None:
            raise RuntimeError(f"异步写盘失败: {self._writer_error}") from self._writer_error

    def _serialize_payload(
        self,
        *,
        sensor_dir: Path,
        frame: SensorFrame,
        frame_id: int,
        payload: dict[str, Any],
        media_end_time_ns: int | None = None,
    ) -> dict[str, Any]:
        return {
            key: self._serialize_value(
                sensor_dir,
                frame.sensor_name,
                frame.sensor_type,
                frame.modality,
                frame.time,
                frame_id,
                key,
                value,
                media_end_time_ns=media_end_time_ns,
            )
            for key, value in payload.items()
        }

    def _serialize_value(
        self,
        sensor_dir: Path,
        sensor_name: str,
        sensor_type: str,
        modality: str,
        frame_time,
        frame_id: int,
        key: str,
        value: Any,
        *,
        media_end_time_ns: int | None = None,
    ) -> Any:
        if isinstance(value, np.ndarray):
            if value.ndim <= 1:
                if self._should_store_audio_in_media(sensor_name, modality, key):
                    return self._store_audio_in_media(value, chunk_time_ns=frame_time.host_time_ns)
                return value.tolist()
            if self._should_store_visual_in_media(sensor_name, modality, key, value):
                return self._store_visual_in_media(
                    sensor_name,
                    sensor_type,
                    modality,
                    key,
                    value,
                    frame_time_ns=frame_time.host_time_ns,
                    frame_duration_ns=self._frame_media_duration_ns(
                        frame_time.host_time_ns,
                        media_end_time_ns,
                        sensor_name=sensor_name,
                    ),
                )
            return self._store_array(
                sensor_dir,
                sensor_type,
                modality,
                frame_id,
                key,
                value,
            )
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {
                child_key: self._serialize_value(
                    sensor_dir,
                    sensor_name,
                    sensor_type,
                    modality,
                    frame_time,
                    frame_id,
                    child_key,
                    child_value,
                    media_end_time_ns=media_end_time_ns,
                )
                for child_key, child_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                self._serialize_value(
                    sensor_dir,
                    sensor_name,
                    sensor_type,
                    modality,
                    frame_time,
                    frame_id,
                    key,
                    item,
                    media_end_time_ns=media_end_time_ns,
                )
                for item in value
            ]
        return value

    def _should_store_visual_in_media(
        self,
        sensor_name: str,
        modality: str,
        key: str,
        value: np.ndarray,
    ) -> bool:
        stream = self.manifest.sensors.get(sensor_name)
        if stream is None or not stream.media_path:
            return False
        if value.dtype != np.uint8 or value.ndim not in {2, 3}:
            return False
        if modality in {"rgb", "visuotactile"} and key in {"color", "image"}:
            return True
        return modality == "rgbd" and key == "color"

    def _should_store_audio_in_media(self, sensor_name: str, modality: str, key: str) -> bool:
        camera_stream = self.manifest.sensors.get("camera")
        return (
            sensor_name == "microphone"
            and modality == "audio"
            and key == "audio"
            and camera_stream is not None
            and bool(camera_stream.media_path)
        )

    def _store_visual_in_media(
        self,
        sensor_name: str,
        sensor_type: str,
        modality: str,
        key: str,
        value: np.ndarray,
        *,
        frame_time_ns: int | None,
        frame_duration_ns: int | None,
    ) -> dict[str, Any]:
        stream = self.manifest.sensors.get(sensor_name)
        if stream is None or not stream.media_path:
            raise RuntimeError(f"{sensor_name} 未配置媒体路径")
        media_path = self.base_dir / stream.media_path
        media_writer = self._media_writers[media_path]
        reference = media_writer.write_video_frame(
            value,
            channel_order=self._infer_channel_order(
                sensor_type=sensor_type,
                modality=modality,
                key=key,
                value=value,
            ),
            frame_time_ns=frame_time_ns,
            frame_duration_ns=frame_duration_ns,
        )
        reference["path"] = str(media_path.relative_to(self.base_dir))
        return reference

    def _store_audio_in_media(self, value: np.ndarray, *, chunk_time_ns: int | None) -> dict[str, Any]:
        camera_stream = self.manifest.sensors.get("camera")
        if camera_stream is None or not camera_stream.media_path:
            raise RuntimeError("camera 主视频不存在，无法写入音轨")
        media_path = self.base_dir / camera_stream.media_path
        media_writer = self._media_writers[media_path]
        reference = media_writer.write_audio_samples(value, chunk_time_ns=chunk_time_ns)
        reference["path"] = str(media_path.relative_to(self.base_dir))
        return reference

    def _store_array(
        self,
        sensor_dir: Path,
        sensor_type: str,
        modality: str,
        frame_id: int,
        key: str,
        value: np.ndarray,
    ) -> dict[str, Any]:
        artifacts_dir = sensor_dir / "artifacts"
        task_slug = (
            self.session_info.task_slug
            if self.session_info is not None
            else self.manifest.task_slug
        )
        base_name = stream_artifact_stem(
            task_slug,
            modality,
            frame_id,
            key,
            self.manifest.item_name,
        )
        channel_order = self._infer_channel_order(
            sensor_type=sensor_type,
            modality=modality,
            key=key,
            value=value,
        )
        if cv2 is not None and value.dtype == np.uint8 and value.ndim in {2, 3}:
            target = artifacts_dir / f"{base_name}.png"
            self._store_artifact(target, value, channel_order=channel_order)
            reference = {
                "path": str(target.relative_to(self.base_dir)),
                "storage": "png",
                "shape": list(value.shape),
                "dtype": str(value.dtype),
            }
            if channel_order is not None:
                reference["channel_order"] = channel_order
            return reference

        target = artifacts_dir / f"{base_name}.npy"
        self._store_artifact(target, value, channel_order=channel_order)
        return {
            "path": str(target.relative_to(self.base_dir)),
            "storage": "npy",
            "shape": list(value.shape),
            "dtype": str(value.dtype),
        }

    def _store_artifact(
        self,
        target: Path,
        value: np.ndarray,
        *,
        channel_order: str | None = None,
    ) -> None:
        if self._artifact_executor is None:
            self._artifact_submitted_count += 1
            self._write_artifact(target, value, channel_order=channel_order)
            self._artifact_completed_count += 1
            return
        future = self._artifact_executor.submit(
            self._write_artifact,
            target,
            value,
            channel_order,
        )
        self._artifact_futures.append(future)
        self._artifact_submitted_count += 1
        self._artifact_max_pending = max(self._artifact_max_pending, len(self._artifact_futures))

    def _collect_completed_artifact_futures(self, *, wait: bool) -> None:
        if not self._artifact_futures:
            return
        remaining: list[Future] = []
        for future in self._artifact_futures:
            if not wait and not future.done():
                remaining.append(future)
                continue
            try:
                future.result()
            except Exception as exc:  # pragma: no cover
                self._writer_error = exc
            else:
                self._artifact_completed_count += 1
        self._artifact_futures = remaining
        self._raise_if_writer_failed()

    def _write_artifact(
        self,
        target: Path,
        value: np.ndarray,
        channel_order: str | None = None,
    ) -> None:
        if cv2 is not None and value.dtype == np.uint8 and value.ndim in {2, 3}:
            encoded_value = value
            if (
                channel_order == "rgb"
                and value.ndim == 3
                and value.shape[2] == 3
            ):
                encoded_value = cv2.cvtColor(value, cv2.COLOR_RGB2BGR)
            ok = cv2.imwrite(str(target), encoded_value)
            if not ok:
                raise RuntimeError(f"写入 PNG 失败: {target}")
            return
        np.save(target, value)

    def _infer_channel_order(
        self,
        *,
        sensor_type: str,
        modality: str,
        key: str,
        value: np.ndarray,
    ) -> str | None:
        if value.dtype != np.uint8 or value.ndim != 3 or value.shape[2] != 3:
            return None
        if key not in {"color", "image"}:
            return None
        if sensor_type in {
            "camera_sensor",
            "gelsight_sensor",
            "fake_camera_sensor",
            "fake_gelsight_sensor",
        }:
            return "rgb"
        if modality in {"rgb", "visuotactile"} and sensor_type not in {"realsense", "fake_realsense"}:
            return "rgb"
        return None

    def _finalize_timing_notes(self) -> None:
        if self.session_info is None:
            return
        sensor_time_summary: dict[str, dict[str, Any]] = {}
        for sensor_name in self.manifest.sensors:
            summary = dict(self._sensor_time_summary.get(sensor_name, {}))
            trim = self._trim_diagnostics["sensor_counts"].get(sensor_name, {})
            summary.setdefault("kept_frame_count", 0)
            summary["dropped_before_window"] = int(trim.get("dropped_before_window", 0))
            summary["dropped_after_window"] = int(trim.get("dropped_after_window", 0))
            for bound in (
                "first_kept_host_time_ns",
                "last_kept_host_time_ns",
                "first_kept_monotonic_time_ns",
                "last_kept_monotonic_time_ns",
            ):
                if bound in summary:
                    summary[f"{bound[:-3]}"] = ns_to_seconds(summary[bound])
            sensor_time_summary[sensor_name] = summary
        self.session_info.notes["session_time_window"] = self._to_jsonable(self._session_window)
        self.session_info.notes["sensor_time_summary"] = self._to_jsonable(sensor_time_summary)
        self.session_info.notes["trim_diagnostics"] = self._to_jsonable(self._trim_diagnostics)
        self.session_info.notes["media_timing_mode"] = "timestamp_driven"

    def _validate_session_time_alignment(self) -> None:
        if self.session_info is None:
            return
        start_host_ns = self._session_window.get("start_host_time_ns")
        end_host_ns = self._session_window.get("end_host_time_ns")
        duration_ns = self._session_window.get("duration_ns")
        tolerance_ns = self._session_time_validation_tolerance_ns()
        report = {
            "mode": "best_effort",
            "enforced": False,
            "tolerance_ns": tolerance_ns,
            "warnings": [],
        }
        self.session_info.notes["session_time_validation"] = self._to_jsonable(report)
        if start_host_ns is None or end_host_ns is None or duration_ns is None or duration_ns <= 0:
            return
        warnings: list[str] = []
        for sensor_name in self.manifest.sensors:
            summary = self.session_info.notes.get("sensor_time_summary", {}).get(sensor_name, {})
            kept = int(summary.get("kept_frame_count", 0) or 0)
            if kept <= 0:
                continue
            first_host_ns = self._to_int_or_none(summary.get("first_kept_host_time_ns"))
            last_host_ns = self._to_int_or_none(summary.get("last_kept_host_time_ns"))
            if first_host_ns is not None and first_host_ns < start_host_ns - tolerance_ns:
                warnings.append(
                    f"{sensor_name}: 首帧早于 session_start，偏差 {first_host_ns - start_host_ns}ns"
                )
            if last_host_ns is not None and last_host_ns > end_host_ns + tolerance_ns:
                warnings.append(
                    f"{sensor_name}: 末帧晚于 session_end，偏差 {last_host_ns - end_host_ns}ns"
                )
        for stream in self.manifest.sensors.values():
            if not stream.media_path:
                continue
            media_path = self.base_dir / stream.media_path
            if not media_path.exists():
                continue
            duration_error = self._probe_media_duration_error_ns(media_path, expected_duration_ns=duration_ns)
            if duration_error is None:
                continue
            effective_tolerance_ns = max(tolerance_ns, self._media_duration_tolerance_ns(media_path))
            if abs(duration_error) > effective_tolerance_ns:
                warnings.append(
                    f"{stream.sensor_name}: 媒体时长与 session 不一致，偏差 {duration_error}ns"
                )
        report["warnings"] = warnings
        self.session_info.notes["session_time_validation"] = self._to_jsonable(report)

    def _session_time_validation_tolerance_ns(self) -> int:
        if self.session_info is None:
            return 100_000_000
        raw_value = self.session_info.config.get("session_time_validation_tolerance_sec", 0.1)
        try:
            return max(1, int(round(float(raw_value) * 1_000_000_000.0)))
        except (TypeError, ValueError):
            return 100_000_000

    def _probe_media_duration_error_ns(self, media_path: Path, *, expected_duration_ns: int) -> int | None:
        if av is None:
            return None
        container = av.open(str(media_path))
        try:
            stream_durations_ns: list[int] = []
            for stream in container.streams:
                if stream.type not in {"video", "audio"}:
                    continue
                if stream.duration is None or stream.time_base is None:
                    continue
                stream_durations_ns.append(
                    int(round(float(stream.duration * stream.time_base) * 1_000_000_000.0))
                )
            if not stream_durations_ns:
                return None
            return max(stream_durations_ns) - int(expected_duration_ns)
        finally:
            container.close()

    def _media_duration_tolerance_ns(self, media_path: Path) -> int:
        if av is None:
            return 0
        container = av.open(str(media_path))
        try:
            tolerances: list[int] = []
            for stream in container.streams:
                if stream.type == "video" and stream.time_base is not None:
                    tolerances.append(max(1, int(round(float(stream.time_base) * 1_000_000_000.0))))
                if stream.type == "audio" and getattr(stream, "sample_rate", None):
                    tolerances.append(max(1, int(round(1_000_000_000.0 / int(stream.sample_rate)))))
            return max(tolerances) if tolerances else 1_000
        finally:
            container.close()

    def _close_media_writers(self) -> None:
        for media_writer in self._media_writers.values():
            media_writer.close()

    def _close_jsonl_handles(self) -> None:
        for handle in self._jsonl_handles.values():
            try:
                handle.flush()
            except Exception:
                pass
            try:
                handle.close()
            except Exception:
                pass
        self._jsonl_handles = {}

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
