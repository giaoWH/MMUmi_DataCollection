from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.config import load_config_file
from sdk.logging import build_logger
from sdk.perception import OrbSlam3Pipeline, OrbSlam3PipelineConfig
from sdk.storage import SessionWriter
from sdk.storage.schema import manifest_path_for


def main() -> None:
    parser = argparse.ArgumentParser(description="对已有 Session 执行 ORB-SLAM3 轨迹处理")
    parser.add_argument("session_dir")
    parser.add_argument("--command", default=None)
    parser.add_argument("--mode", default="rgbd_inertial")
    parser.add_argument("--output-mode", choices=["jsonl_file", "stdout_jsonl"], default="jsonl_file")
    parser.add_argument("--source-name", default="orbslam3")
    parser.add_argument("--working-dir", default=None)
    parser.add_argument("--bundle-dir", default=None)
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    config_payload = load_config_file(args.config) if args.config else {}
    command = config_payload.get("command", args.command)
    if not command:
        raise ValueError("必须通过 --command 或 --config 提供 ORB-SLAM3 命令")

    logger = build_logger(
        "sdk.process_trajectory",
        log_file=Path(args.session_dir) / "logs" / "sdk_process_trajectory.log",
    )
    logger.info("开始轨迹处理，mode=%s", config_payload.get("mode", args.mode))
    pipeline = OrbSlam3Pipeline(
        OrbSlam3PipelineConfig(
            command=command,
            mode=config_payload.get("mode", args.mode),
            output_mode=config_payload.get("output_mode", args.output_mode),
            source_name=config_payload.get("source_name", args.source_name),
            working_dir=config_payload.get("working_dir", args.working_dir),
            bundle_dir=config_payload.get("bundle_dir", args.bundle_dir),
            env=config_payload.get("env"),
        )
    )
    bundle, trajectory_frames = pipeline.run(args.session_dir)

    writer = SessionWriter.open_existing(args.session_dir)
    for frame in trajectory_frames:
        writer.write_trajectory_frame(frame)
    writer.close()

    manifest_path = manifest_path_for(args.session_dir)
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    notes = manifest_payload.setdefault("notes", {})
    notes["trajectory_source"] = config_payload.get("source_name", args.source_name)
    notes["orbslam3_mode"] = config_payload.get("mode", args.mode)
    notes["orbslam3_bundle"] = str(bundle.bundle_dir)
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("轨迹处理完成，bundle=%s", bundle.bundle_dir)
    print(f"轨迹写入完成: {Path(args.session_dir) / 'trajectory' / 'frames.jsonl'}")


if __name__ == "__main__":
    main()
