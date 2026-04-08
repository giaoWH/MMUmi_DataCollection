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
from sdk.quality import load_trajectory_qc_config, run_trajectory_qc, write_trajectory_qc_report

DEFAULT_RECORD_CONFIG_CANDIDATES = (
    REPO_ROOT / "configs" / "record.yaml",
    REPO_ROOT / "configs" / "record.yml",
    REPO_ROOT / "configs" / "record.json",
)


def _resolve_config_path(config_path: str | None) -> Path | None:
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
    parser = argparse.ArgumentParser(description="对 session 的轨迹与 action 进行规则型质检")
    parser.add_argument("session_dir")
    parser.add_argument("--config")
    args = parser.parse_args()

    logger = build_logger(
        "sdk.qc_trajectory",
        log_file=Path(args.session_dir) / "logs" / "sdk_qc_trajectory.log",
    )
    config_path = _resolve_config_path(getattr(args, "config", None))
    config_payload = load_config_file(config_path) if config_path is not None else {}
    qc_config = load_trajectory_qc_config(config_payload)
    if config_path is not None:
        logger.info("加载轨迹质检配置文件: %s", config_path)
    if not qc_config.enabled:
        payload = {
            "enabled": False,
            "skipped": True,
            "session_dir": str(Path(args.session_dir)),
        }
        logger.info("轨迹质检已禁用，跳过: %s", args.session_dir)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    result = run_trajectory_qc(args.session_dir, qc_config)
    output_path = write_trajectory_qc_report(result)
    logger.info(
        "轨迹质检完成，status=%s, output=%s",
        result.session_status,
        output_path,
    )
    payload = {
        "session_status": result.session_status,
        "output_path": str(output_path),
        "summary": result.report["summary"],
        "session_flags": result.report.get("session_flags", []),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if result.session_status == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
