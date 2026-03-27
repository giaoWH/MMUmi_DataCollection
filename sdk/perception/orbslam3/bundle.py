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
    "rgbd_inertial",
    "stereo",
    "stereo_inertial",
}


@dataclass(frozen=True)
class OrbSlam3Bundle:
    mode: str
    bundle_dir: Path
    manifest_path: Path
    rgb_dir: Path | None = None
    depth_dir: Path | None = None
    left_dir: Path | None = None
    right_dir: Path | None = None
    association_file: Path | None = None
    rgb_timestamps_file: Path | None = None
    depth_timestamps_file: Path | None = None
    left_timestamps_file: Path | None = None
    right_timestamps_file: Path | None = None
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
        if self.left_dir is not None:
            values["left_dir"] = str(self.left_dir)
        if self.right_dir is not None:
            values["right_dir"] = str(self.right_dir)
        if self.association_file is not None:
            values["association_file"] = str(self.association_file)
            values["stereo_association_file"] = str(self.association_file)
        if self.rgb_timestamps_file is not None:
            values["rgb_timestamps_file"] = str(self.rgb_timestamps_file)
        if self.depth_timestamps_file is not None:
            values["depth_timestamps_file"] = str(self.depth_timestamps_file)
        if self.left_timestamps_file is not None:
            values["left_timestamps_file"] = str(self.left_timestamps_file)
        if self.right_timestamps_file is not None:
            values["right_timestamps_file"] = str(self.right_timestamps_file)
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
        if realsense_name is None:
            raise RuntimeError("当前 session 中不存在 RealSense 数据，无法构建 ORB-SLAM3 bundle")

        rgb_dir = bundle_dir / "rgb" if mode == "rgbd_inertial" else None
        depth_dir = bundle_dir / "depth" if mode == "rgbd_inertial" else None
        left_dir = bundle_dir / "left" if "stereo" in mode else None
        right_dir = bundle_dir / "right" if "stereo" in mode else None
        for path in (rgb_dir, depth_dir, left_dir, right_dir):
            if path is not None:
                path.mkdir(parents=True, exist_ok=True)

        association_file = (
            bundle_dir / "rgbd_associations.txt"
            if mode == "rgbd_inertial"
            else bundle_dir / "stereo_associations.txt"
        )
        rgb_timestamps_file = bundle_dir / "rgb_timestamps.txt" if rgb_dir is not None else None
        depth_timestamps_file = bundle_dir / "depth_timestamps.txt" if depth_dir is not None else None
        left_timestamps_file = bundle_dir / "left_timestamps.txt" if left_dir is not None else None
        right_timestamps_file = bundle_dir / "right_timestamps.txt" if right_dir is not None else None
        imu_file = bundle_dir / "imu.csv" if "inertial" in mode else None

        if mode == "rgbd_inertial":
            frame_count = self._export_rgbd_frames(
                reader=reader,
                sensor_name=realsense_name,
                rgb_dir=rgb_dir,
                depth_dir=depth_dir,
                association_file=association_file,
                rgb_timestamps_file=rgb_timestamps_file,
                depth_timestamps_file=depth_timestamps_file,
            )
        else:
            frame_count = self._export_stereo_frames(
                reader=reader,
                sensor_name=realsense_name,
                left_dir=left_dir,
                right_dir=right_dir,
                association_file=association_file,
                left_timestamps_file=left_timestamps_file,
                right_timestamps_file=right_timestamps_file,
            )
        if frame_count == 0:
            raise RuntimeError(f"当前 session 中不存在可用于 {mode} 的 RealSense 图像数据")

        imu_rows = 0
        if imu_file is not None:
            imu_rows = self._export_realsense_imu(
                reader=reader,
                sensor_name=realsense_name,
                imu_file=imu_file,
            )
            if imu_rows == 0:
                raise RuntimeError(f"当前 session 中不存在可用于 {mode} 的 RealSense IMU 数据")

        manifest_path = bundle_dir / "bundle_manifest.json"
        manifest_payload = {
            "mode": mode,
            "session_id": reader.manifest.session_id,
            "frame_count": frame_count,
            "imu_rows": imu_rows,
            "realsense_sensor": realsense_name,
            "imu_sensor": realsense_name if "inertial" in mode else None,
            "files": {
                "rgb_dir": str(rgb_dir) if rgb_dir else None,
                "depth_dir": str(depth_dir) if depth_dir else None,
                "left_dir": str(left_dir) if left_dir else None,
                "right_dir": str(right_dir) if right_dir else None,
                "association_file": str(association_file),
                "rgb_timestamps_file": str(rgb_timestamps_file) if rgb_timestamps_file else None,
                "depth_timestamps_file": str(depth_timestamps_file) if depth_timestamps_file else None,
                "left_timestamps_file": str(left_timestamps_file) if left_timestamps_file else None,
                "right_timestamps_file": str(right_timestamps_file) if right_timestamps_file else None,
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
            left_dir=left_dir,
            right_dir=right_dir,
            association_file=association_file,
            rgb_timestamps_file=rgb_timestamps_file,
            depth_timestamps_file=depth_timestamps_file,
            left_timestamps_file=left_timestamps_file,
            right_timestamps_file=right_timestamps_file,
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
        association_file: Path,
        rgb_timestamps_file: Path | None,
        depth_timestamps_file: Path | None,
    ) -> int:
        association_lines: list[str] = []
        rgb_lines: list[str] = []
        depth_lines: list[str] = []
        count = 0

        for frame in reader.iter_sensor_frames(sensor_name, load_payload=True):
            color = frame.payload.get("color")
            depth = frame.payload.get("aligned_depth_to_color")
            if not isinstance(color, np.ndarray) or not isinstance(depth, np.ndarray):
                continue

            timestamp = self._format_frame_device_timestamp(frame, stream_name="color")
            basename = f"{count:06d}.png"
            rgb_path = rgb_dir / basename
            depth_path = depth_dir / basename
            cv2.imwrite(str(rgb_path), color)
            cv2.imwrite(str(depth_path), depth.astype(np.uint16, copy=False))
            rgb_rel = f"rgb/{basename}"
            depth_rel = f"depth/{basename}"
            rgb_lines.append(f"{timestamp} {rgb_rel}")
            depth_lines.append(f"{timestamp} {depth_rel}")
            association_lines.append(f"{timestamp} {rgb_rel} {timestamp} {depth_rel}")
            count += 1

        association_file.write_text(
            "\n".join(association_lines) + ("\n" if association_lines else ""),
            encoding="utf-8",
        )
        if rgb_timestamps_file is not None:
            rgb_timestamps_file.write_text(
                "\n".join(rgb_lines) + ("\n" if rgb_lines else ""),
                encoding="utf-8",
            )
        if depth_timestamps_file is not None:
            depth_timestamps_file.write_text(
                "\n".join(depth_lines) + ("\n" if depth_lines else ""),
                encoding="utf-8",
            )
        return count

    def _export_stereo_frames(
        self,
        *,
        reader: SessionReader,
        sensor_name: str,
        left_dir: Path | None,
        right_dir: Path | None,
        association_file: Path,
        left_timestamps_file: Path | None,
        right_timestamps_file: Path | None,
    ) -> int:
        association_lines: list[str] = []
        left_lines: list[str] = []
        right_lines: list[str] = []
        count = 0

        for frame in reader.iter_sensor_frames(sensor_name, load_payload=True):
            left = frame.payload.get("ir1")
            right = frame.payload.get("ir2")
            if not isinstance(left, np.ndarray) or not isinstance(right, np.ndarray):
                continue

            timestamp = self._format_frame_device_timestamp(frame, stream_name="ir1")
            basename = f"{count:06d}.png"
            left_path = left_dir / basename
            right_path = right_dir / basename
            cv2.imwrite(str(left_path), left.astype(np.uint8, copy=False))
            cv2.imwrite(str(right_path), right.astype(np.uint8, copy=False))
            left_rel = f"left/{basename}"
            right_rel = f"right/{basename}"
            left_lines.append(f"{timestamp} {left_rel}")
            right_lines.append(f"{timestamp} {right_rel}")
            association_lines.append(f"{timestamp} {left_rel} {timestamp} {right_rel}")
            count += 1

        association_file.write_text(
            "\n".join(association_lines) + ("\n" if association_lines else ""),
            encoding="utf-8",
        )
        if left_timestamps_file is not None:
            left_timestamps_file.write_text(
                "\n".join(left_lines) + ("\n" if left_lines else ""),
                encoding="utf-8",
            )
        if right_timestamps_file is not None:
            right_timestamps_file.write_text(
                "\n".join(right_lines) + ("\n" if right_lines else ""),
                encoding="utf-8",
            )
        return count

    def _export_realsense_imu(
        self,
        *,
        reader: SessionReader,
        sensor_name: str,
        imu_file: Path,
    ) -> int:
        lines = ["timestamp,sample_type,x,y,z"]
        count = 0
        for frame in reader.iter_sensor_frames(sensor_name, load_payload=True):
            imu_samples = frame.payload.get("imu_samples", [])
            if not isinstance(imu_samples, list):
                continue
            for sample in imu_samples:
                if not isinstance(sample, dict):
                    continue
                timestamp_ms = sample.get("timestamp_ms")
                if timestamp_ms is None:
                    raise RuntimeError("RealSense IMU 数据缺少设备时间戳，无法导出 stereo_inertial bundle")
                timestamp = f"{float(timestamp_ms) / 1000.0:.9f}"
                lines.append(
                    ",".join(
                        [
                            timestamp,
                            str(sample.get("sample_type", "unknown")),
                            f"{float(sample.get('x', 0.0)):.9f}",
                            f"{float(sample.get('y', 0.0)):.9f}",
                            f"{float(sample.get('z', 0.0)):.9f}",
                        ]
                    )
                )
                count += 1
        imu_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return count

    def _format_frame_device_timestamp(self, frame: Any, *, stream_name: str) -> str:
        if frame.time.device_time is not None:
            return f"{frame.time.device_time:.9f}"
        frame_info = frame.metadata.get("frame_info", {}) if isinstance(frame.metadata, dict) else {}
        stream_info = frame_info.get(stream_name, {}) if isinstance(frame_info, dict) else {}
        raw_timestamp_ms = stream_info.get("timestamp_ms") if isinstance(stream_info, dict) else None
        if raw_timestamp_ms is not None:
            return f"{float(raw_timestamp_ms) / 1000.0:.9f}"
        raise RuntimeError(
            f"RealSense {stream_name} 帧缺少设备时间戳，无法导出 ORB-SLAM3 bundle"
        )
