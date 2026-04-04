from __future__ import annotations

import argparse
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from serial.tools import list_ports

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.config import load_config_file

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    import pyrealsense2 as rs
except Exception:  # pragma: no cover
    rs = None


DEFAULT_RECORD_CONFIG_CANDIDATES = (
    REPO_ROOT / "configs" / "record.yaml",
    REPO_ROOT / "configs" / "record.yml",
    REPO_ROOT / "configs" / "record.json",
)


@dataclass(frozen=True)
class PortSensorSpec:
    sensor_key: str
    enable_key: str
    label: str
    config_key: str
    port_key: str = "port"


@dataclass(frozen=True)
class VisualSensorSpec:
    sensor_key: str
    enable_key: str
    label: str
    config_key: str
    assignment_kind: str
    config_field: str


@dataclass(frozen=True)
class VideoDeviceInfo:
    index: int
    width: int
    height: int
    fps: float
    name: str | None = None


@dataclass(frozen=True)
class RealSenseDeviceInfo:
    serial_number: str
    name: str


PORT_SENSOR_SPECS = {
    "ft": PortSensorSpec(
        sensor_key="ft",
        enable_key="enable_ft",
        label="FT",
        config_key="ft",
    ),
    "imu": PortSensorSpec(
        sensor_key="imu",
        enable_key="enable_imu",
        label="IMU",
        config_key="imu",
    ),
    "motors": PortSensorSpec(
        sensor_key="motors",
        enable_key="enable_motors",
        label="Motors",
        config_key="motors",
    ),
}


VISUAL_SENSOR_SPECS = {
    "realsense": VisualSensorSpec(
        sensor_key="realsense",
        enable_key="enable_realsense",
        label="RealSense",
        config_key="realsense",
        assignment_kind="realsense_serial",
        config_field="serial_number",
    ),
    "camera": VisualSensorSpec(
        sensor_key="camera",
        enable_key="enable_camera",
        label="Camera",
        config_key="camera",
        assignment_kind="uvc_index",
        config_field="device_index",
    ),
    "gelsight": VisualSensorSpec(
        sensor_key="gelsight",
        enable_key="enable_gelsight",
        label="GelSight",
        config_key="gelsight",
        assignment_kind="uvc_index",
        config_field="device_index",
    ),
}


def resolve_record_config_path(config_path: str | None) -> Path:
    if config_path:
        raw_path = Path(config_path)
        candidates = [raw_path]
        if not raw_path.is_absolute():
            candidates.append(REPO_ROOT / raw_path)
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    for candidate in DEFAULT_RECORD_CONFIG_CANDIDATES:
        if candidate.exists():
            return candidate.resolve()

    raise FileNotFoundError("未找到录制配置文件，请创建 configs/record.yaml 或通过 --config 指定")


def build_port_discovery_plan(
    config_payload: dict[str, object],
) -> tuple[list[PortSensorSpec], list[str]]:
    steps: list[PortSensorSpec] = []
    skipped: list[str] = []

    for key, value in config_payload.items():
        if not key.startswith("enable_") or not value:
            continue

        sensor_key = key.removeprefix("enable_")
        spec = PORT_SENSOR_SPECS.get(sensor_key)
        if spec is None:
            skipped.append(sensor_key)
            continue

        steps.append(spec)
    return steps, skipped


def build_visual_discovery_plan(
    config_payload: dict[str, object],
) -> tuple[list[VisualSensorSpec], list[str]]:
    steps: list[VisualSensorSpec] = []
    skipped: list[str] = []

    for key, value in config_payload.items():
        if not key.startswith("enable_") or not value:
            continue

        sensor_key = key.removeprefix("enable_")
        spec = VISUAL_SENSOR_SPECS.get(sensor_key)
        if spec is None:
            skipped.append(sensor_key)
            continue

        steps.append(spec)
    return steps, skipped


def snapshot_serial_ports() -> dict[str, str]:
    ports: dict[str, str] = {}
    for port in list_ports.comports():
        description = port.description or port.name or ""
        ports[port.device] = description
    return dict(sorted(ports.items()))


def detect_new_ports(previous_ports: dict[str, str], current_ports: dict[str, str]) -> list[str]:
    return sorted(device for device in current_ports if device not in previous_ports)


