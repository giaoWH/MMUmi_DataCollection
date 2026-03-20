from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from sdk.storage import SessionReader

try:
    import h5py
except ImportError:  # pragma: no cover
    h5py = None

try:
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover
    pq = None


@dataclass(frozen=True)
class ExportValidationResult:
    export_format: str
    output_path: Path
    issues: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.issues


class SessionExportValidator:
    def __init__(self, session_dir: str | Path) -> None:
        self.session_dir = Path(session_dir)
        self.reader = SessionReader(session_dir)

    def validate(
        self,
        export_format: str,
        output_path: str | Path | None = None,
    ) -> ExportValidationResult:
        target = self._resolve_output_path(export_format, output_path)
        validator = getattr(self, f"_validate_{export_format}", None)
        if validator is None:
            raise ValueError(f"暂不支持校验该导出格式: {export_format}")
        return ExportValidationResult(
            export_format=export_format,
            output_path=target,
            issues=validator(target),
        )

    def _resolve_output_path(self, export_format: str, output_path: str | Path | None) -> Path:
        if output_path is not None:
            return Path(output_path)

        defaults = {
            "csv": self.session_dir / "exports" / "session_snapshot.csv",
            "hdf5": self.session_dir / "exports" / "session.hdf5",
            "rlds": self.session_dir / "exports" / "rlds",
            "lerobot": self.session_dir / "exports" / "lerobot",
            "rosbag2": self.session_dir / "exports" / "rosbag2",
        }
        try:
            return defaults[export_format]
        except KeyError as exc:
            raise ValueError(f"不支持的导出格式: {export_format}") from exc

    def _expected_step_count(self) -> int:
        return sum(1 for _ in self.reader.iter_aligned_records())

    def _validate_csv(self, target: Path) -> list[str]:
        issues: list[str] = []
        if not target.exists():
            return [f"未找到 CSV 文件: {target}"]

        required_columns = {"Aligned_Time", "FT_Time", "IMU_Time", "Missing_Sensors"}
        sensor_modalities = {stream.modality for stream in self.reader.manifest.sensors.values()}
        if "rgbd" in sensor_modalities:
            required_columns.add("RealSense_Frame_ID")
        if "rgb" in sensor_modalities:
            required_columns.add("Camera_Frame_ID")

        with target.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            missing_columns = sorted(required_columns - set(reader.fieldnames or []))
            if missing_columns:
                issues.append(f"CSV 缺少字段: {', '.join(missing_columns)}")

        if len(rows) != self._expected_step_count():
            issues.append(f"CSV 行数与 aligned 记录数不一致: {len(rows)} != {self._expected_step_count()}")
        return issues

    def _validate_hdf5(self, target: Path) -> list[str]:
        if h5py is None:
            return ["未安装 h5py，无法校验 HDF5 导出"]
        if not target.exists():
            return [f"未找到 HDF5 文件: {target}"]

        issues: list[str] = []
        with h5py.File(target, "r") as handle:
            for group_name in ("streams", "aligned", "trajectory"):
                if group_name not in handle:
                    issues.append(f"HDF5 缺少分组: {group_name}")
            if "aligned" in handle and len(handle["aligned"].keys()) != self._expected_step_count():
                issues.append(
                    f"HDF5 aligned 条目数与 session 不一致: {len(handle['aligned'].keys())} != {self._expected_step_count()}"
                )
        return issues

    def _validate_rlds(self, target: Path) -> list[str]:
        episode_path = target / "episodes" / "episode_000000.json"
        if not episode_path.exists():
            return [f"未找到 RLDS episode 文件: {episode_path}"]

        issues: list[str] = []
        payload = json.loads(episode_path.read_text(encoding="utf-8"))
        steps = payload.get("steps", [])
        if len(steps) != self._expected_step_count():
            issues.append(f"RLDS step 数量与 session 不一致: {len(steps)} != {self._expected_step_count()}")
        for field_name in ("episode_id", "metadata", "steps"):
            if field_name not in payload:
                issues.append(f"RLDS 缺少字段: {field_name}")
        return issues

    def _validate_lerobot(self, target: Path) -> list[str]:
        issues: list[str] = []
        info_path = target / "meta" / "info.json"
        data_path = target / "data" / "chunk-000" / "file-000.parquet"
        episode_path = target / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
        for path in (info_path, data_path, episode_path):
            if not path.exists():
                issues.append(f"LeRobot 缺少文件: {path}")
        if issues:
            return issues

        info_payload = json.loads(info_path.read_text(encoding="utf-8"))
        if info_payload.get("total_frames") != self._expected_step_count():
            issues.append(
                f"LeRobot total_frames 与 session 不一致: {info_payload.get('total_frames')} != {self._expected_step_count()}"
            )
        if pq is None:
            issues.append("未安装 pyarrow，无法进一步校验 LeRobot parquet")
            return issues

        data_table = pq.read_table(data_path)
        if data_table.num_rows != self._expected_step_count():
            issues.append(f"LeRobot parquet 行数与 session 不一致: {data_table.num_rows} != {self._expected_step_count()}")
        return issues

    def _validate_rosbag2(self, target: Path) -> list[str]:
        if not target.exists():
            return [f"未找到 rosbag2 输出目录: {target}"]
        expected_files = {f.name for f in target.glob("*")}
        if not expected_files:
            return ["rosbag2 输出目录为空"]
        return []
