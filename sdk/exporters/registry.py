from __future__ import annotations

from sdk.exporters.base import SessionExporter
from sdk.exporters.csv_exporter import CSVSnapshotExporter
from sdk.exporters.hdf5_exporter import HDF5SessionExporter
from sdk.exporters.lerobot_exporter import LeRobotSessionExporter
from sdk.exporters.rlds_exporter import RLDSSessionExporter
from sdk.exporters.rosbag2_exporter import Rosbag2SessionExporter


EXPORTERS: dict[str, type[SessionExporter]] = {
    "csv": CSVSnapshotExporter,
    "hdf5": HDF5SessionExporter,
    "rlds": RLDSSessionExporter,
    "lerobot": LeRobotSessionExporter,
    "rosbag2": Rosbag2SessionExporter,
}


def create_exporter(export_format: str) -> SessionExporter:
    try:
        return EXPORTERS[export_format]()
    except KeyError as exc:
        raise ValueError(f"不支持的导出格式: {export_format}") from exc


def list_export_formats() -> list[str]:
    return sorted(EXPORTERS.keys())
