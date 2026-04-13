from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_SIMPLIFIED_JSONL_TIMEZONE = "Asia/Shanghai"
SIMPLIFIED_JSONL_SENSOR_NAMES = frozenset({"ft", "imu", "motors", "realsense"})


def should_write_simplified_stream(sensor_name: str) -> bool:
    return sensor_name in SIMPLIFIED_JSONL_SENSOR_NAMES


def simplified_frames_path(frames_path: str | Path) -> str:
    path = Path(frames_path)
    return str(path.with_name(f"{path.stem}_s{path.suffix}"))


def format_simplified_host_time(
    timestamp: float | int | None,
    *,
    tz_name: str = DEFAULT_SIMPLIFIED_JSONL_TIMEZONE,
) -> str | None:
    if timestamp is None:
        return None
    dt = datetime.fromtimestamp(float(timestamp), tz=ZoneInfo(tz_name))
    return dt.strftime("%Y-%m-%d-%H-%M-%S-%f")[:-3]


def build_simplified_sensor_record(
    sensor_name: str,
    record: dict[str, Any],
    *,
    tz_name: str = DEFAULT_SIMPLIFIED_JSONL_TIMEZONE,
) -> dict[str, Any] | None:
    if not should_write_simplified_stream(sensor_name):
        return None

    time_payload = record.get("time")
    host_time = None
    if isinstance(time_payload, dict):
        host_time = format_simplified_host_time(time_payload.get("host_time"), tz_name=tz_name)

    payload = record.get("payload")
    if sensor_name == "realsense":
        depth_path = None
        if isinstance(payload, dict):
            depth = payload.get("depth")
            if isinstance(depth, dict):
                raw_path = depth.get("path")
                if raw_path is not None:
                    depth_path = str(raw_path)
        return {
            "host_time": host_time,
            "payload": {"depth_path": depth_path},
        }

    return {
        "host_time": host_time,
        "payload": payload,
    }
