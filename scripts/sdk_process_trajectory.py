from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.config import load_config_file
from sdk.logging import build_logger
from sdk.perception import (
    OrbSlam3SessionProcessConfig,
    build_default_stereo_inertial_command,
    load_orbslam3_process_config,
    normalize_orbslam3_process_config,
    process_orbslam3_session,
)

DEFAULT_RECORD_CONFIG_CANDIDATES = (
    REPO_ROOT / "configs" / "record.yaml",
    REPO_ROOT / "configs" / "record.yml",
    REPO_ROOT / "configs" / "record.json",
)


def _resolve_process_config_path(config_path: str | None) -> Path | None:
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
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="在 conda umi_sdk 环境中对已拷贝到 PC 的 Session 执行离线 stereo_inertial 轨迹处理",
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument("session_dir")
    parser.add_argument("--command")
    parser.add_argument("--mode")
    parser.add_argument("--source-name")
    parser.add_argument("--working-dir")
    parser.add_argument("--bundle-dir")
    parser.add_argument("--config")
    args = parser.parse_args()

    config_path = _resolve_process_config_path(getattr(args, "config", None))
    config_payload = load_config_file(config_path) if config_path is not None else {}
    loaded_config = load_orbslam3_process_config(config_payload)
    process_config = normalize_orbslam3_process_config(
        OrbSlam3SessionProcessConfig(
            command=getattr(args, "command", None) or loaded_config.command or build_default_stereo_inertial_command(),
            mode=getattr(args, "mode", loaded_config.mode),
            output_mode=loaded_config.output_mode,
            source_name=getattr(args, "source_name", loaded_config.source_name),
            working_dir=getattr(args, "working_dir", loaded_config.working_dir),
            bundle_dir=getattr(args, "bundle_dir", loaded_config.bundle_dir),
            env=loaded_config.env,
        )
    )

    logger = build_logger(
        "sdk.process_trajectory",
        log_file=Path(args.session_dir) / "logs" / "sdk_process_trajectory.log",
    )
    if config_path is not None:
        logger.info("加载轨迹配置文件: %s", config_path)
    logger.info("开始离线轨迹处理，mode=%s", process_config.mode)
    result = process_orbslam3_session(args.session_dir, process_config)
    logger.info("轨迹处理完成，bundle=%s", result.bundle_dir)
    print(f"轨迹写入完成: {Path(args.session_dir) / 'trajectory' / 'frames.jsonl'}")


if __name__ == "__main__":
    main()
