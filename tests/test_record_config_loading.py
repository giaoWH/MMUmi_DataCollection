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

    def test_parse_args_loads_camera_flip_vertical_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "enable_camera: true",
                        "camera:",
                        "  flip_vertical: true",
                    ]
                ),
                encoding="utf-8",
            )

            config, loaded_path = sdk_record.parse_args(["--config", str(config_path)])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertTrue(config.enable_camera)
        self.assertTrue(config.camera.flip_vertical)

    def test_parse_args_ignores_gelsight_flip_vertical_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "enable_gelsight: true",
                        "gelsight:",
                        "  flip_vertical: true",
                        "  width: 320",
                    ]
                ),
                encoding="utf-8",
            )

            config, loaded_path = sdk_record.parse_args(["--config", str(config_path)])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertTrue(config.enable_gelsight)
        self.assertEqual(config.gelsight.width, 320)
        self.assertFalse(hasattr(config.gelsight, "flip_vertical"))

    def test_parse_args_loads_gelsight_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "enable_gelsight: true",
                        "gelsight:",
                        "  width: 320",
                        "  height: 240",
                        "  fps: 60",
                    ]
                ),
                encoding="utf-8",
            )

            config, loaded_path = sdk_record.parse_args(["--config", str(config_path)])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertTrue(config.enable_gelsight)
        self.assertEqual(config.gelsight.width, 320)
        self.assertEqual(config.gelsight.height, 240)
        self.assertEqual(config.gelsight.fps, 60)

    def test_parse_args_loads_nested_realsense_and_trajectory_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "enable_realsense: true",
                        "realsense:",
                        "  enable_ir1: true",
                        "  enable_ir2: true",
                        "  enable_imu: true",
                        "  color:",
                        "    width: 848",
                        "    height: 480",
                        "    fps: 60",
                        "  derived:",
                        "    enable_pointcloud: true",
                        "trajectory:",
                        "  mode: stereo_inertial",
                        "  output_mode: stdout_jsonl",
                    ]
                ),
                encoding="utf-8",
            )

            config, loaded_path = sdk_record.parse_args(["--config", str(config_path)])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertTrue(config.enable_realsense)
        self.assertTrue(config.realsense.enable_ir1)
        self.assertTrue(config.realsense.enable_ir2)
        self.assertTrue(config.realsense.enable_imu)
        self.assertEqual(config.realsense.color.width, 848)
        self.assertEqual(config.realsense.color.height, 480)
        self.assertEqual(config.realsense.color.fps, 60)
        self.assertTrue(config.realsense.derived.enable_pointcloud)
        self.assertEqual(config.trajectory.mode, "stereo_inertial")
        self.assertEqual(config.trajectory.output_mode, "stdout_jsonl")
