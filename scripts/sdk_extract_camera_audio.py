from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import wave

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import av
    from av.audio.resampler import AudioResampler
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("未安装 PyAV，无法从 MP4 中提取音轨") from exc


@dataclass(frozen=True)
class ExtractionResult:
    mp4_path: Path
    wav_path: Path
    status: str
    sample_rate: int | None = None
    channels: int | None = None
    sample_count: int = 0
    detail: str | None = None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="递归扫描 sessions 目录，把 streams/camera/*.mp4 的音轨抽取为同名 WAV 文件。",
    )
    parser.add_argument(
        "input_path",
        type=Path,
        help="sessions 根目录，或单个 camera mp4 文件路径",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="如果同名 wav 已存在，则覆盖它",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要处理的文件，不真正写出 wav",
    )
    return parser.parse_args()


def _looks_like_camera_mp4(path: Path) -> bool:
    if path.suffix.lower() != ".mp4":
        return False
    parts = path.parts
    if len(parts) < 3:
        return False
    return parts[-2] == "camera" and parts[-3] in {"stream", "streams"}


def _discover_camera_mp4s(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if not _looks_like_camera_mp4(input_path):
            raise ValueError(f"不是 stream(s)/camera 下的 mp4: {input_path}")
        return [input_path]

    if not input_path.exists():
        raise FileNotFoundError(f"路径不存在: {input_path}")

    paths = set(input_path.glob("**/streams/camera/*.mp4"))
    paths.update(input_path.glob("**/stream/camera/*.mp4"))
    return sorted(path.resolve() for path in paths)


def _coerce_to_int16(samples: np.ndarray) -> np.ndarray:
    array = np.asarray(samples)
    if np.issubdtype(array.dtype, np.integer):
        if array.dtype == np.int16:
            return array
        info = np.iinfo(array.dtype)
        scale = max(abs(info.min), abs(info.max))
        if scale == 0:
            return array.astype(np.int16, copy=False)
        normalized = np.asarray(array, dtype=np.float64) / float(scale)
        return np.clip(np.round(normalized * 32767.0), -32768, 32767).astype(np.int16)
    if np.issubdtype(array.dtype, np.floating):
        return np.clip(np.round(array * 32767.0), -32768, 32767).astype(np.int16)
    raise TypeError(f"不支持的音频 dtype: {array.dtype}")


def _frame_to_interleaved_pcm(frame: av.AudioFrame, *, expected_channels: int) -> tuple[bytes, int]:
    array = _coerce_to_int16(frame.to_ndarray())
    if array.ndim == 1:
        interleaved = array
        samples_per_channel = int(array.shape[0] // max(expected_channels, 1))
        return interleaved.tobytes(), samples_per_channel

    if array.ndim != 2:
        flattened = array.reshape(-1)
        samples_per_channel = int(flattened.shape[0] // max(expected_channels, 1))
        return flattened.tobytes(), samples_per_channel

    if expected_channels <= 1:
        mono = array.reshape(-1)
        return mono.tobytes(), int(mono.shape[0])

    if array.shape[0] == expected_channels:
        interleaved = np.ascontiguousarray(array.T.reshape(-1))
        return interleaved.tobytes(), int(array.shape[1])

    if array.shape[1] == expected_channels:
        interleaved = np.ascontiguousarray(array.reshape(-1))
        return interleaved.tobytes(), int(array.shape[0])

    flattened = np.ascontiguousarray(array.reshape(-1))
    samples_per_channel = int(flattened.shape[0] // expected_channels)
    return flattened.tobytes(), samples_per_channel


def _extract_audio_to_wav(mp4_path: Path, wav_path: Path, *, overwrite: bool, dry_run: bool) -> ExtractionResult:
    if wav_path.exists() and not overwrite:
        return ExtractionResult(mp4_path=mp4_path, wav_path=wav_path, status="skip_exists")

    container = av.open(str(mp4_path))
    try:
        audio_stream = next((stream for stream in container.streams if stream.type == "audio"), None)
        if audio_stream is None:
            return ExtractionResult(
                mp4_path=mp4_path,
                wav_path=wav_path,
                status="skip_no_audio",
                detail="mp4 中没有音轨",
            )

        codec_context = audio_stream.codec_context
        channels = int(getattr(codec_context, "channels", 0) or 1)
        sample_rate = int(getattr(audio_stream, "sample_rate", None) or getattr(codec_context, "sample_rate", None) or 48000)
        layout = getattr(codec_context, "layout", None)
        layout_value = getattr(layout, "name", None) or channels

        if dry_run:
            return ExtractionResult(
                mp4_path=mp4_path,
                wav_path=wav_path,
                status="dry_run",
                sample_rate=sample_rate,
                channels=channels,
            )

        wav_path.parent.mkdir(parents=True, exist_ok=True)
        resampler = AudioResampler(format="s16", layout=layout_value, rate=sample_rate)
        total_samples = 0

        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)

            for frame in container.decode(audio_stream):
                for out_frame in resampler.resample(frame):
                    pcm_bytes, samples_per_channel = _frame_to_interleaved_pcm(
                        out_frame,
                        expected_channels=channels,
                    )
                    if pcm_bytes:
                        wav_file.writeframes(pcm_bytes)
                        total_samples += samples_per_channel

            for out_frame in resampler.resample(None):
                pcm_bytes, samples_per_channel = _frame_to_interleaved_pcm(
                    out_frame,
                    expected_channels=channels,
                )
                if pcm_bytes:
                    wav_file.writeframes(pcm_bytes)
                    total_samples += samples_per_channel

        return ExtractionResult(
            mp4_path=mp4_path,
            wav_path=wav_path,
            status="written",
            sample_rate=sample_rate,
            channels=channels,
            sample_count=total_samples,
        )
    finally:
        container.close()


def main() -> int:
    args = _parse_args()
    try:
        mp4_paths = _discover_camera_mp4s(args.input_path.expanduser().resolve())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    if not mp4_paths:
        print("没有找到 streams/camera/*.mp4 或 stream/camera/*.mp4 文件。")
        return 0

    results: list[ExtractionResult] = []
    for mp4_path in mp4_paths:
        wav_path = mp4_path.with_suffix(".wav")
        try:
            result = _extract_audio_to_wav(
                mp4_path,
                wav_path,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            result = ExtractionResult(
                mp4_path=mp4_path,
                wav_path=wav_path,
                status="error",
                detail=str(exc),
            )
        results.append(result)

        if result.status == "written":
            print(
                f"[written] {result.wav_path} | rate={result.sample_rate}Hz channels={result.channels} "
                f"samples={result.sample_count}"
            )
        elif result.status == "dry_run":
            print(f"[dry-run] {result.mp4_path} -> {result.wav_path}")
        elif result.status == "skip_exists":
            print(f"[skip_exists] {result.wav_path}")
        elif result.status == "skip_no_audio":
            print(f"[skip_no_audio] {result.mp4_path}")
        else:
            print(f"[error] {result.mp4_path} | {result.detail}", file=sys.stderr)

    written = sum(result.status == "written" for result in results)
    skipped_exists = sum(result.status == "skip_exists" for result in results)
    skipped_no_audio = sum(result.status == "skip_no_audio" for result in results)
    dry_run_count = sum(result.status == "dry_run" for result in results)
    errors = sum(result.status == "error" for result in results)
    print(
        "summary:"
        f" total={len(results)}"
        f" written={written}"
        f" skip_exists={skipped_exists}"
        f" skip_no_audio={skipped_no_audio}"
        f" dry_run={dry_run_count}"
        f" errors={errors}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
