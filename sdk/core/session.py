from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sdk.constants import SDK_SCHEMA_VERSION
from sdk.session_naming import (
    build_batch_task_dir,
    format_item_name,
    infer_layout_from_session_dir,
    normalize_task_dir_name,
    normalize_task_slug,
    parse_item_index,
)


@dataclass(frozen=True)
class SessionInfo:
    schema_version: str
    session_id: str
    started_at: float
    output_dir: Path
    task_name: str
    task_slug: str
    task_dir: str
    item_index: int
    item_name: str
    batch_size: int
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
    task_dir: str | None = None,
    item_index: int | None = None,
    item_name: str | None = None,
    batch_size: int | None = None,
) -> SessionInfo:
    started_at = datetime.now().timestamp()
    task_slug = normalize_task_slug(task_name)
    resolved_batch_size = max(1, int(batch_size if batch_size is not None else config.get("task_name_batch_size", 5)))
    resolved_task_dir, resolved_item_index, resolved_item_name = _resolve_session_layout(
        Path(output_root),
        task_name=task_name,
        session_name=session_name,
        task_dir=task_dir,
        item_index=item_index,
        item_name=item_name,
        batch_size=resolved_batch_size,
    )
    session_id = f"{resolved_task_dir}__{resolved_item_name}"
    output_dir = Path(output_root) / resolved_task_dir / resolved_item_name
    return SessionInfo(
        schema_version=SDK_SCHEMA_VERSION,
        session_id=session_id,
        started_at=started_at,
        output_dir=output_dir,
        task_name=task_name,
        task_slug=task_slug,
        task_dir=resolved_task_dir,
        item_index=resolved_item_index,
        item_name=resolved_item_name,
        batch_size=resolved_batch_size,
        sensors=sensors,
        config=config,
        notes=notes or {},
    )


def infer_session_layout(session_dir: str | Path) -> tuple[str, int, str]:
    task_dir, item_name = infer_layout_from_session_dir(session_dir)
    return task_dir, parse_item_index(item_name), item_name


def _resolve_session_layout(
    output_root: Path,
    *,
    task_name: str,
    session_name: str | None,
    task_dir: str | None,
    item_index: int | None,
    item_name: str | None,
    batch_size: int,
) -> tuple[str, int, str]:
    if task_dir is not None or item_index is not None or item_name is not None:
        resolved_task_dir = (task_dir or "").strip()
        if not resolved_task_dir:
            raise ValueError("显式指定 item 时必须同时提供 task_dir")
        if item_name is None and item_index is None:
            raise ValueError("显式指定 task_dir 时必须同时提供 item_name 或 item_index")
        resolved_item_name = item_name or format_item_name(int(item_index), batch_size=batch_size)
        resolved_item_index = item_index if item_index is not None else parse_item_index(resolved_item_name)
        if resolved_item_name != format_item_name(int(resolved_item_index), batch_size=batch_size):
            raise ValueError("item_name 与 batch_size 规则不一致")
        return resolved_task_dir, int(resolved_item_index), resolved_item_name

    task_dir_base = normalize_task_dir_name(session_name) if session_name else normalize_task_dir_name(task_name)
    resolved_task_dir = _allocate_next_task_dir(output_root, task_dir_base)
    resolved_item_name = format_item_name(1, batch_size=batch_size)
    return resolved_task_dir, 1, resolved_item_name


def _allocate_next_task_dir(output_root: Path, task_dir_base: str) -> str:
    if not (output_root / task_dir_base).exists():
        return task_dir_base
    suffix = 2
    while True:
        candidate = build_batch_task_dir(task_dir_base, suffix)
        if not (output_root / candidate).exists():
            return candidate
        suffix += 1