def wait_for_port_change(
    previous_ports: dict[str, str],
    *,
    timeout: float,
    poll_interval: float,
) -> dict[str, str]:
    deadline = time.time() + timeout
    latest_ports = snapshot_serial_ports()

    while time.time() < deadline:
        latest_ports = snapshot_serial_ports()
        if set(latest_ports) != set(previous_ports):
            return latest_ports
        time.sleep(poll_interval)
    return latest_ports


def set_sensor_port(config_payload: dict[str, object], spec: PortSensorSpec, port: str) -> None:
    sensor_config = config_payload.get(spec.config_key)
    if not isinstance(sensor_config, dict):
        sensor_config = {}
        config_payload[spec.config_key] = sensor_config
    sensor_config[spec.port_key] = port


def set_sensor_config_value(
    config_payload: dict[str, object],
    spec: VisualSensorSpec,
    value: str | int | None,
) -> None:
    sensor_config = config_payload.get(spec.config_key)
    if not isinstance(sensor_config, dict):
        sensor_config = {}
        config_payload[spec.config_key] = sensor_config
    sensor_config[spec.config_field] = value


def extract_leading_comments(path: Path) -> str:
    if path.suffix.lower() not in {".yaml", ".yml"}:
        return ""

    lines = path.read_text(encoding="utf-8").splitlines()
    comment_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or stripped == "":
            comment_lines.append(line)
            continue
        break
    if not comment_lines:
        return ""
    return "\n".join(comment_lines).rstrip() + "\n\n"


def write_config_payload(path: Path, payload: dict[str, object], *, create_backup: bool = True) -> Path | None:
    backup_path = None
    if create_backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        shutil.copyfile(path, backup_path)

    if path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        leading_comments = extract_leading_comments(path)
        dumped = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        path.write_text(leading_comments + dumped, encoding="utf-8")
    else:
        import json

        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return backup_path


def print_port_snapshot(title: str, ports: dict[str, str]) -> None:
    print(title)
    if not ports:
        print("  (未检测到串口设备)")
        return
    for device, description in ports.items():
        print(f"  - {device}: {description}")


def print_video_devices(title: str, devices: list[VideoDeviceInfo]) -> None:
    print(title)
    if not devices:
        print("  (未检测到可用视频设备)")
        return
    for item in devices:
        name = item.name or "unknown"
        fps_text = f"{item.fps:.1f}" if item.fps > 0 else "?"
        print(f"  - index={item.index:<2} name={name} frame={item.width}x{item.height} fps={fps_text}")


def print_realsense_devices(title: str, devices: list[RealSenseDeviceInfo]) -> None:
    print(title)
    if not devices:
        print("  (未检测到 RealSense 设备)")
        return
    for item in devices:
        print(f"  - serial={item.serial_number} name={item.name}")


def prompt_manual_port(current_ports: dict[str, str]) -> str | None:
    print_port_snapshot("当前可见串口：", current_ports)
    raw = input("请输入目标 port（直接回车表示取消手动输入）: ").strip()
    return raw or None


def lookup_video_device_name(index: int) -> str | None:
    linux_name_path = Path(f"/sys/class/video4linux/video{index}/name")
    if linux_name_path.exists():
        return linux_name_path.read_text(encoding="utf-8", errors="ignore").strip() or None
    return None


def scan_video_devices(
    *,
    max_index: int,
    include_realsense_nodes: bool = False,
) -> list[VideoDeviceInfo]:
    if cv2 is None:
        raise RuntimeError("未安装 opencv-python，无法扫描 UVC 相机")

    devices: list[VideoDeviceInfo] = []
    for index in range(max_index + 1):
        cap = cv2.VideoCapture(index)
        try:
            if not cap.isOpened():
                continue
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            name = lookup_video_device_name(index)
            if not include_realsense_nodes and name and "realsense" in name.lower():
                continue
            devices.append(
                VideoDeviceInfo(
                    index=index,
                    width=width,
                    height=height,
                    fps=fps,
                    name=name,
                )
            )
        finally:
            cap.release()
    return devices


def discover_realsense_devices() -> list[RealSenseDeviceInfo]:
    if rs is None:
        return []

    devices: list[RealSenseDeviceInfo] = []
    context = rs.context()
    for device in context.query_devices():
        try:
            serial_number = device.get_info(rs.camera_info.serial_number)
            name = device.get_info(rs.camera_info.name)
        except Exception:
            continue
        devices.append(
            RealSenseDeviceInfo(
                serial_number=str(serial_number),
                name=str(name),
            )
        )
    return devices


