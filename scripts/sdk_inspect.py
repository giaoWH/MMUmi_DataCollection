from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.storage import SessionReader
from sdk.logging import build_logger


def main() -> None:
    parser = argparse.ArgumentParser(description="查看 Session 摘要")
    parser.add_argument("session_dir")
    args = parser.parse_args()

    logger = build_logger("sdk.inspect", log_file=Path(args.session_dir) / "logs" / "sdk_inspect.log")
    reader = SessionReader(args.session_dir)
    logger.info("读取 Session 摘要: %s", args.session_dir)
    print(json.dumps(reader.summary(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
