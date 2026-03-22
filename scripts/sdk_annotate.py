from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.annotations.web import run_annotation_server
from sdk.logging import build_logger


def main() -> None:
    parser = argparse.ArgumentParser(description="启动本地 Web 标注工具")
    parser.add_argument("session_dir")
    parser.add_argument("--schema", default=str(REPO_ROOT / "configs" / "annotation_schema.yaml"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--annotator", default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    logger = build_logger("sdk.annotate", log_file=Path(args.session_dir) / "logs" / "sdk_annotate.log")
    logger.info("启动标注工具: session=%s host=%s port=%s schema=%s", args.session_dir, args.host, args.port, args.schema)
    run_annotation_server(
        args.session_dir,
        schema_path=args.schema,
        annotator=args.annotator,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )


if __name__ == "__main__":
    main()
