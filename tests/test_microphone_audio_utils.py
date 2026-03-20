import tempfile
import unittest
from pathlib import Path

from sensors.microphone.audio_utils import (
    build_candidate_sample_rates,
    ensure_alsa_plugin_dir,
    list_input_devices,
    open_input_stream,
    resolve_input_device,
)


class _DummyStream:
    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _DummyAudio:
    def __init__(self, devices, *, invalid_rates=None) -> None:
        self._devices = list(devices)
        self.invalid_rates = set(invalid_rates or [])
        self.open_calls: list[dict[str, int]] = []

    def get_device_count(self) -> int:
        return len(self._devices)

    def get_device_info_by_index(self, index: int):
        return dict(self._devices[index])

    def open(self, *, format, channels, rate, input, input_device_index, frames_per_buffer):
        self.open_calls.append(
            {
                "channels": channels,
                "rate": rate,
                "input_device_index": input_device_index,
                "frames_per_buffer": frames_per_buffer,
            }
        )
        if rate in self.invalid_rates:
            raise OSError(-9997, "Invalid sample rate")
        return _DummyStream(rate)


class MicrophoneAudioUtilsTest(unittest.TestCase):
    def test_ensure_alsa_plugin_dir_prefers_system_dir_when_conda_dir_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            conda_prefix = Path(tmp_dir) / "conda_env"
            broken_dir = conda_prefix / "lib" / "alsa-lib"
            broken_dir.mkdir(parents=True)

            system_dir = Path(tmp_dir) / "system_alsa"
            system_dir.mkdir()
            (system_dir / "libasound_module_pcm_pipewire.so").write_text("", encoding="utf-8")

            env = {
                "CONDA_PREFIX": str(conda_prefix),
                "ALSA_PLUGIN_DIR": str(broken_dir),
            }

            resolved = ensure_alsa_plugin_dir(
                env=env,
                candidates=(str(system_dir),),
            )

        self.assertEqual(resolved, str(system_dir))
        self.assertEqual(env["ALSA_PLUGIN_DIR"], str(system_dir))

    def test_ensure_alsa_plugin_dir_keeps_existing_valid_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            valid_dir = Path(tmp_dir) / "alsa"
            valid_dir.mkdir()
            (valid_dir / "libasound_module_pcm_pipewire.so").write_text("", encoding="utf-8")
            env = {"ALSA_PLUGIN_DIR": str(valid_dir)}

            resolved = ensure_alsa_plugin_dir(env=env, candidates=())

        self.assertEqual(resolved, str(valid_dir))
        self.assertEqual(env["ALSA_PLUGIN_DIR"], str(valid_dir))

    def test_list_input_devices_filters_output_only_devices(self) -> None:
        audio = _DummyAudio(
            [
                {"name": "OutputOnly", "maxInputChannels": 0, "defaultSampleRate": 48000.0},
                {"name": "USB Mic", "maxInputChannels": 1, "defaultSampleRate": 48000.0},
            ]
        )

        devices = list_input_devices(audio)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["index"], 1)
        self.assertEqual(devices[0]["name"], "USB Mic")

    def test_resolve_input_device_defaults_to_first_input_device(self) -> None:
        audio = _DummyAudio(
            [
                {"name": "Speaker", "maxInputChannels": 0, "defaultSampleRate": 48000.0},
                {"name": "USB Mic", "maxInputChannels": 2, "defaultSampleRate": 48000.0},
            ]
        )

        device = resolve_input_device(audio)

        self.assertEqual(device["index"], 1)
        self.assertEqual(device["name"], "USB Mic")

    def test_build_candidate_sample_rates_prioritizes_requested_then_device_default(self) -> None:
        rates = build_candidate_sample_rates(44100, {"defaultSampleRate": 48000.0})

        self.assertEqual(rates[:2], [44100, 48000])
        self.assertIn(16000, rates)

    def test_open_input_stream_falls_back_to_device_default_rate(self) -> None:
        audio = _DummyAudio(
            [{"name": "USB Mic", "maxInputChannels": 1, "defaultSampleRate": 48000.0}],
            invalid_rates={44100},
        )

        stream, device_info, actual_rate = open_input_stream(
            audio,
            audio_format=8,
            channels=1,
            preferred_rate=44100,
            chunk=1024,
            device_index=None,
        )

        self.assertEqual(actual_rate, 48000)
        self.assertEqual(device_info["index"], 0)
        self.assertEqual(audio.open_calls[0]["rate"], 44100)
        self.assertEqual(audio.open_calls[1]["rate"], 48000)
        stream.close()

    def test_open_input_stream_raises_when_no_input_device_exists(self) -> None:
        audio = _DummyAudio([{"name": "Speaker", "maxInputChannels": 0, "defaultSampleRate": 48000.0}])

        with self.assertRaisesRegex(RuntimeError, "未找到可用的音频输入设备"):
            open_input_stream(
                audio,
                audio_format=8,
                channels=1,
                preferred_rate=48000,
                chunk=1024,
                device_index=None,
            )
