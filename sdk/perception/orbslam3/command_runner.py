from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sdk.core.frame import FrameTime, TrajectoryFrame

from .base import TrajectoryRunner


@dataclass(frozen=True)
class OrbSlam3CommandConfig:
    command: str
    source_name: str = "orbslam3"
    working_dir: str | None = None
    env: dict[str, str] | None = None
    output_mode: str = "stdout_jsonl"
    template_vars: dict[str, str] | None = None


class OrbSlam3CommandRunner(TrajectoryRunner):
    def __init__(self, config: OrbSlam3CommandConfig) -> None:
        self.config = config

    def run(self, session_dir: str | Path) -> list[TrajectoryFrame]:
        session_dir = Path(session_dir)
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_jsonl = Path(tmp_dir) / "trajectory.jsonl"
            command = self._build_command(session_dir, output_jsonl)
            env = os.environ.copy()
            env.update(self.config.env or {})
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                cwd=self.config.working_dir,
                env=env,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "ORB-SLAM3 命令执行失败:\n"
                    f"command={command}\n"
                    f"stdout={result.stdout}\n"
                    f"stderr={result.stderr}"
                )

            if self.config.output_mode == "stdout_jsonl":
                frames = self._parse_jsonl(result.stdout.splitlines())
                if not frames:
                    raise RuntimeError("ORB-SLAM3 未生成有效轨迹")
                return frames

            if self.config.output_mode != "jsonl_file":
                raise ValueError(f"不支持的 ORB-SLAM3 输出模式: {self.config.output_mode}")
            if not output_jsonl.exists():
                raise RuntimeError("ORB-SLAM3 命令未生成期望的轨迹文件")
            frames = self._parse_jsonl(output_jsonl.read_text(encoding="utf-8").splitlines())
            if not frames:
                raise RuntimeError("ORB-SLAM3 未生成有效轨迹")
            return frames

    def _build_command(self, session_dir: Path, output_jsonl: Path) -> list[str]:
        template = self.config.command.replace("{session_dir}", str(session_dir))
        template = template.replace("{output_jsonl}", str(output_jsonl))
        for key, value in (self.config.template_vars or {}).items():
            template = template.replace(f"{{{key}}}", value)
        return shlex.split(template)

    def _parse_jsonl(self, lines: list[str]) -> list[TrajectoryFrame]:
        frames: list[TrajectoryFrame] = []
        last_timestamp: float | None = None
        for index, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            frame = self._build_frame(index, payload)
            timestamp = frame.time.host_time
            if timestamp is None:
                raise ValueError("ORB-SLAM3 轨迹记录缺少 timestamp 字段")
            if last_timestamp is not None and timestamp < last_timestamp:
                raise ValueError("ORB-SLAM3 轨迹记录的 timestamp 必须单调不减")
            last_timestamp = timestamp
            frames.append(frame)
        return frames

    def _build_frame(self, frame_id: int, payload: dict[str, Any]) -> TrajectoryFrame:
        if "timestamp" not in payload:
            raise ValueError("ORB-SLAM3 轨迹记录缺少 timestamp 字段")
        position = payload.get("position")
        quaternion = payload.get("quaternion")
        metadata = payload.get("metadata", {})
        if not isinstance(position, list) or len(position) != 3:
            raise ValueError("ORB-SLAM3 轨迹记录的 position 字段必须是长度为 3 的列表")
        if not isinstance(quaternion, list) or len(quaternion) != 4:
            raise ValueError("ORB-SLAM3 轨迹记录的 quaternion 字段必须是长度为 4 的列表")
        if not isinstance(metadata, dict):
            raise ValueError("ORB-SLAM3 轨迹记录的 metadata 字段必须是对象")
        timestamp = float(payload["timestamp"])
        if not math.isfinite(timestamp):
            raise ValueError("ORB-SLAM3 轨迹记录的 timestamp 必须是有限数值")
        position_values = [float(value) for value in position]
        quaternion_values = [float(value) for value in quaternion]
        if not all(math.isfinite(value) for value in position_values):
            raise ValueError("ORB-SLAM3 轨迹记录的 position 字段必须是有限数值")
        if not all(math.isfinite(value) for value in quaternion_values):
            raise ValueError("ORB-SLAM3 轨迹记录的 quaternion 字段必须是有限数值")
        if math.isclose(sum(value * value for value in quaternion_values), 0.0, abs_tol=1e-12):
            raise ValueError("ORB-SLAM3 轨迹记录的 quaternion 不能为零向量")
        normalized_metadata = dict(metadata)
        normalized_metadata.setdefault("time_semantics", "trajectory_result_timestamp")
        return TrajectoryFrame(
            source=self.config.source_name,
            frame_id=int(payload.get("frame_id", frame_id)),
            time=FrameTime(
                host_time=timestamp,
                monotonic_time=timestamp,
                device_time=payload.get("device_time"),
                aligned_time=payload.get("aligned_time"),
            ),
            position=position_values,
            quaternion=quaternion_values,
            tracking_state=str(payload.get("tracking_state", "OK")),
            metadata=normalized_metadata,
        )
