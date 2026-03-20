import tempfile
import unittest
from pathlib import Path

from scripts.sdk_discover_ports import (
    build_port_discovery_plan,
    detect_new_ports,
    write_config_payload,
)
from sdk.config import load_config_file


class PortDiscoveryTest(unittest.TestCase):
    def test_build_port_discovery_plan_uses_enabled_order_and_skips_non_serial(self) -> None:
        payload = {
            "output_root": "sessions",
            "enable_ft": True,
            "enable_realsense": True,
            "enable_motors": True,
            "enable_camera": True,
            "enable_imu": False,
        }

        steps, skipped = build_port_discovery_plan(payload)

        self.assertEqual([step.sensor_key for step in steps], ["ft", "motors"])
        self.assertEqual(skipped, ["realsense", "camera"])

    def test_detect_new_ports_returns_new_devices_only(self) -> None:
        previous = {"COM3": "FT", "COM4": "IMU"}
        current = {"COM3": "FT", "COM4": "IMU", "COM7": "USB Serial"}

        self.assertEqual(detect_new_ports(previous, current), ["COM7"])

    def test_write_config_payload_preserves_leading_comments_and_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "record.yaml"
            config_path.write_text(
                "# comment 1\n# comment 2\n\nenable_ft: true\nft:\n  port: COM3\n",
                encoding="utf-8",
            )
            payload = load_config_file(config_path)
            payload["ft"]["port"] = "COM8"

            backup_path = write_config_payload(config_path, payload, create_backup=True)

            self.assertIsNotNone(backup_path)
            self.assertTrue(backup_path.exists())
            content = config_path.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("# comment 1\n# comment 2\n"))
            self.assertIn("port: COM8", content)
