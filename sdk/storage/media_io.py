from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from sdk.session_naming import stream_media_path

try:
    import av
except ImportError:  # pragma: no cover
    av = None


_VIDEO_CACHE_SIZE = 32
_VIDEO_TIME_BASE_DEN = 90_000


def ensure_av_available() -> None:
    if av is None:
        raise RuntimeError("未安装 PyAV，无法读写 MP4 媒体流")


@dataclass(frozen=True)
class PlannedAudioStream:
    channels: int = 1
    sample_rate: int = 48000
    dtype: str = "int16"


@dataclass(frozen=True)
class MediaEncodingConfig:
    video_codec: str = "libx264"
    video_pixel_format: str = "yuv420p"
    video_profile: str = "high"
    video_preset: str = "veryfast"
    video_crf: str = "5"
    audio_codec: str = "alac"
    movflags: str = "+faststart"

    def container_options(self) -> dict[str, str]:
        return {"movflags": self.movflags} if self.movflags else {}

    def to_metadata(self, *, has_audio: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "container": "mp4",
            "video_codec": self.video_codec,
            "video_pixel_format": self.video_pixel_format,
            "video_profile": self.video_profile,
            "video_preset": self.video_preset,
            "video_crf": self.video_crf,
            "movflags": self.movflags,
        }
        if has_audio:
            payload["audio_codec"] = self.audio_codec
        return payload


def media_path_for_sensor(sensor_name: str, modality: str, task_slug: str, seq: str | int = 1) -> str | None:
    return stream_media_path(sensor_name, modality, task_slug, seq)


def media_role_for_sensor(sensor_name: str, modality: str) -> str | None:
    if sensor_name == "camera" and modality == "rgb":
        return "primary_av"
    if modality in {"rgb", "visuotactile", "rgbd"}:
        return "video_only"
    return None


