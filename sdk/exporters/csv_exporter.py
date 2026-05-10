from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sdk.exporters.base import ExportResult, SessionExporter
from sdk.storage import SessionReader


class CSVSnapshotExporter(SessionExporter):
    export_format = "csv"

    def export(self, session_dir: str | Path, output_path: str | Path | None = None) -> ExportResult:
        reader = SessionReader(session_dir)
        target = Path(output_path) if output_path else Path(session_dir) / "exports" / "session_snapshot.csv"
        target.parent.mkdir(parents=True, exist_ok=True)

        frame_index: dict[str, dict[int, Any]] = {}
        for sensor_name in reader.sensor_names():
            frame_index[sensor_name] = {
                frame.frame_id: frame
                for frame in reader.iter_sensor_frames(sensor_name, load_payload=True)
            }

        fieldnames = [
            "Aligned_Time",
            "FT_Time",
            "Fx_Raw",
            "Fy_Raw",
            "Fz_Raw",
            "Tx",
            "Ty",
            "Tz",
            "IMU_Time",
            "Ax",
            "Ay",
            "Az",
            "Gx",
            "Gy",
            "Gz",
            "Qw",
            "Qx",
            "Qy",
            "Qz",
            "RealSense_Time",
            "RealSense_Frame_ID",
            "Camera_Time",
            "Camera_Frame_ID",
            "Missing_Sensors",
        ]

        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for aligned_record in reader.iter_aligned_records():
                writer.writerow(self._build_row(aligned_record, frame_index))

        return ExportResult(export_format=self.export_format, output_path=target)

    def _build_row(self, aligned_record: dict[str, Any], frame_index: dict[str, dict[int, Any]]) -> dict[str, Any]:
        row = {key: "" for key in [
            "FT_Time", "Fx_Raw", "Fy_Raw", "Fz_Raw", "Tx", "Ty", "Tz",
            "Fx_Pure", "Fy_Pure", "Fz_Pure", "Gravity_Fx", "Gravity_Fy", "Gravity_Fz",
            "IMU_Time", "Ax", "Ay", "Az", "Gx", "Gy", "Gz", "Qw", "Qx", "Qy", "Qz",
            "RealSense_Time", "RealSense_Frame_ID", "Camera_Time", "Camera_Frame_ID",
        ]}
        row["Aligned_Time"] = f"{float(aligned_record['aligned_time']):.6f}"
        row["Missing_Sensors"] = "|".join(aligned_record.get("missing_sensors", []))

        self._fill_force_torque(row, aligned_record, frame_index)
        self._fill_imu(row, aligned_record, frame_index)
        self._fill_visual_streams(row, aligned_record, frame_index)
        return row

    def _fill_force_torque(self, row: dict[str, Any], aligned_record: dict[str, Any], frame_index: dict[str, dict[int, Any]]) -> None:
        for sensor_name, frame_info in aligned_record.get("frames", {}).items():
            frame = frame_index.get(sensor_name, {}).get(frame_info["frame_id"])
            if frame is None or frame.modality != "force_torque":
                continue
            row["FT_Time"] = f"{float(frame.time.host_time):.6f}"
            force = frame.payload.get("force", [])
            torque = frame.payload.get("torque", [])
            if len(force) >= 3:
                row["Fx_Raw"], row["Fy_Raw"], row["Fz_Raw"] = [f"{float(value):.6f}" for value in force[:3]]
            if len(torque) >= 3:
                row["Tx"], row["Ty"], row["Tz"] = [f"{float(value):.6f}" for value in torque[:3]]
            if len(force) >= 3 and len(torque) >= 3:
                return

            wrench = frame.payload.get("force_torque", [])
            if len(wrench) >= 6:
                row["Fx_Raw"], row["Fy_Raw"], row["Fz_Raw"] = [f"{float(value):.6f}" for value in wrench[:3]]
                row["Tx"], row["Ty"], row["Tz"] = [f"{float(value):.6f}" for value in wrench[3:6]]
            return

    def _fill_imu(self, row: dict[str, Any], aligned_record: dict[str, Any], frame_index: dict[str, dict[int, Any]]) -> None:
        for sensor_name, frame_info in aligned_record.get("frames", {}).items():
            frame = frame_index.get(sensor_name, {}).get(frame_info["frame_id"])
            if frame is None or frame.modality != "imu":
                continue
            row["IMU_Time"] = f"{float(frame.time.host_time):.6f}"
            acceleration = frame.payload.get("acceleration", [])
            angular_velocity = frame.payload.get("angular_velocity", [])
            quaternion = frame.payload.get("quaternion", [])
            if len(acceleration) >= 3:
                row["Ax"], row["Ay"], row["Az"] = [f"{float(value):.6f}" for value in acceleration[:3]]
            if len(angular_velocity) >= 3:
                row["Gx"], row["Gy"], row["Gz"] = [f"{float(value):.6f}" for value in angular_velocity[:3]]
            if len(quaternion) >= 4:
                row["Qw"], row["Qx"], row["Qy"], row["Qz"] = [f"{float(value):.6f}" for value in quaternion[:4]]
            return

    def _fill_visual_streams(
        self,
        row: dict[str, Any],
        aligned_record: dict[str, Any],
        frame_index: dict[str, dict[int, Any]],
    ) -> None:
        for sensor_name, frame_info in aligned_record.get("frames", {}).items():
            frame = frame_index.get(sensor_name, {}).get(frame_info["frame_id"])
            if frame is None:
                continue
            if frame.modality == "rgbd" and not row["RealSense_Frame_ID"]:
                row["RealSense_Time"] = f"{float(frame_info['host_time']):.6f}"
                row["RealSense_Frame_ID"] = str(frame_info["frame_id"])
                continue
            if frame.modality == "rgb" and not row["Camera_Frame_ID"]:
                row["Camera_Time"] = f"{float(frame_info['host_time']):.6f}"
                row["Camera_Frame_ID"] = str(frame_info["frame_id"])

