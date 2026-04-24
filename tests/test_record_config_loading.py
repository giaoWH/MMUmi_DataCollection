import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import sdk_record
from sdk.annotations import AnnotationField, AnnotationSchema


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

    def test_parse_args_loads_task_name(self) -> None:
        with patch.object(sdk_record, "DEFAULT_RECORD_CONFIG_CANDIDATES", ()):
            config, loaded_path = sdk_record.parse_args(["--task-name", "pick cube"])

        self.assertIsNone(loaded_path)
        self.assertEqual(config.task_name, "pick cube")

    def test_parse_args_defaults_task_name_batch_size_to_five(self) -> None:
        config, _loaded_path = sdk_record.parse_args([])

        self.assertEqual(config.task_name_batch_size, 5)
        self.assertEqual(config.device_role, "collector")

    def test_parse_args_loads_task_name_batch_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text("task_name_batch_size: 7\n", encoding="utf-8")

            config, loaded_path = sdk_record.parse_args(["--config", str(config_path)])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertEqual(config.task_name_batch_size, 7)

    def test_parse_args_loads_end_effector_network_uplink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "device_role: end_effector",
                        "network_uplink:",
                        "  enabled: true",
                        "  host: 192.168.10.2",
                        "  port: 9876",
                        "  reconnect_interval_sec: 0.2",
                        "  send_timeout_sec: 0.3",
                        "  poll_interval_sec: 0.004",
                    ]
                ),
                encoding="utf-8",
            )

            config, loaded_path = sdk_record.parse_args(["--config", str(config_path)])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertEqual(config.device_role, "end_effector")
        self.assertTrue(config.network_uplink.enabled)
        self.assertEqual(config.network_uplink.host, "192.168.10.2")
        self.assertEqual(config.network_uplink.port, 9876)
        self.assertEqual(config.network_uplink.reconnect_interval_sec, 0.2)
        self.assertEqual(config.network_uplink.send_timeout_sec, 0.3)
        self.assertEqual(config.network_uplink.poll_interval_sec, 0.004)

    def test_main_fails_fast_without_noninteractive_task_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.json"
            config_path.write_text(
                json.dumps(
                    {
                        "output_root": str(Path(tmp_dir) / "sessions"),
                        "sensor_source": "fake",
                        "duration_sec": 0.1,
                    }
                ),
                encoding="utf-8",
            )

            exit_code = sdk_record.main(["--config", str(config_path)])

        self.assertEqual(exit_code, 1)

    def test_main_fails_fast_when_noninteractive_task_name_schema_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.json"
            config_path.write_text(
                json.dumps(
                    {
                        "output_root": str(Path(tmp_dir) / "sessions"),
                        "sensor_source": "fake",
                        "duration_sec": 0.1,
                        "task_name": "pick_cube",
                    }
                ),
                encoding="utf-8",
            )
            invalid_schema = AnnotationSchema(
                version="1.0.0",
                fields=[AnnotationField(id="instruction", scope="session", type="string", label="Instruction")],
            )

            with patch.object(sdk_record, "_load_interactive_annotation_schema", return_value=invalid_schema):
                exit_code = sdk_record.main(["--config", str(config_path)])

        self.assertEqual(exit_code, 1)

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

    def test_parse_args_loads_camera_flip_horizontal_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "enable_camera: true",
                        "camera:",
                        "  flip_horizontal: true",
                    ]
                ),
                encoding="utf-8",
            )

            config, loaded_path = sdk_record.parse_args(["--config", str(config_path)])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertTrue(config.enable_camera)
        self.assertTrue(config.camera.flip_horizontal)

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

    def test_parse_args_ignores_gelsight_flip_horizontal_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "enable_gelsight: true",
                        "gelsight:",
                        "  flip_horizontal: true",
                        "  width: 320",
                    ]
                ),
                encoding="utf-8",
            )

            config, loaded_path = sdk_record.parse_args(["--config", str(config_path)])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertTrue(config.enable_gelsight)
        self.assertEqual(config.gelsight.width, 320)
        self.assertFalse(hasattr(config.gelsight, "flip_horizontal"))

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
                        "  source_name: orbslam3_pc",
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
        self.assertEqual(config.trajectory.source_name, "orbslam3_pc")

    def test_parse_args_loads_ft_and_motor_channel_switches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "enable_ft: true",
                        "enable_motors: true",
                        "ft:",
                        "  enable_torque: false",
                        "motors:",
                        "  enable_motor_1: false",
                        "  enable_motor_2: true",
                    ]
                ),
                encoding="utf-8",
            )

            config, loaded_path = sdk_record.parse_args(["--config", str(config_path)])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertTrue(config.enable_ft)
        self.assertFalse(config.ft.enable_torque)
        self.assertTrue(config.enable_motors)
        self.assertFalse(config.motors.enable_motor_1)
        self.assertTrue(config.motors.enable_motor_2)

    def test_parse_args_loads_startup_discard_sec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "duration_sec: 10.0",
                        "startup_discard_sec: 0.5",
                    ]
                ),
                encoding="utf-8",
            )

            config, loaded_path = sdk_record.parse_args(["--config", str(config_path)])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertEqual(config.duration_sec, 10.0)
        self.assertEqual(config.startup_discard_sec, 0.5)

    def test_parse_args_loads_interactive_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "interactive: true",
                        "duration_sec: 10.0",
                    ]
                ),
                encoding="utf-8",
            )

            config, loaded_path = sdk_record.parse_args(["--config", str(config_path)])

        self.assertEqual(loaded_path, config_path.resolve())
        self.assertTrue(config.interactive)
        self.assertEqual(config.duration_sec, 10.0)
