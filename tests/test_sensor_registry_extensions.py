import time
import unittest

from scripts.sdk_record import RecorderConfig, build_registry
from sdk.core import SystemClock


class SensorRegistryExtensionsTest(unittest.TestCase):
    def test_fake_registry_supports_all_sensor_types(self) -> None:
        config = RecorderConfig(
            sensor_source="fake",
            duration_sec=0.0,
            enable_ft=True,
            enable_imu=True,
            enable_realsense=True,
            enable_motors=True,
            enable_microphone=True,
            enable_camera=True,
        )
        registry = build_registry(config, SystemClock())

        try:
            registry.start_all()
            self.assertTrue(registry.wait_until_ready(timeout=1.0))

            deadline = time.time() + 1.0
            seen_frames: dict[str, object] = {}
            while time.time() < deadline and len(seen_frames) < len(registry.sensors):
                for sensor_name, sensor in registry.sensors.items():
                    frame = sensor.read_frame()
                    if frame is not None:
                        seen_frames[sensor_name] = frame
                time.sleep(0.01)

            self.assertEqual(set(seen_frames), set(registry.sensors))
            self.assertEqual(seen_frames["motors"].modality, "motor_state")
            self.assertEqual(seen_frames["microphone"].modality, "audio")
            self.assertEqual(seen_frames["camera"].modality, "rgb")
            self.assertIn("audio", seen_frames["microphone"].payload)
            self.assertIn("motor_state", seen_frames["motors"].payload)
            self.assertIn("color", seen_frames["camera"].payload)
        finally:
            registry.stop_all()
