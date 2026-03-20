import unittest
from pathlib import Path


class LegacyCleanupTest(unittest.TestCase):
    def test_sensors_module_keeps_legacy_surface_minimal(self) -> None:
        init_file = Path(__file__).resolve().parents[1] / "sensors" / "__init__.py"
        exports = init_file.read_text(encoding="utf-8")

        self.assertIn("FTSensor", exports)
        self.assertIn("IMUSensor", exports)
        self.assertNotIn(
            "CameraSensor",
            exports,
            "普通 RGB 相机通过 SDK 录制入口接入，这里保持 legacy sensors 包根入口的最小暴露面",
        )
