from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from sdk.exporters.base import ExportResult, SessionExporter
from sdk.storage import SessionReader

try:
    import h5py
except ImportError:  # pragma: no cover
    h5py = None


class HDF5SessionExporter(SessionExporter):
    export_format = "hdf5"

    def export(self, session_dir: str | Path, output_path: str | Path | None = None) -> ExportResult:
        if h5py is None:
            raise RuntimeError("未安装 h5py，无法导出 HDF5")

        reader = SessionReader(session_dir)
        target = Path(output_path) if output_path else Path(session_dir) / "exports" / "session.hdf5"
        target.parent.mkdir(parents=True, exist_ok=True)

        with h5py.File(target, "w") as handle:
            summary = reader.summary()
            for key, value in summary.items():
                handle.attrs[key] = json.dumps(value, ensure_ascii=False)
            handle.create_dataset("config_json", data=json.dumps(reader.manifest.config, ensure_ascii=False))
            handle.create_dataset("notes_json", data=json.dumps(reader.manifest.notes, ensure_ascii=False))

            streams_group = handle.create_group("streams")
            for sensor_name in reader.sensor_names():
                sensor_group = streams_group.create_group(sensor_name)
                for frame in reader.iter_sensor_frames(sensor_name, load_payload=True):
                    frame_group = sensor_group.create_group(f"{frame.frame_id:06d}")
                    frame_group.attrs["sensor_type"] = frame.sensor_type
                    frame_group.attrs["modality"] = frame.modality
                    frame_group.create_dataset("host_time", data=frame.time.host_time)
                    if frame.time.host_time_ns is not None:
                        frame_group.create_dataset("host_time_ns", data=frame.time.host_time_ns)
                    frame_group.create_dataset("monotonic_time", data=frame.time.monotonic_time)
                    if frame.time.monotonic_time_ns is not None:
                        frame_group.create_dataset("monotonic_time_ns", data=frame.time.monotonic_time_ns)
                    if frame.time.device_time is not None:
                        frame_group.create_dataset("device_time", data=frame.time.device_time)
                    if frame.time.device_time_ns is not None:
                        frame_group.create_dataset("device_time_ns", data=frame.time.device_time_ns)
                    if frame.time.aligned_time is not None:
                        frame_group.create_dataset("aligned_time", data=frame.time.aligned_time)
                    if frame.time.aligned_time_ns is not None:
                        frame_group.create_dataset("aligned_time_ns", data=frame.time.aligned_time_ns)
                    if frame.time.host_arrival_time is not None:
                        frame_group.create_dataset("host_arrival_time", data=frame.time.host_arrival_time)
                    if frame.time.host_arrival_time_ns is not None:
                        frame_group.create_dataset("host_arrival_time_ns", data=frame.time.host_arrival_time_ns)
                    if frame.time.host_read_start_time_ns is not None:
                        frame_group.create_dataset("host_read_start_time_ns", data=frame.time.host_read_start_time_ns)
                    if frame.time.host_read_end_time_ns is not None:
                        frame_group.create_dataset("host_read_end_time_ns", data=frame.time.host_read_end_time_ns)
                    payload_group = frame_group.create_group("payload")
                    self._write_mapping(payload_group, frame.payload)
                    frame_group.create_dataset(
                        "metadata_json",
                        data=json.dumps(frame.metadata, ensure_ascii=False),
                    )

            aligned_group = handle.create_group("aligned")
            for record in reader.iter_aligned_records():
                record_group = aligned_group.create_group(f"{record['sequence_id']:06d}")
                record_group.create_dataset("aligned_time", data=record["aligned_time"])
                record_group.create_dataset(
                    "missing_sensors_json",
                    data=json.dumps(record.get("missing_sensors", []), ensure_ascii=False),
                )
                record_group.create_dataset(
                    "age_by_sensor_json",
                    data=json.dumps(record.get("age_by_sensor", {}), ensure_ascii=False),
                )
                record_group.create_dataset(
                    "frames_json",
                    data=json.dumps(record.get("frames", {}), ensure_ascii=False),
                )

            trajectory_group = handle.create_group("trajectory")
            for frame in reader.iter_trajectory_frames():
                frame_group = trajectory_group.create_group(f"{frame.frame_id:06d}")
                frame_group.attrs["source"] = frame.source
                frame_group.attrs["tracking_state"] = frame.tracking_state
                frame_group.create_dataset("host_time", data=frame.time.host_time)
                if frame.time.host_time_ns is not None:
                    frame_group.create_dataset("host_time_ns", data=frame.time.host_time_ns)
                frame_group.create_dataset("monotonic_time", data=frame.time.monotonic_time)
                if frame.time.monotonic_time_ns is not None:
                    frame_group.create_dataset("monotonic_time_ns", data=frame.time.monotonic_time_ns)
                if frame.time.device_time is not None:
                    frame_group.create_dataset("device_time", data=frame.time.device_time)
                if frame.time.device_time_ns is not None:
                    frame_group.create_dataset("device_time_ns", data=frame.time.device_time_ns)
                if frame.time.aligned_time is not None:
                    frame_group.create_dataset("aligned_time", data=frame.time.aligned_time)
                if frame.time.aligned_time_ns is not None:
                    frame_group.create_dataset("aligned_time_ns", data=frame.time.aligned_time_ns)
                if frame.time.host_arrival_time is not None:
                    frame_group.create_dataset("host_arrival_time", data=frame.time.host_arrival_time)
                if frame.time.host_arrival_time_ns is not None:
                    frame_group.create_dataset("host_arrival_time_ns", data=frame.time.host_arrival_time_ns)
                if frame.time.host_read_start_time_ns is not None:
                    frame_group.create_dataset("host_read_start_time_ns", data=frame.time.host_read_start_time_ns)
                if frame.time.host_read_end_time_ns is not None:
                    frame_group.create_dataset("host_read_end_time_ns", data=frame.time.host_read_end_time_ns)
                frame_group.create_dataset("position", data=np.asarray(frame.position, dtype=np.float64))
                frame_group.create_dataset("quaternion", data=np.asarray(frame.quaternion, dtype=np.float64))
                frame_group.create_dataset(
                    "metadata_json",
                    data=json.dumps(frame.metadata, ensure_ascii=False),
                )

        return ExportResult(export_format=self.export_format, output_path=target)

    def _write_mapping(self, group: Any, payload: dict[str, Any]) -> None:
        for key, value in payload.items():
            self._write_value(group, key, value)

    def _write_value(self, group: Any, key: str, value: Any) -> None:
        if isinstance(value, np.ndarray):
            group.create_dataset(key, data=value)
            return
        if isinstance(value, dict):
            child_group = group.create_group(key)
            if {"path", "storage", "shape", "dtype"} <= set(value.keys()):
                child_group.create_dataset("artifact_ref_json", data=json.dumps(value, ensure_ascii=False))
                return
            for child_key, child_value in value.items():
                self._write_value(child_group, child_key, child_value)
            return
        if isinstance(value, list):
            if all(isinstance(item, (int, float, bool)) for item in value):
                group.create_dataset(key, data=np.asarray(value))
            else:
                group.create_dataset(key, data=json.dumps(value, ensure_ascii=False))
            return
        if isinstance(value, (int, float, bool, np.integer, np.floating)):
            group.create_dataset(key, data=value)
            return
        group.create_dataset(key, data=json.dumps(value, ensure_ascii=False))
