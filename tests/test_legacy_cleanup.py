import unittest
from pathlib import Path


class LegacyCleanupTest(unittest.TestCase):
    def test_sensors_module_no_longer_exports_camera_sensor(self) -> None:
        init_file = Path(__file__).resolve().parents[1] / "sensors" / "__init__.py"
        exports = init_file.read_text(encoding="utf-8")

        self.assertIn("FTSensor", exports)
        self.assertIn("IMUSensor", exports)
        self.assertNotIn("CameraSensor", exports, "新 SDK 已收口到 RealSense，不应继续暴露普通相机接口")
