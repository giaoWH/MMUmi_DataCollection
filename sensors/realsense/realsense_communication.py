from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pyrealsense2 as rs

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sensors.realsense_sensor import (  # noqa: E402
    RealsenseSensor,
    depth_to_preview,
    infrared_to_preview,
)


def save_pointcloud_as_ply(path: str | Path, vertices: np.ndarray, colors: np.ndarray | None = None) -> Path:
    target = Path(path)
    vertices = np.asarray(vertices, dtype=np.float32).reshape(-1, 3)
    colors = None if colors is None else np.asarray(colors, dtype=np.uint8).reshape(-1, 3)

    with target.open("w", encoding="utf-8") as handle:
        header = [
            "ply",
            "format ascii 1.0",
            f"element vertex {len(vertices)}",
            "property float x",
            "property float y",
            "property float z",
        ]
        if colors is not None:
            header.extend(
                [
                    "property uchar red",
                    "property uchar green",
                    "property uchar blue",
                ]
            )
        header.append("end_header")
        handle.write("\n".join(header))
        handle.write("\n")
        for index, vertex in enumerate(vertices):
            if colors is None:
                handle.write(f"{vertex[0]:.6f} {vertex[1]:.6f} {vertex[2]:.6f}\n")
            else:
                color = colors[index]
                handle.write(
                    f"{vertex[0]:.6f} {vertex[1]:.6f} {vertex[2]:.6f} "
                    f"{int(color[0])} {int(color[1])} {int(color[2])}\n"
                )
    return target


def _colorize_depth(depth: np.ndarray | None) -> np.ndarray | None:
    if depth is None:
        return None
    preview = depth_to_preview(depth)
    return cv2.applyColorMap(preview, cv2.COLORMAP_JET)


def _tile(image: np.ndarray | None, label: str) -> np.ndarray:
    if image is None:
        image = np.zeros((240, 320, 3), dtype=np.uint8)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    image = cv2.resize(image, (320, 240), interpolation=cv2.INTER_NEAREST)
    cv2.putText(image, label, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
    return image


def _build_preview(payload: dict[str, object]) -> np.ndarray | None:
    tiles = []
    color = payload.get("color")
    if isinstance(color, np.ndarray):
        tiles.append(_tile(color, "color"))

    depth = payload.get("depth")
    if isinstance(depth, np.ndarray):
        tiles.append(_tile(_colorize_depth(depth), "depth"))

    ir1 = payload.get("ir1")
    if isinstance(ir1, np.ndarray):
        tiles.append(_tile(cv2.cvtColor(infrared_to_preview(ir1), cv2.COLOR_GRAY2BGR), "ir1"))

    ir2 = payload.get("ir2")
    if isinstance(ir2, np.ndarray):
        tiles.append(_tile(cv2.cvtColor(infrared_to_preview(ir2), cv2.COLOR_GRAY2BGR), "ir2"))

    aligned = payload.get("aligned_depth_to_color")
    if isinstance(aligned, np.ndarray):
        tiles.append(_tile(_colorize_depth(aligned), "aligned_depth"))

    if not tiles:
        return None

    rows = []
    for start in range(0, len(tiles), 2):
        row = tiles[start : start + 2]
        if len(row) == 1:
            row.append(np.zeros_like(row[0]))
        rows.append(np.hstack(row))
    return np.vstack(rows)


def _save_artifacts(output_dir: Path, payload: dict[str, object]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    color = payload.get("color")
    if isinstance(color, np.ndarray):
        cv2.imwrite(str(output_dir / "color.png"), color)

    depth = payload.get("depth")
    if isinstance(depth, np.ndarray):
        cv2.imwrite(str(output_dir / "depth.png"), depth.astype(np.uint16, copy=False))

    ir1 = payload.get("ir1")
    if isinstance(ir1, np.ndarray):
        cv2.imwrite(str(output_dir / "ir1.png"), ir1)

    ir2 = payload.get("ir2")
    if isinstance(ir2, np.ndarray):
        cv2.imwrite(str(output_dir / "ir2.png"), ir2)

    aligned = payload.get("aligned_depth_to_color")
    if isinstance(aligned, np.ndarray):
        cv2.imwrite(str(output_dir / "aligned_depth_to_color.png"), aligned.astype(np.uint16, copy=False))

    pointcloud = payload.get("pointcloud")
    if isinstance(pointcloud, dict) and isinstance(pointcloud.get("vertices"), np.ndarray):
        save_pointcloud_as_ply(output_dir / "pointcloud.ply", pointcloud["vertices"], pointcloud.get("colors"))


def main() -> None:
    parser = argparse.ArgumentParser(description="RealSense D435i bring-up / 连通性测试")
    parser.add_argument("--save-dir", default="realsense_debug_output", help="退出时保存样例产物的目录")
    args = parser.parse_args()

    print(f"pyrealsense2 已加载: pipeline={hasattr(rs, 'pipeline')}, align={hasattr(rs, 'align')}, pointcloud={hasattr(rs, 'pointcloud')}")

    sensor = RealsenseSensor()
    last_payload = None
    last_log_time = 0.0
    save_dir = Path(args.save_dir)

    print("启动 RealSense 测试，按 q / ESC 退出")
    sensor.start()
    try:
        while True:
            payload, timestamp, frame_id, _time_info = sensor.get_data_with_time_info()
            if payload is None:
                time.sleep(0.01)
                continue

            last_payload = payload
            preview = _build_preview(payload)
            if preview is not None:
                cv2.imshow("RealSense Bring-up", preview)

            now = time.time()
            if now - last_log_time >= 1.0:
                imu_count = len(payload.get("imu_samples", []))
                pointcloud = payload.get("pointcloud")
                point_count = 0
                if isinstance(pointcloud, dict) and isinstance(pointcloud.get("vertices"), np.ndarray):
                    point_count = int(pointcloud["vertices"].shape[0])
                print(
                    f"\rframe={frame_id:06d} timestamp={timestamp:.6f} "
                    f"imu_batch={imu_count:03d} pointcloud={point_count:06d}",
                    end="",
                    flush=True,
                )
                last_log_time = now

            key = cv2.waitKey(1) & 0xFF
            if key in {27, ord('q')}:
                break
    except KeyboardInterrupt:
        pass
    finally:
        print("\n正在停止 RealSense 测试...")
        sensor.stop()
        cv2.destroyAllWindows()
        if last_payload is not None:
            _save_artifacts(save_dir, last_payload)
            print(f"样例数据已保存到: {save_dir}")
        else:
            print("未采集到有效数据")


if __name__ == "__main__":
    main()
