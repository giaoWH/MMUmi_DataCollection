from __future__ import annotations

import re
from pathlib import Path


_INVALID_TASK_SLUG_CHARS = {"/", "\\", ":", "*", "?", '"', "<", ">", "|"}


def normalize_task_slug(task_name: str) -> str:
    if not isinstance(task_name, str):
        raise ValueError("task_name 必须是非空字符串")
    normalized = task_name.strip().lower()
    if not normalized:
        raise ValueError("task_name 不能为空")

    chars: list[str] = []
    for char in normalized:
        if char.isspace():
            chars.append("_")
            continue
        if char in _INVALID_TASK_SLUG_CHARS or ord(char) < 32:
            chars.append("_")
            continue
        chars.append(char)

    slug = re.sub(r"_+", "_", "".join(chars)).strip("_.")
    if not slug:
        raise ValueError("task_name 规范化后不能为空")
    return slug


def normalize_task_dir_name(task_name: str) -> str:
    if not isinstance(task_name, str):
        raise ValueError("task_name 必须是非空字符串")
    collapsed = " ".join(task_name.strip().split())
    if not collapsed:
        raise ValueError("task_name 不能为空")

    chars: list[str] = []
    for char in collapsed:
        if char in _INVALID_TASK_SLUG_CHARS or ord(char) < 32:
            chars.append(" ")
            continue
        chars.append(char)

    normalized = " ".join("".join(chars).split()).strip(" .")
    if not normalized:
        raise ValueError("task_name 规范化后不能为空")
    return normalized


def build_batch_task_dir(task_dir_base: str, suffix_index: int) -> str:
    if suffix_index <= 1:
        return task_dir_base
    return f"{task_dir_base}_{suffix_index:02d}"


def item_index_width(batch_size: int) -> int:
    return max(2, len(str(max(1, int(batch_size)))))


def format_item_name(item_index: int, *, batch_size: int) -> str:
    if item_index <= 0:
        raise ValueError("item_index 必须为正整数")
    return f"{item_index:0{item_index_width(batch_size)}d}"


def parse_item_index(item_name: str) -> int:
    if not isinstance(item_name, str) or not item_name.isdigit():
        raise ValueError("item_name 必须是纯数字字符串")
    return int(item_name)


def coerce_stream_seq(seq: str | int) -> str:
    if isinstance(seq, str):
        normalized = seq.strip()
        if not normalized or not normalized.isdigit():
            raise ValueError("seq 字符串必须是纯数字")
        return normalized
    if int(seq) <= 0:
        raise ValueError("seq 必须为正整数")
    return f"{int(seq):03d}"


def stream_file_stem(task_slug: str, modality: str, seq: str | int = 1) -> str:
    if not isinstance(modality, str) or not modality.strip():
        raise ValueError("modality 不能为空")
    return f"{task_slug}_{modality}_{coerce_stream_seq(seq)}"


def stream_frames_path(sensor_name: str, modality: str, task_slug: str, seq: str | int = 1) -> str:
    return f"streams/{sensor_name}/{stream_file_stem(task_slug, modality, seq)}.jsonl"


def stream_simplified_frames_path(sensor_name: str, modality: str, task_slug: str, seq: str | int = 1) -> str:
    return f"streams/{sensor_name}/{stream_file_stem(task_slug, modality, seq)}_s.jsonl"


def stream_media_path(sensor_name: str, modality: str, task_slug: str, seq: str | int = 1) -> str | None:
    if modality not in {"rgb", "rgbd", "visuotactile"}:
        return None
    return f"streams/{sensor_name}/{stream_file_stem(task_slug, modality, seq)}.mp4"


def stream_artifact_stem(
    task_slug: str,
    modality: str,
    frame_id: int,
    key: str,
    seq: str | int = 1,
) -> str:
    if frame_id < 0:
        raise ValueError("frame_id 不能为负数")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("artifact key 不能为空")
    return f"{stream_file_stem(task_slug, modality, seq)}_{frame_id:06d}_{key}"


def infer_layout_from_session_dir(session_dir: str | Path) -> tuple[str, str]:
    session_dir = Path(session_dir)
    item_name = session_dir.name
    task_dir = session_dir.parent.name
    if not task_dir:
        raise ValueError(f"无法从 session_dir 推断 task_dir: {session_dir}")
    parse_item_index(item_name)
    return task_dir, item_name
