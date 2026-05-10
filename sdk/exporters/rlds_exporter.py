from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from sdk.exporters.base import ExportResult, SessionExporter
from sdk.storage import SessionReader


class RLDSSessionExporter(SessionExporter):
    export_format = "rlds"

    def export(self, session_dir: str | Path, output_path: str | Path | None = None) -> ExportResult:
        reader = SessionReader(session_dir)
        target = Path(output_path) if output_path else Path(session_dir) / "exports" / "rlds"
        target.mkdir(parents=True, exist_ok=True)
        episodes_dir = target / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)

        frame_index = {
            sensor_name: {
                frame.frame_id: frame
                for frame in reader.iter_sensor_frames(sensor_name, load_payload=False)
            }
            for sensor_name in reader.sensor_names()
        }
        trajectory_frames = list(reader.iter_trajectory_frames())
        aligned_records = list(reader.iter_aligned_records())
        steps = []
        for index, record in enumerate(aligned_records):
            steps.append(
                {
                    "is_first": index == 0,
                    "is_last": index == len(aligned_records) - 1,
                    "is_terminal": False,
                    "reward": 0.0,
                    "discount": 1.0,
                    "action": {},
                    "observation": self._build_observation(
                        record,
                        frame_index,
                        trajectory_frames[index] if index < len(trajectory_frames) else None,
                    ),
                    "metadata": {
                        "aligned_time": record["aligned_time"],
                        "missing_sensors": record.get("missing_sensors", []),
                        "age_by_sensor": record.get("age_by_sensor", {}),
                    },
                }
            )

        payload = {
            "episode_id": reader.manifest.session_id,
            "metadata": {
                "session_id": reader.manifest.session_id,
                "schema_version": reader.manifest.schema_version,
                "sensor_names": reader.sensor_names(),
                "config": reader.manifest.config,
            },
            "steps": steps,
        }
        (episodes_dir / "episode_000000.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target / "dataset_info.json").write_text(
            json.dumps(
                {
                    "format": "rlds",
                    "episode_count": 1,
                    "step_count": len(steps),
                    "mandatory_step_fields": ["is_first", "is_last"],
                    "optional_step_fields": [
                        "observation",
                        "action",
                        "reward",
                        "discount",
                        "is_terminal",
                        "metadata",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return ExportResult(export_format=self.export_format, output_path=target)

    def _build_observation(
        self,
        aligned_record: dict[str, Any],
        frame_index: dict[str, dict[int, Any]],
        trajectory_frame: Any | None,
    ) -> dict[str, Any]:
        observation: dict[str, Any] = {}
        for sensor_name, frame_info in aligned_record.get("frames", {}).items():
            frame = frame_index.get(sensor_name, {}).get(frame_info["frame_id"])
            observation[sensor_name] = {
                "frame_id": frame_info["frame_id"],
                "host_time": frame_info["host_time"],
                "device_time": frame_info.get("device_time"),
                "payload": self._to_jsonable(frame.payload if frame is not None else {}),
                "metadata": self._to_jsonable(frame.metadata if frame is not None else {}),
            }
        if trajectory_frame is not None:
            observation["trajectory"] = {
                "frame_id": trajectory_frame.frame_id,
                "position": trajectory_frame.position,
                "quaternion": trajectory_frame.quaternion,
                "tracking_state": trajectory_frame.tracking_state,
            }
        return observation

    def _to_jsonable(self, value: Any) -> Any:
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {key: self._to_jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._to_jsonable(item) for item in value]
        return value