class MediaStreamWriter:
    def __init__(
        self,
        output_path: Path,
        *,
        fps: float,
        expected_audio: PlannedAudioStream | None = None,
        encoding_config: MediaEncodingConfig | None = None,
        session_start_ns: int | None = None,
    ) -> None:
        ensure_av_available()
        self.output_path = output_path
        self.fps = max(1, int(round(fps or 30.0)))
        self.expected_audio = expected_audio
        self.encoding_config = encoding_config or MediaEncodingConfig()
        self.session_start_ns = session_start_ns
        self._container: Any | None = None
        self._video_stream: Any | None = None
        self._audio_stream: Any | None = None
        self._video_frame_count = 0
        self._audio_sample_count = 0
        self._pending_audio_chunks: list[np.ndarray] = []
        self._pending_audio_starts: list[int] = []
        self._pending_audio_shapes: list[tuple[int, ...]] = []
        self._pending_audio_dtypes: list[np.dtype] = []
        self._last_video_duration_ticks: int = max(1, int(round(_VIDEO_TIME_BASE_DEN / self.fps)))

    def update_session_start_ns(self, session_start_ns: int | None) -> None:
        if session_start_ns is not None:
            self.session_start_ns = int(session_start_ns)

    def write_video_frame(
        self,
        value: np.ndarray,
        *,
        channel_order: str | None,
        frame_time_ns: int | None,
        frame_duration_ns: int | None,
    ) -> dict[str, Any]:
        array = np.asarray(value)
        if array.dtype != np.uint8 or array.ndim not in {2, 3}:
            raise ValueError("仅支持将 uint8 的 2D/3D 图像写入 MP4")
        if (
            array.ndim == 3
            and self.encoding_config.video_pixel_format == "yuv420p"
            and ((array.shape[0] % 2) or (array.shape[1] % 2))
        ):
            raise ValueError(
                f"yuv420p 编码要求偶数宽高，收到帧形状 {array.shape}"
            )

        self._ensure_container_for_video(array)
        index = self._video_frame_count
        frame = av.VideoFrame.from_ndarray(
            np.ascontiguousarray(array),
            format=_video_input_format(array, channel_order=channel_order),
        )
        pts, duration_ticks, time_base = self._video_timing(
            frame_time_ns=frame_time_ns,
            frame_duration_ns=frame_duration_ns,
            fallback_index=index,
        )
        frame.pts = pts
        frame.time_base = time_base
        for packet in self._video_stream.encode(frame):
            packet.duration = duration_ticks
            self._container.mux(packet)
        self._video_frame_count += 1
        self._last_video_duration_ticks = duration_ticks
        session_offset_ns = self._session_offset_ns(frame_time_ns)
        return {
            "path": str(self.output_path),
            "storage": "mp4_frame",
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "media_frame_index": index,
            "media_kind": "mp4",
            "fps": self.fps,
            "media_time_ns": frame_time_ns,
            "media_duration_ns": frame_duration_ns,
            "session_offset_ns": session_offset_ns,
            **({"channel_order": channel_order} if channel_order is not None else {}),
        }

    def write_audio_samples(self, value: np.ndarray, *, chunk_time_ns: int | None) -> dict[str, Any]:
        array = _normalize_audio_array(value)
        sample_start = self._audio_sample_start(chunk_time_ns, fallback=self._audio_sample_count)
        sample_count = int(array.shape[0])
        original = np.asarray(value)
        if self._container is None:
            self._pending_audio_chunks.append(array.copy())
            self._pending_audio_starts.append(sample_start)
            self._pending_audio_shapes.append(tuple(original.shape))
            self._pending_audio_dtypes.append(original.dtype)
        else:
            self._ensure_audio_stream()
            self._mux_audio_chunk(array, sample_start)
        self._audio_sample_count = max(self._audio_sample_count, sample_start + sample_count)
        media_duration_ns = int(round(sample_count * 1_000_000_000.0 / self._audio_sample_rate))
        return {
            "path": str(self.output_path),
            "storage": "mp4_audio",
            "shape": list(original.shape),
            "dtype": str(original.dtype),
            "sample_start": sample_start,
            "sample_count": sample_count,
            "media_kind": "mp4",
            "sample_rate": self._audio_sample_rate,
            "channels": array.shape[1],
            "media_time_ns": chunk_time_ns,
            "media_duration_ns": media_duration_ns,
            "session_offset_ns": self._session_offset_ns(chunk_time_ns),
        }

    def close(self) -> None:
        if self._container is None:
            if self._pending_audio_chunks:
                raise RuntimeError(f"媒体文件缺少视频帧，无法写出音轨: {self.output_path}")
            return
        if self._video_stream is not None:
            for packet in self._video_stream.encode():
                packet.duration = self._last_video_duration_ticks
                self._container.mux(packet)
        if self._audio_stream is not None:
            for packet in self._audio_stream.encode():
                self._container.mux(packet)
        self._container.close()
        self._container = None
        self._video_stream = None
        self._audio_stream = None
        self._pending_audio_chunks = []
        self._pending_audio_starts = []
        self._pending_audio_shapes = []
        self._pending_audio_dtypes = []

    def _ensure_container_for_video(self, array: np.ndarray) -> None:
        if self._container is not None:
            return
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._container = av.open(
            str(self.output_path),
            mode="w",
            container_options=self.encoding_config.container_options(),
        )
        self._video_stream = _add_video_stream(
            self._container,
            array=array,
            fps=self.fps,
            encoding_config=self.encoding_config,
        )
        if self.expected_audio is not None:
            self._audio_stream = _add_audio_stream(
                self._container,
                planned=self.expected_audio,
                encoding_config=self.encoding_config,
            )
            pending_chunks = list(self._pending_audio_chunks)
            pending_starts = list(self._pending_audio_starts)
            self._pending_audio_chunks = []
            self._pending_audio_starts = []
            for chunk, start in zip(pending_chunks, pending_starts):
                self._mux_audio_chunk(chunk, start)

    def _ensure_audio_stream(self) -> None:
        if self._audio_stream is None:
            planned = self.expected_audio or PlannedAudioStream()
            self._audio_stream = _add_audio_stream(
                self._container,
                planned=planned,
                encoding_config=self.encoding_config,
            )

    def _mux_audio_chunk(self, array: np.ndarray, sample_start: int) -> None:
        layout = _audio_layout(array.shape[1])
        audio_frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(array.T),
            format="s16",
            layout=layout,
        )
        audio_frame.sample_rate = self._audio_sample_rate
        audio_frame.pts = sample_start
        audio_frame.time_base = Fraction(1, audio_frame.sample_rate)
        for packet in self._audio_stream.encode(audio_frame):
            self._container.mux(packet)

    @property
    def _audio_sample_rate(self) -> int:
        return self.expected_audio.sample_rate if self.expected_audio is not None else 48000

    def _session_offset_ns(self, timestamp_ns: int | None) -> int | None:
        if timestamp_ns is None or self.session_start_ns is None:
            return None
        return int(timestamp_ns) - int(self.session_start_ns)

    def _audio_sample_start(self, chunk_time_ns: int | None, *, fallback: int) -> int:
        offset_ns = self._session_offset_ns(chunk_time_ns)
        if offset_ns is None:
            return int(fallback)
        return max(0, int(round(offset_ns * self._audio_sample_rate / 1_000_000_000.0)))

    def _video_timing(
        self,
        *,
        frame_time_ns: int | None,
        frame_duration_ns: int | None,
        fallback_index: int,
    ) -> tuple[int, int, Fraction]:
        time_base = Fraction(1, _VIDEO_TIME_BASE_DEN)
        nominal_duration_ns = int(round(1_000_000_000.0 / max(self.fps, 1)))
        effective_duration_ns = int(frame_duration_ns) if frame_duration_ns is not None else nominal_duration_ns
        if frame_time_ns is None:
            pts = fallback_index * max(1, int(round(_VIDEO_TIME_BASE_DEN / max(self.fps, 1))))
        else:
            offset_ns = self._session_offset_ns(frame_time_ns)
            if offset_ns is None:
                pts = int(round(frame_time_ns * _VIDEO_TIME_BASE_DEN / 1_000_000_000.0))
            else:
                pts = int(round(offset_ns * _VIDEO_TIME_BASE_DEN / 1_000_000_000.0))
        duration_ticks = max(1, int(round(effective_duration_ns * _VIDEO_TIME_BASE_DEN / 1_000_000_000.0)))
        return pts, duration_ticks, time_base


