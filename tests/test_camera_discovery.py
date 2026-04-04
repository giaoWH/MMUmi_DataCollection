import sys
import tempfile
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

from scripts import sdk_discover_cameras
from scripts.sdk_discover_ports import (
    RealSenseDeviceInfo,
    VideoDeviceInfo,
    build_visual_discovery_plan,
    run_device_discovery,
    run_visual_discovery,
)
from sdk.config import load_config_file


class CameraDiscoveryTest(unittest.TestCase):
    def test_build_visual_discovery_plan_uses_enabled_order_and_skips_non_visual(self) -> None:
        payload = {
            "output_root": "sessions",
            "enable_ft": True,
            "enable_realsense": True,
            "enable_camera": True,
            "enable_gelsight": True,
            "enable_imu": False,
        }

        steps, skipped = build_visual_discovery_plan(payload)

        self.assertEqual([step.sensor_key for step in steps], ["realsense", "camera", "gelsight"])
        self.assertEqual(skipped, ["ft"])

    def test_run_visual_discovery_writes_camera_gelsight_and_realsense_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "enable_realsense: true",
                        "enable_camera: true",
                        "enable_gelsight: true",
                        "realsense:",
                        "  serial_number: null",
                        "camera:",
                        "  device_index: 0",
                        "gelsight:",
                        "  device_index: 1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with (
                patch(
                    "scripts.sdk_discover_ports.discover_realsense_devices",
                    return_value=[RealSenseDeviceInfo(serial_number="RS123", name="D435i")],
                ),
                patch(
                    "scripts.sdk_discover_ports.scan_video_devices",
                    return_value=[
                        VideoDeviceInfo(index=2, width=640, height=480, fps=30.0, name="USB Camera"),
                        VideoDeviceInfo(index=5, width=320, height=240, fps=25.0, name="GelSight Mini"),
                    ],
                ),
                patch(
                    "builtins.input",
                    side_effect=[
                        "2",
                        "5",
                    ],
                ),
            ):
                exit_code = run_visual_discovery(
                    config_path,
                    max_index=10,
                    preview_sec=0.1,
                    include_realsense_nodes=False,
                    create_backup=False,
                )

            self.assertEqual(exit_code, 0)
            payload = load_config_file(config_path)
            self.assertIsNone(payload["realsense"]["serial_number"])
            self.assertEqual(payload["camera"]["device_index"], 2)
            self.assertEqual(payload["gelsight"]["device_index"], 5)

    def test_run_visual_discovery_clears_existing_realsense_serial_when_device_is_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "enable_realsense: true",
                        "realsense:",
                        "  serial_number: RS_OLD",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch(
                "scripts.sdk_discover_ports.discover_realsense_devices",
                return_value=[RealSenseDeviceInfo(serial_number="RS123", name="D435i")],
            ):
                exit_code = run_visual_discovery(
                    config_path,
                    max_index=10,
                    preview_sec=0.1,
                    include_realsense_nodes=False,
                    create_backup=False,
                )

            self.assertEqual(exit_code, 0)
            payload = load_config_file(config_path)
            self.assertIsNone(payload["realsense"]["serial_number"])

    def test_run_device_discovery_merges_serial_and_visual_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "enable_ft: true",
                        "enable_realsense: true",
                        "enable_camera: true",
                        "ft:",
                        "  port: /dev/ttyUSB0",
                        "realsense:",
                        "  serial_number: null",
                        "camera:",
                        "  device_index: 0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with (
                patch(
                    "scripts.sdk_discover_ports.snapshot_serial_ports",
                    side_effect=[
                        {},
                        {},
                        {"/dev/ttyUSB3": "USB Serial"},
                    ],
                ),
                patch(
                    "scripts.sdk_discover_ports.discover_realsense_devices",
                    return_value=[RealSenseDeviceInfo(serial_number="RS999", name="D435i")],
                ),
                patch(
                    "scripts.sdk_discover_ports.scan_video_devices",
                    return_value=[VideoDeviceInfo(index=4, width=640, height=480, fps=30.0, name="USB Camera")],
                ),
                patch("builtins.input", side_effect=["", "", "4"]),
            ):
                exit_code = run_device_discovery(
                    config_path,
                    timeout=0.1,
                    poll_interval=0.0,
                    max_index=10,
                    preview_sec=0.1,
                    include_realsense_nodes=False,
                    create_backup=False,
                )

            self.assertEqual(exit_code, 0)
            payload = load_config_file(config_path)
            self.assertEqual(payload["ft"]["port"], "/dev/ttyUSB3")
            self.assertIsNone(payload["realsense"]["serial_number"])
            self.assertEqual(payload["camera"]["device_index"], 4)

    def test_sdk_discover_cameras_wrapper_forces_visual_scope(self) -> None:
        with patch("scripts.sdk_discover_cameras.run_cli", return_value=0) as run_cli_mock:
            with self.assertRaises(SystemExit) as ctx:
                with patch("sys.argv", ["sdk_discover_cameras.py", "--config", "configs/record.yaml"]):
                    sdk_discover_cameras.main()

        self.assertEqual(ctx.exception.code, 0)
        run_cli_mock.assert_called_once_with(["--scope", "visual", "--config", "configs/record.yaml"])

    def test_sdk_discover_cameras_bootstraps_repo_root_for_direct_script_execution(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "scripts" / "sdk_discover_cameras.py"
        repo_root = str(module_path.parents[1])
        original_sys_path = list(sys.path)

        try:
            sys.path[:] = [
                entry
                for entry in original_sys_path
                if Path(entry or ".").resolve() != Path(repo_root).resolve()
            ]
            sys.path.insert(0, str(module_path.parent))

            spec = spec_from_file_location("sdk_discover_cameras_direct", module_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = module_from_spec(spec)
            spec.loader.exec_module(module)

            self.assertEqual(Path(module.REPO_ROOT).resolve(), Path(repo_root).resolve())
            self.assertIs(module.run_cli, sdk_discover_cameras.run_cli)
        finally:
            sys.path[:] = original_sys_path


if __name__ == "__main__":
    unittest.main()
