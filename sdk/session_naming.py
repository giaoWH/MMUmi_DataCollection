from __future__ import annotations

import re


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


def stream_file_stem(task_slug: str, modality: str, seq: int = 1) -> str:
    if not isinstance(modality, str) or not modality.strip():
        raise ValueError("modality 不能为空")
    if seq <= 0:
        raise ValueError("seq 必须为正整数")
    return f"{task_slug}_{modality}_{seq:03d}"


def stream_frames_path(sensor_name: str, modality: str, task_slug: str, seq: int = 1) -> str:
    return f"streams/{sensor_name}/{stream_file_stem(task_slug, modality, seq)}.jsonl"


def stream_media_path(sensor_name: str, modality: str, task_slug: str, seq: int = 1) -> str | None:
    if modality not in {"rgb", "rgbd", "visuotactile"}:
        return None
    return f"streams/{sensor_name}/{stream_file_stem(task_slug, modality, seq)}.mp4"


def stream_artifact_stem(
    task_slug: str,
    modality: str,
    frame_id: int,
    key: str,
    seq: int = 1,
) -> str:
    if frame_id < 0:
        raise ValueError("frame_id 不能为负数")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("artifact key 不能为空")
    return f"{stream_file_stem(task_slug, modality, seq)}_{frame_id:06d}_{key}"
