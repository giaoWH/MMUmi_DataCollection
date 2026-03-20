import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import sdk_record


class RecorderConfigLoadingTest(unittest.TestCase):
    def test_parse_args_uses_default_config_file_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "sensor_source: fake",
                        "duration_sec: 1.5",
                        "enable_camera: true",
                        "camera:",
                        "  width: 800",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.object(sdk_record, "DEFAULT_RECORD_CONFIG_CANDIDATES", (config_path,)):
                config, loaded_path = sdk_record.parse_args([])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertEqual(config.sensor_source, "fake")
        self.assertEqual(config.duration_sec, 1.5)
        self.assertTrue(config.enable_camera)
        self.assertEqual(config.camera.width, 800)

    def test_cli_overrides_config_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.json"
            config_path.write_text(
                json.dumps(
                    {
                        "sensor_source": "real",
                        "enable_ft": False,
                        "enable_motors": False,
                        "camera": {"width": 640},
                    }
                ),
                encoding="utf-8",
            )

            config, loaded_path = sdk_record.parse_args(
                [
                    "--config",
                    str(config_path),
                    "--sensor-source",
                    "fake",
                    "--enable-ft",
                    "--enable-motors",
                    "--camera-width",
                    "960",
                ]
            )

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertEqual(config.sensor_source, "fake")
        self.assertTrue(config.enable_ft)
        self.assertTrue(config.enable_motors)
        self.assertEqual(config.camera.width, 960)
