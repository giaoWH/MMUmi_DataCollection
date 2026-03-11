from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sdk.constants import SDK_SCHEMA_VERSION


@dataclass(frozen=True)
class SessionInfo:
    schema_version: str
    session_id: str
    started_at: float
    output_dir: Path
    sensors: dict[str, dict[str, object]]
    config: dict[str, object]
    notes: dict[str, object] = field(default_factory=dict)


def create_session_info(
    output_root: str | Path,
    *,
    sensors: dict[str, dict[str, object]],
    config: dict[str, object],
    notes: dict[str, object] | None = None,
) -> SessionInfo:
    started_at = datetime.now().timestamp()
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(output_root) / f"session_{session_id}"
    return SessionInfo(
        schema_version=SDK_SCHEMA_VERSION,
        session_id=session_id,
        started_at=started_at,
        output_dir=output_dir,
        sensors=sensors,
        config=config,
        notes=notes or {},
    )
