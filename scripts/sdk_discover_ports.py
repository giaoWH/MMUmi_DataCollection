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


def prompt_manual_port(current_ports: dict[str, str]) -> str | None:
    print_port_snapshot("当前可见串口：", current_ports)
    raw = input("请输入目标 port（直接回车表示取消手动输入）: ").strip()
    return raw or None


def run_port_discovery(
    config_path: Path,
    *,
    timeout: float,
    poll_interval: float,
    create_backup: bool,
) -> int:
    config_payload = load_config_file(config_path)
    steps, skipped = build_port_discovery_plan(config_payload)

    if not steps:
        print("配置中没有启用需要自动发现 port 的串口传感器。")
        if skipped:
            print(f"已启用但不参与串口发现的设备: {', '.join(skipped)}")
        return 0

    print("=== 串口自动发现 ===")
    print(f"配置文件: {config_path}")
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
                print("用户主动退出，未写回配置文件。")
                return 1

            print("无效输入，请重新选择。")

    backup_path = write_config_payload(config_path, config_payload, create_backup=create_backup)
    print()
    print("端口发现完成，已写回配置文件。")
    for sensor_key, port in resolved_ports.items():
        print(f"  - {sensor_key}: {port}")
    if backup_path is not None:
        print(f"已生成备份文件: {backup_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="根据 record.yaml 引导式自动发现串口并写回配置")
    parser.add_argument(
        "--config",
        default=None,
        help="录制配置文件路径；未提供时默认尝试 configs/record.yaml",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="每次等待新串口出现的最长秒数")
    parser.add_argument("--poll-interval", type=float, default=0.5, help="轮询串口变化的时间间隔")
    parser.add_argument("--no-backup", action="store_true", help="写回配置时不生成 .bak 备份文件")
    args = parser.parse_args()

    config_path = resolve_record_config_path(args.config)
    exit_code = run_port_discovery(
        config_path,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
        create_backup=not args.no_backup,
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
