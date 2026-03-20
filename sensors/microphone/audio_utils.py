from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any


COMMON_SAMPLE_RATES = (48000, 44100, 32000, 16000, 8000)
DEFAULT_ALSA_PLUGIN_DIR_CANDIDATES = (
    "/usr/lib/aarch64-linux-gnu/alsa-lib",
    "/usr/lib/x86_64-linux-gnu/alsa-lib",
    "/usr/lib/alsa-lib",
    "/lib/aarch64-linux-gnu/alsa-lib",
    "/lib/x86_64-linux-gnu/alsa-lib",
    "/lib/alsa-lib",
)


def _looks_usable_alsa_plugin_dir(path: str | os.PathLike[str] | None) -> bool:
    if not path:
        return False
    candidate = Path(path)
    if not candidate.is_dir():
        return False
    pipewire_plugin = candidate / "libasound_module_pcm_pipewire.so"
    if pipewire_plugin.exists():
        return True
    return any(candidate.glob("libasound_module_pcm_*.so"))


def ensure_alsa_plugin_dir(
    *,
    env: dict[str, str] | None = None,
    candidates: tuple[str, ...] = DEFAULT_ALSA_PLUGIN_DIR_CANDIDATES,
) -> str | None:
    env = env if env is not None else os.environ

    configured = env.get("ALSA_PLUGIN_DIR")
    if _looks_usable_alsa_plugin_dir(configured):
        return configured

    conda_prefix = env.get("CONDA_PREFIX")
    if configured:
        configured_path = Path(configured)
        if configured_path.is_dir() and not conda_prefix:
            return configured

    for candidate in candidates:
        if _looks_usable_alsa_plugin_dir(candidate):
            env["ALSA_PLUGIN_DIR"] = candidate
            return candidate

    return configured


@contextmanager
def suppress_native_stderr(enabled: bool = True):
    if not enabled:
        yield
        return

    try:
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        yield
        return

    try:
        sys.stderr.flush()
    except Exception:
        pass

    saved_fd = os.dup(stderr_fd)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, stderr_fd)
        yield
    finally:
        os.dup2(saved_fd, stderr_fd)
        os.close(saved_fd)
        os.close(devnull_fd)


def create_audio_interface(
    pyaudio_module: Any,
    *,
    quiet_native_stderr: bool = True,
) -> Any:
    ensure_alsa_plugin_dir()
    with suppress_native_stderr(enabled=quiet_native_stderr):
        return pyaudio_module.PyAudio()


def list_input_devices(audio: Any, *, quiet_native_stderr: bool = True) -> list[dict[str, object]]:
    devices: list[dict[str, object]] = []
    with suppress_native_stderr(enabled=quiet_native_stderr):
        for index in range(audio.get_device_count()):
            info = dict(audio.get_device_info_by_index(index))
            if int(info.get("maxInputChannels", 0) or 0) <= 0:
                continue
            info["index"] = index
            devices.append(info)
    return devices


def resolve_input_device(
    audio: Any,
    requested_index: int | None = None,
    *,
    quiet_native_stderr: bool = True,
) -> dict[str, object]:
    devices = list_input_devices(audio, quiet_native_stderr=quiet_native_stderr)
    if not devices:
        raise RuntimeError("未找到可用的音频输入设备")

    if requested_index is None:
        return devices[0]

    with suppress_native_stderr(enabled=quiet_native_stderr):
        device_count = audio.get_device_count()
    if requested_index < 0 or requested_index >= device_count:
        raise RuntimeError(f"音频输入设备索引无效: {requested_index}")

    with suppress_native_stderr(enabled=quiet_native_stderr):
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
    quiet_native_stderr: bool = True,
) -> tuple[Any, dict[str, object], int]:
    device_info = resolve_input_device(
        audio,
        requested_index=device_index,
        quiet_native_stderr=quiet_native_stderr,
    )
    sample_rates = build_candidate_sample_rates(preferred_rate, device_info)
    last_error: Exception | None = None

    for sample_rate in sample_rates:
        try:
            with suppress_native_stderr(enabled=quiet_native_stderr):
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