class MediaArtifactReader:
    def __init__(self) -> None:
        ensure_av_available()
        self._audio_cache: dict[Path, np.ndarray] = {}
        self._video_states: dict[Path, _VideoDecodeState] = {}

    def load_reference(self, reference: dict[str, Any]) -> np.ndarray:
        storage = reference.get("storage")
        path = Path(reference["path"])
        if storage == "mp4_frame":
            return self._load_video_frame(path, reference)
        if storage == "mp4_audio":
            return self._load_audio_slice(path, reference)
        raise ValueError(f"不支持的媒体引用类型: {storage}")

    def close(self) -> None:
        for state in self._video_states.values():
            state.close()
        self._video_states = {}
        self._audio_cache = {}

    def _load_video_frame(self, path: Path, reference: dict[str, Any]) -> np.ndarray:
        state = self._video_states.get(path)
        if state is None:
            state = _VideoDecodeState(path)
            self._video_states[path] = state
        return state.read_frame(reference)

    def _load_audio_slice(self, path: Path, reference: dict[str, Any]) -> np.ndarray:
        audio = self._audio_cache.get(path)
        if audio is None:
            audio = _decode_full_audio_track(path)
            self._audio_cache[path] = audio
        sample_start = int(reference.get("sample_start", 0))
        sample_count = int(reference.get("sample_count", 0))
        sample_end = sample_start + sample_count
        window = np.asarray(audio[sample_start:sample_end])
        target_dtype = np.dtype(reference.get("dtype", str(window.dtype)))
        window = _coerce_audio_dtype(window, target_dtype)
        shape = tuple(int(item) for item in reference.get("shape", []))
        if len(shape) == 1 and window.ndim == 2 and window.shape[1] == 1:
            return window[:, 0]
        if shape and tuple(window.shape) != shape:
            return window.reshape(shape)
        return window


class _VideoDecodeState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._container: Any | None = None
        self._stream: Any | None = None
        self._iterator: Any | None = None
        self._next_index = 0
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()

    def read_frame(self, reference: dict[str, Any]) -> np.ndarray:
        target_index = int(reference["media_frame_index"])
        if target_index in self._cache:
            cached = self._cache[target_index]
            return cached.copy()
        if target_index < self._next_index:
            self._reset()
        self._ensure_open()
        while self._next_index <= target_index:
            try:
                frame = next(self._iterator)
            except StopIteration as exc:  # pragma: no cover
                raise IndexError(f"媒体帧越界: {self.path}#{target_index}") from exc
            array = _decode_video_frame(frame, reference)
            self._cache[self._next_index] = array
            self._trim_cache()
            self._next_index += 1
        return self._cache[target_index].copy()

    def close(self) -> None:
        if self._container is not None:
            self._container.close()
        self._container = None
        self._stream = None
        self._iterator = None
        self._cache = OrderedDict()
        self._next_index = 0

    def _ensure_open(self) -> None:
        if self._container is not None:
            return
        self._container = av.open(str(self.path))
        self._stream = next(stream for stream in self._container.streams if stream.type == "video")
        self._iterator = self._container.decode(self._stream)

    def _reset(self) -> None:
        self.close()

    def _trim_cache(self) -> None:
        while len(self._cache) > _VIDEO_CACHE_SIZE:
            self._cache.popitem(last=False)


