from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.config import load_config_file
from sdk.logging import build_logger
from sdk.perception import OrbSlam3SessionProcessConfig, process_orbslam3_session


def main() -> None:
    parser = argparse.ArgumentParser(description="对已有 Session 执行 ORB-SLAM3 轨迹处理")
    parser.add_argument("session_dir")
    parser.add_argument("--command", default=None)
    parser.add_argument(
        "--mode",
        choices=["rgbd_inertial", "stereo", "stereo_inertial"],
        default="rgbd_inertial",
    )
    parser.add_argument("--output-mode", choices=["jsonl_file", "stdout_jsonl"], default="stdout_jsonl")
    parser.add_argument("--source-name", default="orbslam3")
    parser.add_argument("--working-dir", default=None)
    parser.add_argument("--bundle-dir", default=None)
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    config_payload = load_config_file(args.config) if args.config else {}
    raw_process_payload = config_payload.get("trajectory", config_payload)
    if not isinstance(raw_process_payload, dict):
        raise ValueError("轨迹处理配置必须是对象")
    process_config = OrbSlam3SessionProcessConfig(
        command=raw_process_payload.get("command", args.command),
        mode=raw_process_payload.get("mode", args.mode),
        output_mode=raw_process_payload.get("output_mode", args.output_mode),
        source_name=raw_process_payload.get("source_name", args.source_name),
        working_dir=raw_process_payload.get("working_dir", args.working_dir),
        bundle_dir=raw_process_payload.get("bundle_dir", args.bundle_dir),
        env=raw_process_payload.get("env"),
    )
    if not process_config.command:
        raise ValueError("必须通过 --command 或 --config 提供 ORB-SLAM3 命令")

    logger = build_logger(
        "sdk.process_trajectory",
        log_file=Path(args.session_dir) / "logs" / "sdk_process_trajectory.log",
    )
    logger.info("开始轨迹处理，mode=%s", process_config.mode)
    result = process_orbslam3_session(args.session_dir, process_config)
    logger.info("轨迹处理完成，bundle=%s", result.bundle_dir)
    print(f"轨迹写入完成: {Path(args.session_dir) / 'trajectory' / 'frames.jsonl'}")


if __name__ == "__main__":
    main()
