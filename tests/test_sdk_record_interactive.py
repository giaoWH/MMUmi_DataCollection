import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import sdk_record
from scripts.sdk_extract_camera_audio import ExtractionResult
from sdk.annotations import AnnotationField, AnnotationSchema
from sdk.core import SensorRegistry
from sdk.sensors.base import SensorAdapter
from sdk.storage import SessionReader, discover_session_dirs


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
    def _list_session_dirs(self, output_root: Path) -> list[Path]:
        return discover_session_dirs(output_root)

    def _load_session_annotation(self, session_dir: Path) -> dict[str, object]:
        return json.loads((session_dir / "annotations" / "session.json").read_text(encoding="utf-8"))

    def test_interactive_mode_batches_task_name_and_rolls_over_after_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(
                tmp_dir,
                output_root=output_root,
                extra_payload={"task_name_batch_size": 2},
            )

            exit_code = sdk_record.main(
                ["--config", str(config_path)],
                key_source=sdk_record.TimedKeySource(
                    [
                        (0.05, " "),
                        (0.06, "pick_cube\n"),
                        (0.20, " "),
                        (0.35, " "),
                        (0.50, "\n"),
                        (0.60, " "),
                        (0.75, " "),
                        (0.90, "\n"),
                        (1.00, " "),
                        (1.01, "pick_cube\n"),
                        (1.15, " "),
                        (1.30, " "),
                        (1.45, "\n"),
                        (1.60, "\x03"),
                    ]
                ),
            )

            self.assertEqual(exit_code, 0)
            sessions = self._list_session_dirs(output_root)
            self.assertEqual(len(sessions), 3)
            self.assertEqual(sessions[0].relative_to(output_root), Path("pick_cube") / "01")
            self.assertEqual(sessions[1].relative_to(output_root), Path("pick_cube") / "02")
            self.assertEqual(sessions[2].relative_to(output_root), Path("pick_cube_02") / "01")
            self.assertEqual(self._load_session_annotation(sessions[0])["data"]["task_name"], "pick_cube")
            self.assertEqual(self._load_session_annotation(sessions[1])["data"]["task_name"], "pick_cube")
            self.assertEqual(self._load_session_annotation(sessions[2])["data"]["task_name"], "pick_cube")

            first_reader = SessionReader(sessions[0])
            second_reader = SessionReader(sessions[1])
            third_reader = SessionReader(sessions[2])
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
            self.assertEqual(first_notes["record_run_id"], third_reader.manifest.notes["record_run_id"])
            self.assertEqual(first_notes["shared_calibration"], second_notes["shared_calibration"])
            self.assertEqual(first_notes["session_index"], 1)
            self.assertEqual(second_notes["session_index"], 2)
            self.assertEqual(third_reader.manifest.item_name, "01")
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
                        (0.06, "discard_me\n"),
                        (0.20, " "),
                        (0.35, " "),
                        (0.50, "q"),
                        (0.70, " "),
                        (0.85, " "),
                        (1.00, "\n"),
                        (1.20, "\x03"),
                    ]
                ),
            )

            self.assertEqual(exit_code, 0)
            sessions = self._list_session_dirs(output_root)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].relative_to(output_root), Path("discard_me") / "01")
            self.assertEqual(self._load_session_annotation(sessions[0])["data"]["task_name"], "discard_me")

    def test_interactive_mode_discards_active_session_on_ctrl_c(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)

            exit_code = sdk_record.main(
                ["--config", str(config_path)],
                key_source=sdk_record.TimedKeySource(
                    [
                        (0.05, " "),
                        (0.06, "ctrl_c_task\n"),
                        (0.18, " "),
                        (0.30, "\x03"),
                    ]
                ),
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(self._list_session_dirs(output_root), [])

    def test_complete_stopped_session_exports_microphone_wav_when_microphone_enabled(self) -> None:
        session = unittest.mock.MagicMock()
        session.session_index = 1
        session.session_info = unittest.mock.MagicMock()
        session.session_info.notes = {}
        session.session_info.sensors = {"microphone": {"sensor_type": "microphone_sensor"}}
        session.session_info.output_dir = Path("/tmp/fake-session")
        session.writer = unittest.mock.MagicMock()
        session.writer.get_diagnostics.side_effect = [{"phase": "pre"}, {"phase": "post"}]
        session.writer._build_manifest.return_value = {"manifest": True}
        session.logger = unittest.mock.MagicMock()

        with patch.object(
            sdk_record,
            "extract_camera_audio_to_wavs",
            return_value=[
                ExtractionResult(
                    mp4_path=Path("/tmp/fake-session/streams/camera/sample.mp4"),
                    wav_path=Path("/tmp/fake-session/streams/microphone/sample.wav"),
                    status="written",
                    sample_rate=48000,
                    channels=1,
                    sample_count=1024,
                )
            ],
        ):
            stopped = sdk_record._complete_stopped_session(
                session=session,
                sensor_runtime_status_snapshot={},
                session_sensor_stats={},
                stop_reason="operator_stop",
            )

        self.assertEqual(stopped.session_index, 1)
        session.writer.close.assert_called_once()
        session.writer._write_manifest.assert_called_once()
        self.assertEqual(session.session_info.notes["microphone_wav_export"]["status"], "ok")
        self.assertEqual(session.session_info.notes["microphone_wav_export"]["summary"]["written"], 1)

    def test_complete_stopped_session_skips_microphone_wav_export_when_microphone_disabled(self) -> None:
        session = unittest.mock.MagicMock()
        session.session_index = 1
        session.session_info = unittest.mock.MagicMock()
        session.session_info.notes = {}
        session.session_info.sensors = {"camera": {"sensor_type": "camera_sensor"}}
        session.session_info.output_dir = Path("/tmp/fake-session")
        session.writer = unittest.mock.MagicMock()
        session.writer.get_diagnostics.side_effect = [{"phase": "pre"}, {"phase": "post"}]
        session.writer._build_manifest.return_value = {"manifest": True}
        session.logger = unittest.mock.MagicMock()

        with patch.object(sdk_record, "extract_camera_audio_to_wavs") as export_mock:
            sdk_record._complete_stopped_session(
                session=session,
                sensor_runtime_status_snapshot={},
                session_sensor_stats={},
                stop_reason="operator_stop",
            )

        export_mock.assert_not_called()
        self.assertNotIn("microphone_wav_export", session.session_info.notes)

    def test_interactive_mode_fails_fast_without_tty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)

            with patch.object(sdk_record.sys.stdin, "isatty", return_value=False):
                exit_code = sdk_record.main(["--config", str(config_path)])

            self.assertEqual(exit_code, 1)
            self.assertEqual(self._list_session_dirs(output_root), [])

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
                                (0.06, "slow_stop\n"),
                                (0.20, " "),
                                (0.35, " "),
                                (0.36, "\n"),
                                (0.75, "\x03"),
                            ]
                        ),
                    )

            output = captured.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertEqual(self._list_session_dirs(output_root), [])
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
                            (0.06, "ctrl_c_while_stopping\n"),
                            (0.20, " "),
                            (0.35, " "),
                            (0.60, "\x03"),
                        ]
                    ),
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(self._list_session_dirs(output_root), [])

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
                    key_source=sdk_record.TimedKeySource(
                        [
                            (0.05, " "),
                            (0.06, "sensor_failure\n"),
                            (0.10, " "),
                        ]
                    ),
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(self._list_session_dirs(output_root), [])

    def test_interactive_mode_does_not_create_session_before_record_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)

            exit_code = sdk_record.main(
                ["--config", str(config_path)],
                key_source=sdk_record.TimedKeySource(
                    [
                        (0.05, " "),
                        (0.06, "prepare_only\n"),
                        (0.25, "\x03"),
                    ]
                ),
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(self._list_session_dirs(output_root), [])

    def test_interactive_mode_cancels_empty_task_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)

            exit_code = sdk_record.main(
                ["--config", str(config_path)],
                key_source=sdk_record.TimedKeySource(
                    [
                        (0.05, " "),
                        (0.06, "\n"),
                        (0.20, "\x03"),
                    ]
                ),
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(self._list_session_dirs(output_root), [])

    def test_interactive_mode_fails_when_task_name_schema_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)
            invalid_schema = AnnotationSchema(
                version="1.0.0",
                fields=[
                    AnnotationField(id="instruction", scope="session", type="string", label="Instruction"),
                ],
            )

            with patch.object(sdk_record, "_load_interactive_annotation_schema", return_value=invalid_schema):
                exit_code = sdk_record.main(
                    ["--config", str(config_path)],
                    key_source=sdk_record.TimedKeySource([(0.05, "\x03")]),
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(self._list_session_dirs(output_root), [])

    def test_interactive_mode_uses_chinese_task_name_for_session_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = _write_interactive_config(tmp_dir, output_root=output_root)

            exit_code = sdk_record.main(
                ["--config", str(config_path)],
                key_source=sdk_record.TimedKeySource(
                    [
                        (0.05, " "),
                        (0.06, "抓取方块\n"),
                        (0.20, " "),
                        (0.35, " "),
                        (0.50, "\n"),
                        (0.70, "\x03"),
                    ]
                ),
            )

            self.assertEqual(exit_code, 0)
            sessions = self._list_session_dirs(output_root)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].relative_to(output_root), Path("抓取方块") / "01")
            self.assertEqual(self._load_session_annotation(sessions[0])["data"]["task_name"], "抓取方块")