def _add_video_stream(
    container: Any,
    *,
    array: np.ndarray,
    fps: int,
    encoding_config: MediaEncodingConfig,
) -> Any:
    height, width = array.shape[:2]
    last_error: Exception | None = None
    pixel_format = "gray" if array.ndim == 2 else encoding_config.video_pixel_format
    codec_candidates = [encoding_config.video_codec]
    if encoding_config.video_codec != "mpeg4":
        codec_candidates.append("mpeg4")
    for codec_name in codec_candidates:
        try:
            stream = container.add_stream(codec_name, rate=_VIDEO_TIME_BASE_DEN)
            stream.width = width
            stream.height = height
            stream.pix_fmt = pixel_format
            stream.time_base = Fraction(1, _VIDEO_TIME_BASE_DEN)
            if hasattr(stream, "codec_context"):
                try:
                    stream.codec_context.max_b_frames = 0
                except Exception:
                    pass
            if codec_name == encoding_config.video_codec and hasattr(stream, "options"):
                stream.options = {
                    "crf": encoding_config.video_crf,
                    "preset": encoding_config.video_preset,
                    "profile": encoding_config.video_profile,
                    "bf": "0",
                }
            return stream
        except Exception as exc:  # pragma: no cover
            last_error = exc
    raise RuntimeError(f"无法创建视频编码器: {last_error}") from last_error


def _add_audio_stream(
    container: Any,
    *,
    planned: PlannedAudioStream,
    encoding_config: MediaEncodingConfig,
) -> Any:
    last_error: Exception | None = None
    codec_candidates = [encoding_config.audio_codec]
    if encoding_config.audio_codec != "aac":
        codec_candidates.append("aac")
    for codec_name in codec_candidates:
        try:
            stream = container.add_stream(codec_name, rate=planned.sample_rate)
            stream.layout = _audio_layout(planned.channels)
            return stream
        except Exception as exc:  # pragma: no cover
            last_error = exc
    raise RuntimeError(f"无法创建音频编码器: {last_error}") from last_error


def _video_input_format(array: np.ndarray, *, channel_order: str | None) -> str:
    if array.ndim == 2:
        return "gray"
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"不支持的视频帧形状: {array.shape}")
    if channel_order == "rgb":
        return "rgb24"
    return "bgr24"


def _decode_video_frame(frame: Any, reference: dict[str, Any]) -> np.ndarray:
    shape = tuple(int(item) for item in reference.get("shape", []))
    if len(shape) == 2:
        array = frame.to_ndarray(format="gray")
        return np.asarray(array, dtype=np.uint8).reshape(shape)
    if len(shape) == 3 and shape[2] == 3:
        format_name = "rgb24" if reference.get("channel_order") == "rgb" else "bgr24"
        array = frame.to_ndarray(format=format_name)
        return np.asarray(array, dtype=np.uint8).reshape(shape)
    raise ValueError(f"不支持的视频输出形状: {shape}")


def _decode_full_audio_track(path: Path) -> np.ndarray:
    container = av.open(str(path))
    try:
        stream = next(stream for stream in container.streams if stream.type == "audio")
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            array = frame.to_ndarray()
            if array.ndim == 1:
                chunks.append(array.reshape(-1, 1))
            else:
                chunks.append(array.T)
        if not chunks:
            return np.zeros((0, 1), dtype=np.int16)
        return np.concatenate(chunks, axis=0)
    finally:
        container.close()


def _normalize_audio_array(value: np.ndarray) -> np.ndarray:
    array = np.asarray(value)
    if array.ndim == 1:
        array = array.reshape(-1, 1)
    elif array.ndim != 2:
        raise ValueError(f"不支持的音频形状: {array.shape}")
    if array.dtype != np.int16:
        if np.issubdtype(array.dtype, np.floating):
            array = np.clip(array, -1.0, 1.0)
            array = (array * 32767.0).astype(np.int16)
        else:
            array = np.clip(array, -32768, 32767).astype(np.int16)
    return np.asarray(array, dtype=np.int16)


def _coerce_audio_dtype(array: np.ndarray, target_dtype: np.dtype[Any]) -> np.ndarray:
    if array.dtype == target_dtype:
        return array
    if target_dtype == np.dtype(np.int16):
        if array.dtype == np.int32 and array.size and np.max(np.abs(array)) > np.iinfo(np.int16).max:
            return np.right_shift(array, 16).astype(np.int16)
        return np.clip(array, -32768, 32767).astype(np.int16)
    return array.astype(target_dtype)


def _audio_layout(channels: int) -> str:
    if channels == 1:
        return "mono"
    if channels == 2:
        return "stereo"
    return f"{channels}c"
