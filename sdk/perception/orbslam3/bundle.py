from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from sdk.storage import SessionReader

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


SUPPORTED_MODES = {
    "rgbd",
    "rgbd_inertial",
    "mono_inertial",
}


@dataclass(frozen=True)
class OrbSlam3Bundle:
    mode: str
    bundle_dir: Path
    manifest_path: Path
    rgb_dir: Path | None = None
    depth_dir: Path | None = None
    association_file: Path | None = None
    rgb_timestamps_file: Path | None = None
    depth_timestamps_file: Path | None = None
    imu_file: Path | None = None

    def template_vars(self) -> dict[str, str]:
        values = {
            "bundle_dir": str(self.bundle_dir),
            "bundle_manifest": str(self.manifest_path),
        }
        if self.rgb_dir is not None:
            values["rgb_dir"] = str(self.rgb_dir)
        if self.depth_dir is not None:
            values["depth_dir"] = str(self.depth_dir)
        if self.association_file is not None:
            values["association_file"] = str(self.association_file)
        if self.rgb_timestamps_file is not None:
            values["rgb_timestamps_file"] = str(self.rgb_timestamps_file)
        if self.depth_timestamps_file is not None:
            values["depth_timestamps_file"] = str(self.depth_timestamps_file)
        if self.imu_file is not None:
            values["imu_file"] = str(self.imu_file)
        return values


