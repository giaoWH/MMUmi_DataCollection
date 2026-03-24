import time
import unittest

from scripts.sdk_record import RecorderConfig, build_registry
from sdk.core import SystemClock
from sdk.sensors.legacy import FTSensorConfig, MotorsSensorConfig


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
            enable_gelsight=True,
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
            self.assertEqual(seen_frames["gelsight"].modality, "visuotactile")
            self.assertIn("audio", seen_frames["microphone"].payload)
            self.assertIn("motor_1", seen_frames["motors"].payload)
            self.assertIn("motor_2", seen_frames["motors"].payload)
            self.assertIn("color", seen_frames["camera"].payload)
            self.assertIn("image", seen_frames["gelsight"].payload)
        finally:
            registry.stop_all()

    def test_fake_registry_applies_ft_and_motor_channel_switches(self) -> None:
        config = RecorderConfig(
            sensor_source="fake",
            duration_sec=0.0,
            enable_ft=True,
            enable_motors=True,
            enable_microphone=False,
            enable_camera=False,
            enable_gelsight=False,
            enable_imu=False,
            enable_realsense=False,
            ft=FTSensorConfig(enable_torque=False),
            motors=MotorsSensorConfig(enable_motor_1=False, enable_motor_2=True),
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

            self.assertIn("ft", seen_frames)
            self.assertIn("motors", seen_frames)
            self.assertIn("force", seen_frames["ft"].payload)
            self.assertNotIn("torque", seen_frames["ft"].payload)
            self.assertNotIn("motor_1", seen_frames["motors"].payload)
            self.assertIn("motor_2", seen_frames["motors"].payload)
        finally:
            registry.stop_all()
