from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.exporters import SessionExportValidator, list_export_formats
from sdk.logging import build_logger


def main() -> None:
    parser = argparse.ArgumentParser(description="校验导出结果与 session 是否一致")
    parser.add_argument("session_dir")
    parser.add_argument("--format", choices=list_export_formats(), required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    logger = build_logger("sdk.validate_export", log_file=Path(args.session_dir) / "logs" / "sdk_validate_export.log")
    validator = SessionExportValidator(args.session_dir)
    result = validator.validate(args.format, args.output)
    logger.info("导出校验完成，format=%s, valid=%s, issues=%d", args.format, result.is_valid, len(result.issues))

    payload = {
        "format": result.export_format,
        "output_path": str(result.output_path),
        "is_valid": result.is_valid,
        "issues": result.issues,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not result.is_valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
