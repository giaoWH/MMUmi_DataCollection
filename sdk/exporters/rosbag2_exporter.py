from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sdk.exporters.base import ExportResult, SessionExporter
from sdk.storage import SessionReader


class Rosbag2SessionExporter(SessionExporter):
    export_format = "rosbag2"

    def export(self, session_dir: str | Path, output_path: str | Path | None = None) -> ExportResult:
        try:
            import rosbag2_py
            from rclpy.serialization import serialize_message
            from std_msgs.msg import String
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "未安装 rosbag2_py / rclpy / std_msgs，无法导出真实 ROS Bag"
            ) from exc

        reader = SessionReader(session_dir)
        target = Path(output_path) if output_path else Path(session_dir) / "exports" / "rosbag2"
        target.parent.mkdir(parents=True, exist_ok=True)

        writer = rosbag2_py.SequentialWriter()
        writer.open(
            rosbag2_py.StorageOptions(uri=str(target), storage_id="sqlite3"),
            rosbag2_py.ConverterOptions("", ""),
        )

        topics = {
            **{f"/sdk/{name}": "std_msgs/msg/String" for name in reader.sensor_names()},
            "/sdk/aligned": "std_msgs/msg/String",
            "/sdk/trajectory": "std_msgs/msg/String",
        }
        for topic_name, topic_type in topics.items():
            writer.create_topic(
                rosbag2_py.TopicMetadata(
                    name=topic_name,
                    type=topic_type,
                    serialization_format="cdr",
                )
            )

        for sensor_name in reader.sensor_names():
            for frame in reader.iter_sensor_frames(sensor_name, load_payload=False):
                msg = String()
                msg.data = json.dumps(self._frame_payload(frame), ensure_ascii=False)
                writer.write(
                    f"/sdk/{sensor_name}",
                    serialize_message(msg),
                    self._timestamp_ns(frame.time.host_time_ns, frame.time.host_time),
                )

        for record in reader.iter_aligned_records():
            msg = String()
            msg.data = json.dumps(record, ensure_ascii=False)
            writer.write(
                "/sdk/aligned",
                serialize_message(msg),
                self._timestamp_ns(record.get("aligned_time_ns"), record["aligned_time"]),
            )

        for frame in reader.iter_trajectory_frames():
            msg = String()
            msg.data = json.dumps(
                {
                    "source": frame.source,
                    "frame_id": frame.frame_id,
                    "position": frame.position,
                    "quaternion": frame.quaternion,
                    "tracking_state": frame.tracking_state,
                },
                ensure_ascii=False,
            )
            writer.write(
                "/sdk/trajectory",
                serialize_message(msg),
                self._timestamp_ns(frame.time.host_time_ns, frame.time.host_time),
            )

        del writer
        return ExportResult(export_format=self.export_format, output_path=target)

    def _frame_payload(self, frame: Any) -> dict[str, Any]:
        return {
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
            "payload": frame.payload,
            "metadata": frame.metadata,
        }

    def _timestamp_ns(self, value_ns: int | None, value_sec: float | None) -> int:
        if value_ns is not None:
            return int(value_ns)
        if value_sec is None:
            return 0
        return int(value_sec * 1_000_000_000)
