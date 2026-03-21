from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sdk.storage import SessionWriter
from sdk.storage.schema import manifest_path_for

from .pipeline import OrbSlam3Pipeline, OrbSlam3PipelineConfig


@dataclass(frozen=True)
class OrbSlam3SessionProcessConfig:
    command: str | None = None
    mode: str = "rgbd_inertial"
    output_mode: str = "stdout_jsonl"
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
        command=payload.get("command"),
        mode=payload.get("mode", "rgbd_inertial"),
        output_mode=payload.get("output_mode", "stdout_jsonl"),
        source_name=payload.get("source_name", "orbslam3"),
        working_dir=payload.get("working_dir"),
        bundle_dir=payload.get("bundle_dir"),
        env=payload.get("env"),
    )


def process_orbslam3_session(
    session_dir: str | Path,
    config: OrbSlam3SessionProcessConfig,
) -> OrbSlam3SessionProcessResult:
    if not config.command:
        raise ValueError("启用轨迹处理时必须提供 trajectory.command")

    pipeline = OrbSlam3Pipeline(
        OrbSlam3PipelineConfig(
            command=config.command,
            mode=config.mode,
            output_mode=config.output_mode,
            source_name=config.source_name,
            working_dir=config.working_dir,
            bundle_dir=config.bundle_dir,
            env=config.env,
        )
    )
    bundle, trajectory_frames = pipeline.run(session_dir)

    writer = SessionWriter.open_existing(session_dir)
    for frame in trajectory_frames:
        writer.write_trajectory_frame(frame)
    writer.close()

    notes_update = {
        "trajectory_source": config.source_name,
        "orbslam3_mode": config.mode,
        "orbslam3_bundle": str(bundle.bundle_dir),
    }
    _update_session_notes(Path(session_dir), notes_update)

    return OrbSlam3SessionProcessResult(
        mode=config.mode,
        source_name=config.source_name,
        bundle_dir=bundle.bundle_dir,
        trajectory_frames=len(trajectory_frames),
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
