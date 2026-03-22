from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pyrealsense2 as rs

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.config import load_config_file  # noqa: E402
from sdk.sensors.realsense import RealSenseConfig as SDKRealSenseConfig  # noqa: E402
from sensors.realsense_sensor import (  # noqa: E402
    RealsenseConfig,
    RealsenseSensor,
    depth_to_preview,
    infrared_to_preview,
)

DEFAULT_RECORD_CONFIG_CANDIDATES = (
    REPO_ROOT / "configs" / "record.yaml",
    REPO_ROOT / "configs" / "record.yml",
    REPO_ROOT / "configs" / "record.json",
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


def _find_default_config_path() -> Path | None:
    for candidate in DEFAULT_RECORD_CONFIG_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _load_sensor_config(config_path: Path | None) -> tuple[RealsenseConfig, Path | None]:
    if config_path is None:
        return RealsenseConfig(), None

    payload = load_config_file(config_path)
    realsense_section = payload.get("realsense")
    if realsense_section is None:
        raise ValueError(f"配置文件缺少 realsense 段: {config_path}")
    if not isinstance(realsense_section, dict):
        raise ValueError(f"realsense 配置必须是对象: {config_path}")
    return SDKRealSenseConfig.from_dict(realsense_section).to_low_level_config(), config_path


def _summarize_config(config: RealsenseConfig) -> str:
    enabled_streams: list[str] = []
    if config.enable_color:
        enabled_streams.append(f"color={config.color.width}x{config.color.height}@{config.color.fps}")
    if config.enable_depth:
        enabled_streams.append(f"depth={config.depth.width}x{config.depth.height}@{config.depth.fps}")
    if config.enable_ir1:
        enabled_streams.append(f"ir1={config.infrared.width}x{config.infrared.height}@{config.infrared.fps}")
    if config.enable_ir2:
        enabled_streams.append(f"ir2={config.infrared.width}x{config.infrared.height}@{config.infrared.fps}")
    if config.enable_imu:
        enabled_streams.append(
            f"imu=accel@{config.imu.accel_fps},gyro@{config.imu.gyro_fps}"
        )
    derived: list[str] = []
    if config.enable_aligned_depth_to_color:
        derived.append("aligned_depth_to_color")
    if config.enable_pointcloud:
        derived.append("pointcloud")
    enabled_streams_text = ", ".join(enabled_streams) if enabled_streams else "none"
    derived_text = ", ".join(derived) if derived else "none"
    return f"streams=[{enabled_streams_text}] derived=[{derived_text}] serial={config.serial_number or 'auto'}"


def _gui_available() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main() -> None:
    parser = argparse.ArgumentParser(description="RealSense D435i bring-up / 连通性测试")
    parser.add_argument("--save-dir", default="realsense_debug_output", help="退出时保存样例产物的目录")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="可选：指定 record.yaml/json；未提供时自动尝试 configs/record.yaml",
    )
    parser.add_argument(
        "--use-default-config",
        action="store_true",
        help="忽略 record 配置，强制使用底层传感器的内置默认配置",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="禁用 OpenCV 预览窗口，适合 SSH / tty / 无桌面环境",
    )
    args = parser.parse_args()

    print(f"pyrealsense2 已加载: pipeline={hasattr(rs, 'pipeline')}, align={hasattr(rs, 'align')}, pointcloud={hasattr(rs, 'pointcloud')}")

    config_path = None if args.use_default_config else (args.config or _find_default_config_path())
    sensor_config, loaded_config_path = _load_sensor_config(config_path)
    if loaded_config_path is not None:
        print(f"已加载 RealSense 配置: {loaded_config_path}")
    else:
        print("未找到 record 配置，使用底层传感器默认配置")
    print(f"当前 RealSense 配置: {_summarize_config(sensor_config)}")

    enable_gui = not args.no_gui
    if enable_gui and not _gui_available():
        enable_gui = False
        print("未检测到图形显示环境，自动禁用 OpenCV 预览窗口")
        print("如需预览，请在桌面会话中运行，或显式传 --no-gui 仅做采集连通性测试")

    sensor = RealsenseSensor(sensor_config)
    last_payload = None
    last_log_time = 0.0
    save_dir = Path(args.save_dir)

    if enable_gui:
        print("启动 RealSense 测试，按 q / ESC 退出")
    else:
        print("启动 RealSense 测试，当前为无界面模式，按 Ctrl-C 退出")
    sensor.start()
    try:
        while True:
            payload, timestamp, frame_id, _time_info = sensor.get_data_with_time_info()
            if payload is None:
                time.sleep(0.01)
                continue

            last_payload = payload
            if enable_gui:
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

            if enable_gui:
                key = cv2.waitKey(1) & 0xFF
                if key in {27, ord('q')}:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        print("\n正在停止 RealSense 测试...")
        sensor.stop()
        if enable_gui:
            cv2.destroyAllWindows()
        if last_payload is not None:
            _save_artifacts(save_dir, last_payload)
            print(f"样例数据已保存到: {save_dir}")
        else:
            print("未采集到有效数据")


if __name__ == "__main__":
    main()
