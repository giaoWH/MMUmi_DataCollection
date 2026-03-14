from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.config import load_config_file
from sdk.exporters import create_exporter, list_export_formats
from sdk.logging import build_logger


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 Session 到外部格式")
    parser.add_argument("session_dir")
    parser.add_argument("--format", choices=list_export_formats(), default="hdf5")
    parser.add_argument("--output", default=None)
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    export_format = args.format
    output = args.output
    if args.config:
        config = load_config_file(args.config)
        export_format = config.get("format", export_format)
        output = config.get("output", output)

    logger = build_logger("sdk.export", log_file=Path(args.session_dir) / "logs" / "sdk_export.log")
    logger.info("开始导出，format=%s", export_format)
    exporter = create_exporter(export_format)
    result = exporter.export(args.session_dir, output)
    logger.info("导出完成: %s", result.output_path)
    print(f"导出完成: {result.export_format} -> {result.output_path}")


if __name__ == "__main__":
    main()
