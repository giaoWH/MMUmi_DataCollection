from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sdk.core.frame import TrajectoryFrame
from sdk.storage.schema import manifest_path_for

from .pipeline import OrbSlam3Pipeline, OrbSlam3PipelineConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
OFFICIAL_ORBSLAM3_MODE = "stereo_inertial"
OFFICIAL_OUTPUT_MODE = "jsonl_file"


@dataclass(frozen=True)
class OrbSlam3SessionProcessConfig:
    command: str | None = None
    mode: str = OFFICIAL_ORBSLAM3_MODE
    output_mode: str = OFFICIAL_OUTPUT_MODE
    source_name: str = "orbslam3"
    working_dir: str | None = None
    bundle_dir: str | None = None
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class OrbSlam3SessionProcessResult:
    mode: str
    source_name: str
    bundle_dir: Path
    trajectory_frames: int


def load_orbslam3_process_config(config_payload: dict[str, Any]) -> OrbSlam3SessionProcessConfig:
    payload = config_payload.get("trajectory", config_payload)
    if not isinstance(payload, dict):
        raise ValueError("trajectory 配置必须是对象")

    return OrbSlam3SessionProcessConfig(
        command=payload.get("command") or build_default_stereo_inertial_command(),
        mode=payload.get("mode", OFFICIAL_ORBSLAM3_MODE),
        output_mode=payload.get("output_mode", OFFICIAL_OUTPUT_MODE),
        source_name=payload.get("source_name", "orbslam3"),
        working_dir=payload.get("working_dir"),
        bundle_dir=payload.get("bundle_dir"),
        env=payload.get("env"),
    )


def process_orbslam3_session(
    session_dir: str | Path,
    config: OrbSlam3SessionProcessConfig,
) -> OrbSlam3SessionProcessResult:
    normalized_config = normalize_orbslam3_process_config(config)

    pipeline = OrbSlam3Pipeline(
        OrbSlam3PipelineConfig(
            command=normalized_config.command,
            mode=normalized_config.mode,
            output_mode=normalized_config.output_mode,
            source_name=normalized_config.source_name,
            working_dir=normalized_config.working_dir,
            bundle_dir=normalized_config.bundle_dir,
            env=normalized_config.env,
        )
    )
    bundle, trajectory_frames = pipeline.run(session_dir)
    if not trajectory_frames:
        raise RuntimeError("ORB-SLAM3 未生成有效轨迹")
    _replace_trajectory_frames(Path(session_dir), trajectory_frames)

    notes_update = {
        "trajectory_source": normalized_config.source_name,
        "orbslam3_mode": normalized_config.mode,
        "orbslam3_bundle": str(bundle.bundle_dir),
    }
    _update_session_notes(Path(session_dir), notes_update)

    return OrbSlam3SessionProcessResult(
        mode=normalized_config.mode,
        source_name=normalized_config.source_name,
        bundle_dir=bundle.bundle_dir,
        trajectory_frames=len(trajectory_frames),
    )


def build_default_stereo_inertial_command(python_executable: str | None = None) -> str:
    python_path = python_executable or sys.executable
    wrapper_path = REPO_ROOT / "scripts" / "orbslam3_wrapper.py"
    return " ".join(
        [
            shlex.quote(python_path),
            shlex.quote(str(wrapper_path)),
            "--mode",
            OFFICIAL_ORBSLAM3_MODE,
            "--bundle-manifest",
            "{bundle_manifest}",
            "--output",
            "{output_jsonl}",
        ]
    )


def normalize_orbslam3_process_config(
    config: OrbSlam3SessionProcessConfig,
) -> OrbSlam3SessionProcessConfig:
    if config.mode != OFFICIAL_ORBSLAM3_MODE:
        raise ValueError(
            f"当前官方轨迹处理仅支持 {OFFICIAL_ORBSLAM3_MODE}，收到 {config.mode}"
        )
    if config.output_mode != OFFICIAL_OUTPUT_MODE:
        raise ValueError(
            f"当前官方轨迹处理仅支持 {OFFICIAL_OUTPUT_MODE} 输出，收到 {config.output_mode}"
        )
    return replace(
        config,
        command=config.command or build_default_stereo_inertial_command(),
        output_mode=OFFICIAL_OUTPUT_MODE,
    )


def _update_session_notes(session_dir: Path, notes_update: dict[str, Any]) -> None:
    manifest_path = manifest_path_for(session_dir)
    _update_json_notes_file(manifest_path, notes_update)

    meta_path = session_dir / "meta.json"
    if meta_path.exists():
        _update_json_notes_file(meta_path, notes_update)


def _update_json_notes_file(path: Path, notes_update: dict[str, Any]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    notes = payload.setdefault("notes", {})
    notes.update(notes_update)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _replace_trajectory_frames(session_dir: Path, frames: list[TrajectoryFrame]) -> None:
    trajectory_dir = session_dir / "trajectory"
    trajectory_dir.mkdir(parents=True, exist_ok=True)
    target_path = trajectory_dir / "frames.jsonl"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=trajectory_dir,
            prefix="frames.",
            suffix=".jsonl.tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            for frame in frames:
                handle.write(json.dumps(_trajectory_record(frame), ensure_ascii=False))
                handle.write("\n")
        os.replace(temp_path, target_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _trajectory_record(frame: TrajectoryFrame) -> dict[str, Any]:
    return {
        "source": frame.source,
        "frame_id": frame.frame_id,
        "time": {
            "host_time": frame.time.host_time,
            "host_time_ns": frame.time.host_time_ns,
            "monotonic_time": frame.time.monotonic_time,
            "monotonic_time_ns": frame.time.monotonic_time_ns,
            "device_time": frame.time.device_time,
            "device_time_ns": frame.time.device_time_ns,
            "aligned_time": frame.time.aligned_time,
            "aligned_time_ns": frame.time.aligned_time_ns,
        },
        "position": frame.position,
        "quaternion": frame.quaternion,
        "tracking_state": frame.tracking_state,
        "metadata": frame.metadata,
    }
