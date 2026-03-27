from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from sdk.core.frame import AlignedFrame, SensorFrame
from sdk.core.frame import TrajectoryFrame
from sdk.core.session import SessionInfo
from sdk.storage.media_io import (
    MediaEncodingConfig,
    MediaStreamWriter,
    PlannedAudioStream,
    media_path_for_sensor,
    media_role_for_sensor,
)
from sdk.storage.schema import SessionManifest, StreamManifest, manifest_path_for

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
        self._media_encoding_config = self._build_media_encoding_config()
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
        writer._media_encoding_config = MediaEncodingConfig()
        writer.manifest = manifest
        writer._prepare_existing_layout()
        return writer

    def write_sensor_frame(self, frame: SensorFrame) -> None:
        self._submit_write("sensor", frame)

    def write_aligned_frame(self, aligned: AlignedFrame) -> None:
        self._submit_write("aligned", aligned)

    def write_trajectory_frame(self, frame: TrajectoryFrame) -> None:
        self._submit_write("trajectory", frame)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._finish_async_writes()
        self._close_media_writers()
        if self.session_info is not None:
            self.session_info.notes["writer_diagnostics"] = self.get_diagnostics()
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
        }

    def _write_sensor_frame_sync(self, frame: SensorFrame) -> None:
        self._sync_stream_metadata_from_frame(frame)
        sensor_dir = self._ensure_sensor_dir(frame.sensor_name)
        record = {
            "sensor_name": frame.sensor_name,
            "sensor_type": frame.sensor_type,
            "modality": frame.modality,
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
                "host_arrival_time": frame.time.host_arrival_time,
                "host_arrival_time_ns": frame.time.host_arrival_time_ns,
                "host_read_start_time_ns": frame.time.host_read_start_time_ns,
                "host_read_end_time_ns": frame.time.host_read_end_time_ns,
            },
            "payload": self._serialize_payload(
                sensor_dir=sensor_dir,
                frame=frame,
                frame_id=frame.frame_id,
                payload=frame.payload,
            ),
            "metadata": self._to_jsonable(frame.metadata),
        }
        self._append_jsonl(sensor_dir / "frames.jsonl", record)

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
                config=self.manifest.config,
                sensors=sensors,
                notes=self.manifest.notes,
                aligned_path=self.manifest.aligned_path,
                trajectory_path=self.manifest.trajectory_path,
                exports_dir=self.manifest.exports_dir,
            )
        if self.session_info is not None and sensor_name in self.session_info.sensors:
            self.session_info.sensors[sensor_name].update(self._to_jsonable(metadata))

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

    def _write_aligned_frame_sync(self, aligned: AlignedFrame) -> None:
        record = {
            "sequence_id": aligned.sequence_id,
            "aligned_time": aligned.aligned_time,
            "aligned_time_ns": aligned.aligned_time_ns,
            "missing_sensors": aligned.missing_sensors,
            "age_by_sensor": aligned.age_by_sensor,
            "frames": {
                name: {
                    "frame_id": frame.frame_id,
                    "host_time": frame.time.host_time,
                    "host_time_ns": frame.time.host_time_ns,
                    "monotonic_time": frame.time.monotonic_time,
                    "monotonic_time_ns": frame.time.monotonic_time_ns,
                    "device_time": frame.time.device_time,
                    "device_time_ns": frame.time.device_time_ns,
                    "host_arrival_time": frame.time.host_arrival_time,
                    "host_arrival_time_ns": frame.time.host_arrival_time_ns,
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
                "host_arrival_time": frame.time.host_arrival_time,
                "host_arrival_time_ns": frame.time.host_arrival_time_ns,
                "host_read_start_time_ns": frame.time.host_read_start_time_ns,
                "host_read_end_time_ns": frame.time.host_read_end_time_ns,
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
                storage_mode=self._storage_mode_for_sensor(
                    sensor_name,
                    str(sensor_meta.get("modality", "unknown")),
                ),
                media_path=media_path_for_sensor(
                    sensor_name,
                    str(sensor_meta.get("modality", "unknown")),
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
        if media_path_for_sensor(sensor_name, modality):
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
        if kind == "sensor":
            self._write_sensor_frame_sync(payload)
        elif kind == "aligned":
            self._write_aligned_frame_sync(payload)
        elif kind == "trajectory":
            self._write_trajectory_frame_sync(payload)
        else:  # pragma: no cover
            raise ValueError(f"不支持的写入类型: {kind}")
        latency_sec = time.perf_counter() - started_at
        self._written_counts[kind] += 1
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
            self._collect_completed_artifact_futures(wait=True)
            if self._artifact_executor is not None:
                self._artifact_executor.shutdown(wait=True)
            return
        self._raise_if_writer_failed()
        self._write_queue.put(self._STOP)
        if self._writer_thread is not None:
            self._writer_thread.join()
        self._collect_completed_artifact_futures(wait=True)
        if self._artifact_executor is not None:
            self._artifact_executor.shutdown(wait=True)
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
    ) -> dict[str, Any]:
        return {
            key: self._serialize_value(
                sensor_dir,
                frame.sensor_name,
                frame.sensor_type,
                frame.modality,
                frame_id,
                key,
                value,
            )
            for key, value in payload.items()
        }

    def _serialize_value(
        self,
        sensor_dir: Path,
        sensor_name: str,
        sensor_type: str,
        modality: str,
        frame_id: int,
        key: str,
        value: Any,
    ) -> Any:
        if isinstance(value, np.ndarray):
            if value.ndim <= 1:
                if self._should_store_audio_in_media(sensor_name, modality, key):
                    return self._store_audio_in_media(value)
                return value.tolist()
            if self._should_store_visual_in_media(sensor_name, modality, key, value):
                return self._store_visual_in_media(
                    sensor_name,
                    sensor_type,
                    modality,
                    key,
                    value,
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
                    frame_id,
                    child_key,
                    child_value,
                )
                for child_key, child_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                self._serialize_value(sensor_dir, sensor_name, sensor_type, modality, frame_id, key, item)
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
        )
        reference["path"] = str(media_path.relative_to(self.base_dir))
        return reference

    def _store_audio_in_media(self, value: np.ndarray) -> dict[str, Any]:
        camera_stream = self.manifest.sensors.get("camera")
        if camera_stream is None or not camera_stream.media_path:
            raise RuntimeError("camera 主视频不存在，无法写入音轨")
        media_path = self.base_dir / camera_stream.media_path
        media_writer = self._media_writers[media_path]
        reference = media_writer.write_audio_samples(value)
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
        base_name = f"{frame_id:06d}_{key}"
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