def preview_video_device(index: int, *, preview_sec: float, label: str) -> None:
    if cv2 is None:
        raise RuntimeError("未安装 opencv-python，无法预览相机")

    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f"无法打开 index={index} 进行预览。")
        return

    window_name = f"{label} Preview (index={index})"
    deadline = time.time() + max(preview_sec, 0.1)
    try:
        while time.time() < deadline:
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.03)
                continue
            cv2.putText(
                frame,
                f"{label} index={index}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow(window_name, frame)
            if cv2.waitKey(1) & 0xFF == 27:
                break
        cv2.destroyWindow(window_name)
    finally:
        cap.release()


def prompt_for_uvc_index(
    spec: VisualSensorSpec,
    *,
    devices: list[VideoDeviceInfo],
    preview_sec: float,
    used_indices: set[int],
) -> int | None | str:
    while True:
        available_devices = [item for item in devices if item.index not in used_indices]
        print()
        print(f"准备为 {spec.label} 选择 device_index")
        print_video_devices("当前候选视频设备：", available_devices)
        raw = input(
            "请输入 device_index；输入 'p <index>' 预览，'r' 重新扫描，'s' 跳过，'q' 退出: "
        ).strip()
        if not raw:
            continue
        if raw.lower() == "r":
            return "rescan"
        if raw.lower() == "s":
            return None
        if raw.lower() == "q":
            raise KeyboardInterrupt
        if raw.lower().startswith("p"):
            parts = raw.split()
            if len(parts) != 2 or not parts[1].isdigit():
                print("预览命令格式应为: p <index>")
                continue
            preview_video_device(int(parts[1]), preview_sec=preview_sec, label=spec.label)
            continue
        if raw.isdigit():
            chosen = int(raw)
            valid_indices = {item.index for item in available_devices}
            if chosen not in valid_indices:
                print(f"index={chosen} 不在当前候选列表中。")
                continue
            return chosen
        print("无效输入，请重新输入。")


def prompt_for_realsense_serial(
    spec: VisualSensorSpec,
    devices: list[RealSenseDeviceInfo],
) -> tuple[bool, str | None]:
    if not devices:
        print(f"未检测到 {spec.label}，保持原配置不变。")
        return False, None
    if len(devices) == 1:
        item = devices[0]
        print(f"自动识别到唯一的 {spec.label}: {item.name} ({item.serial_number})")
        print(f"将 {spec.config_key}.{spec.config_field} 写回为 null，运行时自动选择该设备。")
        return True, None

    print()
    print(f"检测到多个 {spec.label}，请选择要写入的 serial_number：")
    for index, item in enumerate(devices, start=1):
        print(f"  {index}. {item.name} ({item.serial_number})")
    while True:
        raw = input("请输入编号；输入 's' 跳过，'q' 退出: ").strip().lower()
        if raw == "s":
            return False, None
        if raw == "q":
            raise KeyboardInterrupt
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(devices):
                return True, devices[index - 1].serial_number
        print("无效输入，请重新选择。")


def format_resolved_value(value: str | int | None) -> str:
    if value is None:
        return "auto"
    return str(value)


def _discover_serial_assignments(
    config_payload: dict[str, object],
    *,
    timeout: float,
    poll_interval: float,
) -> tuple[dict[str, str], list[str]]:
    steps, skipped = build_port_discovery_plan(config_payload)
    if not steps:
        return {}, skipped

    print("=== 串口自动发现 ===")
    print("将按以下顺序识别串口：")
    for index, spec in enumerate(steps, start=1):
        print(f"  {index}. {spec.label} -> {spec.config_key}.{spec.port_key}")
    if skipped:
        print(f"以下已启用设备不参与串口发现: {', '.join(skipped)}")
    print("识别过程中，已识别成功的设备可以保持连接。")
    input("请先拔掉所有待识别的串口传感器，然后按 Enter 开始...")

    previous_ports = snapshot_serial_ports()
    print_port_snapshot("当前基线串口列表：", previous_ports)

    resolved_ports: dict[str, str] = {}
    for spec in steps:
        print()
        print(f"准备识别 {spec.label}")
        while True:
            input(f"请插上 {spec.label}，然后按 Enter 开始扫描...")
            current_ports = wait_for_port_change(
                previous_ports,
                timeout=timeout,
                poll_interval=poll_interval,
            )
            new_ports = detect_new_ports(previous_ports, current_ports)

            if len(new_ports) == 1:
                assigned_port = new_ports[0]
                set_sensor_port(config_payload, spec, assigned_port)
                resolved_ports[spec.sensor_key] = assigned_port
                print(f"已识别 {spec.label}: {assigned_port}")
                previous_ports = current_ports
                break

            print_port_snapshot("扫描后的串口列表：", current_ports)
            if len(new_ports) > 1:
                print(f"检测到多个新增串口: {', '.join(new_ports)}")
            else:
                print(f"未检测到 {spec.label} 对应的新串口。")

            choice = input("输入 [r] 重试, [m] 手动输入, [s] 跳过, [q] 退出: ").strip().lower()
            if not choice:
                choice = "r"

            if choice == "r":
                continue
            if choice == "m":
                assigned_port = prompt_manual_port(current_ports)
                if assigned_port:
                    set_sensor_port(config_payload, spec, assigned_port)
                    resolved_ports[spec.sensor_key] = assigned_port
                    print(f"已手动设置 {spec.label}: {assigned_port}")
                    previous_ports = current_ports
                    break
                continue
            if choice == "s":
                print(f"跳过 {spec.label}，保持原配置不变。")
                previous_ports = current_ports
                break
            if choice == "q":
                raise KeyboardInterrupt
            print("无效输入，请重新选择。")

    return resolved_ports, skipped


def _discover_visual_assignments(
    config_payload: dict[str, object],
    *,
    max_index: int,
    preview_sec: float,
    include_realsense_nodes: bool,
) -> tuple[dict[str, str | int | None], list[str]]:
    steps, skipped = build_visual_discovery_plan(config_payload)
    if not steps:
        return {}, skipped

    print("=== 视觉设备自动发现 ===")
    print("将按以下顺序识别视觉设备：")
    for index, spec in enumerate(steps, start=1):
        print(f"  {index}. {spec.label} -> {spec.config_key}.{spec.config_field}")
    if skipped:
        print(f"以下已启用设备不参与视觉发现: {', '.join(skipped)}")

    resolved_values: dict[str, str | int | None] = {}
    used_uvc_indices: set[int] = set()
    cached_video_devices: list[VideoDeviceInfo] | None = None

    for spec in steps:
        if spec.assignment_kind == "realsense_serial":
            devices = discover_realsense_devices()
            print()
            print_realsense_devices("当前 RealSense 设备：", devices)
            should_update, serial_number = prompt_for_realsense_serial(spec, devices)
            if not should_update:
                continue
            set_sensor_config_value(config_payload, spec, serial_number)
            resolved_values[spec.sensor_key] = serial_number
            continue

        while True:
            if cached_video_devices is None:
                cached_video_devices = scan_video_devices(
                    max_index=max_index,
                    include_realsense_nodes=include_realsense_nodes,
                )
            chosen = prompt_for_uvc_index(
                spec,
                devices=cached_video_devices,
                preview_sec=preview_sec,
                used_indices=used_uvc_indices,
            )
            if chosen == "rescan":
                cached_video_devices = None
                continue
            if chosen is None:
                break
            set_sensor_config_value(config_payload, spec, chosen)
            resolved_values[spec.sensor_key] = chosen
            used_uvc_indices.add(chosen)
            break

    return resolved_values, skipped


def run_port_discovery(
    config_path: Path,
    *,
    timeout: float,
    poll_interval: float,
    create_backup: bool,
) -> int:
    config_payload = load_config_file(config_path)
    try:
        resolved_ports, skipped = _discover_serial_assignments(
            config_payload,
            timeout=timeout,
            poll_interval=poll_interval,
        )
    except KeyboardInterrupt:
        print("用户主动退出，未写回配置文件。")
        return 1

    if not resolved_ports and not build_port_discovery_plan(config_payload)[0]:
        print("配置中没有启用需要自动发现 port 的串口传感器。")
        if skipped:
            print(f"已启用但不参与串口发现的设备: {', '.join(skipped)}")
        return 0

    backup_path = write_config_payload(config_path, config_payload, create_backup=create_backup)
    print()
    print("串口发现完成，已写回配置文件。")
    for sensor_key, port in resolved_ports.items():
        print(f"  - {sensor_key}: {port}")
    if backup_path is not None:
        print(f"已生成备份文件: {backup_path}")
    return 0


def run_visual_discovery(
    config_path: Path,
    *,
    max_index: int,
    preview_sec: float,
    include_realsense_nodes: bool,
    create_backup: bool,
) -> int:
    config_payload = load_config_file(config_path)
    try:
        resolved_values, skipped = _discover_visual_assignments(
            config_payload,
            max_index=max_index,
            preview_sec=preview_sec,
            include_realsense_nodes=include_realsense_nodes,
        )
    except KeyboardInterrupt:
        print("用户主动退出，未写回配置文件。")
        return 1

    if not resolved_values and not build_visual_discovery_plan(config_payload)[0]:
        print("配置中没有启用需要自动发现的视觉设备。")
        if skipped:
            print(f"已启用但不参与视觉发现的设备: {', '.join(skipped)}")
        return 0

    backup_path = write_config_payload(config_path, config_payload, create_backup=create_backup)
    print()
    print("视觉设备发现完成，已写回配置文件。")
    if resolved_values:
        for sensor_key, value in resolved_values.items():
            print(f"  - {sensor_key}: {format_resolved_value(value)}")
    else:
        print("  (没有更新任何设备配置)")
    if backup_path is not None:
        print(f"已生成备份文件: {backup_path}")
    return 0


def run_device_discovery(
    config_path: Path,
    *,
    timeout: float,
    poll_interval: float,
    max_index: int,
    preview_sec: float,
    include_realsense_nodes: bool,
    create_backup: bool,
) -> int:
    config_payload = load_config_file(config_path)
    print("=== 设备自动发现 ===")
    print(f"配置文件: {config_path}")

    try:
        resolved_ports, serial_skipped = _discover_serial_assignments(
            config_payload,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        print()
        resolved_visual, visual_skipped = _discover_visual_assignments(
            config_payload,
            max_index=max_index,
            preview_sec=preview_sec,
            include_realsense_nodes=include_realsense_nodes,
        )
    except KeyboardInterrupt:
        print("用户主动退出，未写回配置文件。")
        return 1

    has_serial_steps = bool(build_port_discovery_plan(config_payload)[0])
    has_visual_steps = bool(build_visual_discovery_plan(config_payload)[0])
    if not has_serial_steps and not has_visual_steps:
        print("配置中没有启用需要自动发现的串口或视觉设备。")
        skipped = sorted(set(serial_skipped + visual_skipped))
        if skipped:
            print(f"已启用但不参与自动发现的设备: {', '.join(skipped)}")
        return 0

    backup_path = write_config_payload(config_path, config_payload, create_backup=create_backup)
    print()
    print("设备发现完成，已写回配置文件。")
    if resolved_ports:
        print("串口设备：")
        for sensor_key, port in resolved_ports.items():
            print(f"  - {sensor_key}: {port}")
    if resolved_visual:
        print("视觉设备：")
        for sensor_key, value in resolved_visual.items():
            print(f"  - {sensor_key}: {format_resolved_value(value)}")
    if not resolved_ports and not resolved_visual:
        print("  (没有更新任何设备配置)")
    if backup_path is not None:
        print(f"已生成备份文件: {backup_path}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="根据 record.yaml 引导式自动发现设备并写回配置")
    parser.add_argument(
        "--config",
        default=None,
        help="录制配置文件路径；未提供时默认尝试 configs/record.yaml",
    )
    parser.add_argument(
        "--scope",
        choices=["all", "serial", "visual"],
        default="all",
        help="发现范围：all=串口+视觉，serial=仅串口，visual=仅视觉",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="每次等待新串口出现的最长秒数")
    parser.add_argument("--poll-interval", type=float, default=0.5, help="轮询串口变化的时间间隔")
    parser.add_argument("--max-index", type=int, default=15, help="扫描 UVC 相机的最大 device_index")
    parser.add_argument("--preview-sec", type=float, default=2.0, help="单次预览窗口的展示秒数")
    parser.add_argument(
        "--include-realsense-video-nodes",
        action="store_true",
        help="扫描 UVC 相机时包含名称中带 RealSense 的 video 节点",
    )
    parser.add_argument("--no-backup", action="store_true", help="写回配置时不生成 .bak 备份文件")
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    config_path = resolve_record_config_path(args.config)
    if args.scope == "serial":
        return run_port_discovery(
            config_path,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            create_backup=not args.no_backup,
        )
    if args.scope == "visual":
        return run_visual_discovery(
            config_path,
            max_index=args.max_index,
            preview_sec=args.preview_sec,
            include_realsense_nodes=args.include_realsense_video_nodes,
            create_backup=not args.no_backup,
        )
    return run_device_discovery(
        config_path,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
        max_index=args.max_index,
        preview_sec=args.preview_sec,
        include_realsense_nodes=args.include_realsense_video_nodes,
        create_backup=not args.no_backup,
    )


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
