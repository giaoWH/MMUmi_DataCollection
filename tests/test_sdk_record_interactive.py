import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import sdk_record
from sdk.core import SensorRegistry
from sdk.sensors.base import SensorAdapter
from sdk.storage import SessionReader


def _write_interactive_config(
    tmp_dir: str,
    *,
    output_root: Path,
    extra_payload: dict[str, object] | None = None,
) -> Path:
    payload = {
        "output_root": str(output_root),
        "sensor_source": "fake",
        "interactive": True,
        "align_rate_hz": 20.0,
        "duration_sec": 0.1,
        "startup_discard_sec": 0.0,
        "enable_ft": True,
        "enable_imu": True,
        "enable_realsense": False,
        "enable_motors": False,
        "enable_microphone": False,
        "enable_camera": False,
        "enable_gelsight": False,
        "gravity_compensation": {
            "enabled": False,
            "stabilization_sec": 0.0,
            "calibration_duration_sec": 0.0,
            "minimum_samples": 5,
        },
    }
    if extra_payload:
        payload.update(extra_payload)

    config_path = Path(tmp_dir) / "interactive_record.json"
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


class FailingSensor(SensorAdapter):
    def __init__(self, *, fail_after_sec: float) -> None:
        super().__init__("failing_sensor", "test_sensor", "test")
        self.fail_after_sec = fail_after_sec
        self._running = False
        self._failed = False
        self._started_at = 0.0
        self._frame_id = 0
        self._last_frame_at = 0.0

    def start(self) -> None:
        self._running = True
        self._failed = False
        self._started_at = time.perf_counter()
        self._frame_id = 0
        self._last_frame_at = 0.0

    def stop(self) -> None:
        self._running = False

    def read_frame(self):
        if not self._running:
            return None
        now = time.perf_counter()
        if not self._failed and (now - self._started_at) >= self.fail_after_sec:
            self._running = False
            self._failed = True
            return None
        if (now - self._last_frame_at) < 0.01:
            return None
        self._last_frame_at = now
        self._frame_id += 1
        return None

    def get_metadata(self) -> dict[str, object]:
        return {
            "sensor_type": self.sensor_type,
            "modality": self.modality,
        }

    def get_status(self) -> dict[str, object]:
        return {
            "running": self._running,
            "frame_count": self._frame_id,
            "latest_timestamp": self._last_frame_at,
        }

    def is_ready(self) -> bool:
        return True


