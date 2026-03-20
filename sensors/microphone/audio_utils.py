from __future__ import annotations

from typing import Any


COMMON_SAMPLE_RATES = (48000, 44100, 32000, 16000, 8000)


def list_input_devices(audio: Any) -> list[dict[str, object]]:
    devices: list[dict[str, object]] = []
    for index in range(audio.get_device_count()):
        info = dict(audio.get_device_info_by_index(index))
        if int(info.get("maxInputChannels", 0) or 0) <= 0:
            continue
        info["index"] = index
        devices.append(info)
    return devices


def resolve_input_device(audio: Any, requested_index: int | None = None) -> dict[str, object]:
    devices = list_input_devices(audio)
    if not devices:
        raise RuntimeError("未找到可用的音频输入设备")

    if requested_index is None:
        return devices[0]

    if requested_index < 0 or requested_index >= audio.get_device_count():
        raise RuntimeError(f"音频输入设备索引无效: {requested_index}")

    info = dict(audio.get_device_info_by_index(requested_index))
    if int(info.get("maxInputChannels", 0) or 0) <= 0:
        raise RuntimeError(f"设备 {requested_index} 不是可录音输入设备: {info.get('name')}")
    info["index"] = requested_index
    return info


def build_candidate_sample_rates(
    preferred_rate: int | None,
    device_info: dict[str, object],
) -> list[int]:
    candidates: list[int] = []

    def _append(rate: int | float | None) -> None:
        if rate is None:
            return
        normalized = int(round(float(rate)))
        if normalized <= 0 or normalized in candidates:
            return
        candidates.append(normalized)

    _append(preferred_rate)
    _append(device_info.get("defaultSampleRate"))
    for rate in COMMON_SAMPLE_RATES:
        _append(rate)
    return candidates


def open_input_stream(
    audio: Any,
    *,
    audio_format: int,
    channels: int,
    preferred_rate: int | None,
    chunk: int,
    device_index: int | None,
) -> tuple[Any, dict[str, object], int]:
    device_info = resolve_input_device(audio, requested_index=device_index)
    sample_rates = build_candidate_sample_rates(preferred_rate, device_info)
    last_error: Exception | None = None

    for sample_rate in sample_rates:
        try:
            stream = audio.open(
                format=audio_format,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=int(device_info["index"]),
                frames_per_buffer=chunk,
            )
            return stream, device_info, sample_rate
        except Exception as exc:
            last_error = exc

    device_name = device_info.get("name", "unknown")
    raise RuntimeError(
        f"无法打开输入设备 index={device_info['index']} name={device_name}，"
        f"已尝试采样率 {sample_rates}，最后错误: {last_error}"
    ) from last_error
