from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sdk.constants import SDK_SCHEMA_VERSION
from sdk.session_naming import normalize_task_slug


@dataclass(frozen=True)
class SessionInfo:
    schema_version: str
    session_id: str
    started_at: float
    output_dir: Path
    task_name: str
    task_slug: str
    sensors: dict[str, dict[str, object]]
    config: dict[str, object]
    notes: dict[str, object] = field(default_factory=dict)


def create_session_info(
    output_root: str | Path,
    *,
    task_name: str,
    sensors: dict[str, dict[str, object]],
    config: dict[str, object],
    notes: dict[str, object] | None = None,
    session_name: str | None = None,
) -> SessionInfo:
    started_at = datetime.now().timestamp()
    task_slug = normalize_task_slug(task_name)
    default_session_name = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    requested_name = (session_name or "").strip() or default_session_name
    session_id = _resolve_unique_session_name(Path(output_root), requested_name)
    output_dir = Path(output_root) / session_id
    return SessionInfo(
        schema_version=SDK_SCHEMA_VERSION,
        session_id=session_id,
        started_at=started_at,
        output_dir=output_dir,
        task_name=task_name,
        task_slug=task_slug,
        sensors=sensors,
        config=config,
        notes=notes or {},
    )


def _resolve_unique_session_name(output_root: Path, requested_name: str) -> str:
    if not (output_root / requested_name).exists():
        return requested_name
    suffix = 2
    while True:
        candidate = f"{requested_name}-{suffix}"
        if not (output_root / candidate).exists():
            return candidate
        suffix += 1