class InteractiveRecordTest(unittest.TestCase):
    def test_interactive_mode_can_save_multiple_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)

            exit_code = sdk_record.main(
                ["--config", str(config_path)],
                key_source=sdk_record.TimedKeySource(
                    [
                        (0.05, " "),
                        (0.20, " "),
                        (0.35, "\n"),
                        (0.45, " "),
                        (0.60, " "),
                        (0.75, "\n"),
                        (0.95, "\x03"),
                    ]
                ),
            )

            self.assertEqual(exit_code, 0)
            sessions = sorted(output_root.glob("session_*"))
            self.assertEqual(len(sessions), 2)

            first_reader = SessionReader(sessions[0])
            second_reader = SessionReader(sessions[1])
            first_ft_frames = list(first_reader.iter_sensor_frames("ft", load_payload=False))
            second_ft_frames = list(second_reader.iter_sensor_frames("ft", load_payload=False))
            self.assertTrue(first_ft_frames)
            self.assertTrue(second_ft_frames)
            self.assertGreater(second_ft_frames[0].frame_id - first_ft_frames[-1].frame_id, 5)

            first_notes = first_reader.manifest.notes
            second_notes = second_reader.manifest.notes
            self.assertEqual(first_notes["record_mode"], "interactive")
            self.assertEqual(second_notes["record_mode"], "interactive")
            self.assertEqual(first_notes["record_run_id"], second_notes["record_run_id"])
            self.assertEqual(first_notes["shared_calibration"], second_notes["shared_calibration"])
            self.assertEqual(first_notes["session_index"], 1)
            self.assertEqual(second_notes["session_index"], 2)
            self.assertFalse(first_notes["discarded"])
            self.assertFalse(second_notes["discarded"])
            self.assertIn("session_sensor_stats", first_notes)
            self.assertIn("session_sensor_stats", second_notes)
            self.assertIn("sensor_runtime_status_snapshot", first_notes)
            self.assertIn("sensor_runtime_status_snapshot", second_notes)
            self.assertGreater(first_notes["session_sensor_stats"]["ft"]["produced_frame_count"], 0)
            self.assertGreater(second_notes["session_sensor_stats"]["ft"]["produced_frame_count"], 0)
            self.assertLess(
                second_notes["session_sensor_stats"]["ft"]["produced_frame_count"],
                second_notes["sensor_runtime_status_snapshot"]["ft"]["frame_count"],
            )
            self.assertLess(second_notes["session_sensor_stats"]["ft"]["max_queue_depth"], 128)

    def test_interactive_mode_can_discard_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)

            exit_code = sdk_record.main(
                ["--config", str(config_path)],
                key_source=sdk_record.TimedKeySource(
                    [
                        (0.05, " "),
                        (0.20, " "),
                        (0.35, "q"),
                        (0.55, "\x03"),
                    ]
                ),
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(sorted(output_root.glob("session_*")), [])

    def test_interactive_mode_discards_active_session_on_ctrl_c(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)

            exit_code = sdk_record.main(
                ["--config", str(config_path)],
                key_source=sdk_record.TimedKeySource(
                    [
                        (0.05, " "),
                        (0.18, "\x03"),
                    ]
                ),
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(sorted(output_root.glob("session_*")), [])

    def test_interactive_mode_fails_fast_without_tty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)

            with patch.object(sdk_record.sys.stdin, "isatty", return_value=False):
                exit_code = sdk_record.main(["--config", str(config_path)])

            self.assertEqual(exit_code, 1)
            self.assertEqual(sorted(output_root.glob("session_*")), [])

    def test_interactive_mode_shows_stopping_message_and_drops_early_enter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)
            original_complete = sdk_record._complete_stopped_session

            def slow_complete(**kwargs):
                time.sleep(0.25)
                return original_complete(**kwargs)

            captured = io.StringIO()
            with patch.object(sdk_record, "_complete_stopped_session", side_effect=slow_complete):
                with contextlib.redirect_stdout(captured):
                    exit_code = sdk_record.main(
                        ["--config", str(config_path)],
                        key_source=sdk_record.TimedKeySource(
                            [
                                (0.05, " "),
                                (0.20, " "),
                                (0.21, "\n"),
                                (0.60, "\x03"),
                            ]
                        ),
                    )

            output = captured.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(sorted(output_root.glob("session_*")), [])
            self.assertIn("停止中，正在收尾写盘，请稍候...", output)
            self.assertIn("session 已停止。按 Enter 保存，按 q 放弃，按 Ctrl+C 退出。", output)
            self.assertLess(
                output.index("停止中，正在收尾写盘，请稍候..."),
                output.index("session 已停止。按 Enter 保存，按 q 放弃，按 Ctrl+C 退出。"),
            )

    def test_interactive_mode_discards_session_when_ctrl_c_during_stopping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)
            original_complete = sdk_record._complete_stopped_session

            def slow_complete(**kwargs):
                time.sleep(0.25)
                return original_complete(**kwargs)

            with patch.object(sdk_record, "_complete_stopped_session", side_effect=slow_complete):
                exit_code = sdk_record.main(
                    ["--config", str(config_path)],
                    key_source=sdk_record.TimedKeySource(
                        [
                            (0.05, " "),
                            (0.20, " "),
                            (0.21, "\x03"),
                        ]
                    ),
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(sorted(output_root.glob("session_*")), [])

    def test_interactive_mode_aborts_when_sensor_fails_mid_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(
                tmp_dir,
                output_root=output_root,
                extra_payload={"enable_ft": False, "enable_imu": False},
            )

            registry = SensorRegistry()
            registry.register(FailingSensor(fail_after_sec=0.12))

            with patch.object(sdk_record, "build_registry", return_value=registry):
                exit_code = sdk_record.main(
                    ["--config", str(config_path)],
                    key_source=sdk_record.TimedKeySource([(0.05, " ")]),
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(sorted(output_root.glob("session_*")), [])
