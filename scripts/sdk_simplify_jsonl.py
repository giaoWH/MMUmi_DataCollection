from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.storage.simplified_stream import build_simplified_sensor_record, should_write_simplified_stream


def iter_target_jsonl_files(sessions_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(sessions_root.rglob("*.jsonl")):
        if path.stem.endswith("_s"):
            continue
        if not should_write_simplified_stream(path.parent.name):
            continue
        files.append(path)
    return files


def simplify_record(record: dict[str, object], sensor_name: str, tz_name: str) -> dict[str, object]:
    simplified = build_simplified_sensor_record(sensor_name, record, tz_name=tz_name)
    if simplified is None:
        raise ValueError(f"不支持生成简化 jsonl 的传感器: {sensor_name}")
    return simplified


def simplify_jsonl_file(source_path: Path, tz_name: str) -> tuple[Path, int]:
    sensor_name = source_path.parent.name
    target_path = source_path.with_name(f"{source_path.stem}_s{source_path.suffix}")
    line_count = 0

    with source_path.open("r", encoding="utf-8") as source, target_path.open("w", encoding="utf-8") as target:
        for raw_line in source:
            line = raw_line.strip()
            if not line:
                continue
            record = json.loads(line)
            simplified = simplify_record(record, sensor_name, tz_name)
            target.write(json.dumps(simplified, ensure_ascii=False))
            target.write("\n")
            line_count += 1

    return target_path, line_count


def main() -> None:
    parser = argparse.ArgumentParser(description="为指定 sessions 生成简化版 _s.jsonl 文件")
    parser.add_argument(
        "sessions_root",
        nargs="?",
        default="/mnt/m2ssd/umi-data/sessions",
        help="session 根目录，默认 /mnt/m2ssd/umi-data/sessions",
    )
    parser.add_argument(
        "--timezone",
        default="Asia/Shanghai",
        help="将 host_time 转成墙上时间时使用的时区，默认 Asia/Shanghai",
    )
    args = parser.parse_args()

    sessions_root = Path(args.sessions_root).expanduser().resolve()
    if not sessions_root.exists():
        raise FileNotFoundError(f"未找到 session 根目录: {sessions_root}")

    files = iter_target_jsonl_files(sessions_root)
    if not files:
        print(f"未找到可处理的目标 jsonl 文件: {sessions_root}")
        return

    total_rows = 0
    for source_path in files:
        target_path, line_count = simplify_jsonl_file(source_path, args.timezone)
        total_rows += line_count
        print(f"{source_path} -> {target_path} ({line_count} rows)")

    print(f"完成: {len(files)} 个文件, {total_rows} 行")


if __name__ == "__main__":
    main()