class OrbSlam3SessionBundleExporter:
    def export(
        self,
        session_dir: str | Path,
        *,
        output_dir: str | Path | None = None,
        mode: str = "rgbd_inertial",
    ) -> OrbSlam3Bundle:
        if mode not in SUPPORTED_MODES:
            raise ValueError(f"不支持的 ORB-SLAM3 模式: {mode}")
        if cv2 is None:
            raise RuntimeError("未安装 opencv-python，无法导出 ORB-SLAM3 图像 bundle")

        reader = SessionReader(session_dir)
        bundle_dir = Path(output_dir) if output_dir else Path(session_dir) / "exports" / f"orbslam3_{mode}"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        realsense_name = self._find_sensor(reader, "realsense")
        imu_name = self._find_sensor(reader, "imu")

        rgb_dir = bundle_dir / "rgb" if "rgbd" in mode or "mono" in mode else None
        depth_dir = bundle_dir / "depth" if "rgbd" in mode else None
        if rgb_dir is not None:
            rgb_dir.mkdir(parents=True, exist_ok=True)
        if depth_dir is not None:
            depth_dir.mkdir(parents=True, exist_ok=True)

        association_file = bundle_dir / "rgbd_associations.txt" if "rgbd" in mode else None
        rgb_timestamps_file = bundle_dir / "rgb_timestamps.txt" if rgb_dir is not None else None
        depth_timestamps_file = bundle_dir / "depth_timestamps.txt" if depth_dir is not None else None
        imu_file = bundle_dir / "imu.csv" if "inertial" in mode else None

        if realsense_name is None and rgb_dir is not None:
            raise RuntimeError("当前 session 中不存在 RealSense 数据，无法构建 ORB-SLAM3 图像 bundle")
        if "inertial" in mode and imu_name is None:
            raise RuntimeError("当前 session 中不存在 IMU 数据，无法构建 ORB-SLAM3 惯性 bundle")

        frame_count = 0
        if realsense_name is not None:
            frame_count = self._export_rgbd_frames(
                reader=reader,
                sensor_name=realsense_name,
                rgb_dir=rgb_dir,
                depth_dir=depth_dir,
                association_file=association_file,
                rgb_timestamps_file=rgb_timestamps_file,
                depth_timestamps_file=depth_timestamps_file,
            )

        imu_rows = 0
        if imu_name is not None and imu_file is not None:
            imu_rows = self._export_imu_frames(reader=reader, sensor_name=imu_name, imu_file=imu_file)

        manifest_path = bundle_dir / "bundle_manifest.json"
        manifest_payload = {
            "mode": mode,
            "session_id": reader.manifest.session_id,
            "frame_count": frame_count,
            "imu_rows": imu_rows,
            "realsense_sensor": realsense_name,
            "imu_sensor": imu_name,
            "files": {
                "rgb_dir": str(rgb_dir) if rgb_dir else None,
                "depth_dir": str(depth_dir) if depth_dir else None,
                "association_file": str(association_file) if association_file else None,
                "rgb_timestamps_file": str(rgb_timestamps_file) if rgb_timestamps_file else None,
                "depth_timestamps_file": str(depth_timestamps_file) if depth_timestamps_file else None,
                "imu_file": str(imu_file) if imu_file else None,
            },
        }
        manifest_path.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return OrbSlam3Bundle(
            mode=mode,
            bundle_dir=bundle_dir,
            manifest_path=manifest_path,
            rgb_dir=rgb_dir,
            depth_dir=depth_dir,
            association_file=association_file,
            rgb_timestamps_file=rgb_timestamps_file,
            depth_timestamps_file=depth_timestamps_file,
            imu_file=imu_file,
        )

    def _find_sensor(self, reader: SessionReader, sensor_type_fragment: str) -> str | None:
        for sensor_name, stream in reader.manifest.sensors.items():
            if sensor_type_fragment in stream.sensor_type:
                return sensor_name
        return None

    def _export_rgbd_frames(
        self,
        *,
        reader: SessionReader,
        sensor_name: str,
        rgb_dir: Path | None,
        depth_dir: Path | None,
        association_file: Path | None,
        rgb_timestamps_file: Path | None,
        depth_timestamps_file: Path | None,
    ) -> int:
        association_lines: list[str] = []
        rgb_lines: list[str] = []
        depth_lines: list[str] = []
        count = 0

        for frame in reader.iter_sensor_frames(sensor_name, load_payload=True):
            timestamp = f"{frame.time.host_time:.9f}"
            basename = f"{count:06d}.png"

            rgb_rel = None
            depth_rel = None

            color = frame.payload.get("color")
            if rgb_dir is not None and isinstance(color, np.ndarray):
                rgb_path = rgb_dir / basename
                cv2.imwrite(str(rgb_path), color)
                rgb_rel = f"rgb/{basename}"
                rgb_lines.append(f"{timestamp} {rgb_rel}")

            depth = frame.payload.get("depth")
            if depth_dir is not None and isinstance(depth, np.ndarray):
                depth_path = depth_dir / basename
                depth_uint16 = depth.astype(np.uint16, copy=False)
                cv2.imwrite(str(depth_path), depth_uint16)
                depth_rel = f"depth/{basename}"
                depth_lines.append(f"{timestamp} {depth_rel}")

            if association_file is not None and rgb_rel and depth_rel:
                association_lines.append(f"{timestamp} {rgb_rel} {timestamp} {depth_rel}")
            count += 1

        if association_file is not None:
            association_file.write_text("\n".join(association_lines) + ("\n" if association_lines else ""), encoding="utf-8")
        if rgb_timestamps_file is not None:
            rgb_timestamps_file.write_text("\n".join(rgb_lines) + ("\n" if rgb_lines else ""), encoding="utf-8")
        if depth_timestamps_file is not None:
            depth_timestamps_file.write_text("\n".join(depth_lines) + ("\n" if depth_lines else ""), encoding="utf-8")
        return count

    def _export_imu_frames(self, *, reader: SessionReader, sensor_name: str, imu_file: Path) -> int:
        lines = ["timestamp,ax,ay,az,gx,gy,gz,qw,qx,qy,qz"]
        count = 0
        for frame in reader.iter_sensor_frames(sensor_name, load_payload=True):
            acc = frame.payload.get("acceleration", [0.0, 0.0, 0.0])
            gyro = frame.payload.get("angular_velocity", [0.0, 0.0, 0.0])
            quat = frame.payload.get("quaternion", [1.0, 0.0, 0.0, 0.0])
            line = [
                f"{frame.time.host_time:.9f}",
                *[f"{float(value):.9f}" for value in acc],
                *[f"{float(value):.9f}" for value in gyro],
                *[f"{float(value):.9f}" for value in quat],
            ]
            lines.append(",".join(line))
            count += 1
        imu_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return count
