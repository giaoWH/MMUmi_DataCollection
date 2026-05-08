import json
import tempfile
import unittest

import numpy as np

from sdk.core import FrameTime, SensorFrame, create_session_info
from sdk.storage import SessionWriter
from scripts.sdk_clean_ft_gravity_compensation import clean_session


class FTGravityCleaningTest(unittest.TestCase):
    def test_clean_session_writes_compensated_ft_simplified_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                task_name="offline_ft_clean",
                sensors={
                    "ft": {"sensor_type": "ft_sensor", "modality": "force_torque"},
                    "imu": {"sensor_type": "imu_sensor", "modality": "imu"},
                },
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)
            gravity = np.array([0.0, 0.0, -9.81], dtype=np.float64)
            bias = np.array([0.2, -0.1, 0.3], dtype=np.float64)
            external = np.array([1.0, 2.0, 3.0], dtype=np.float64)

            for frame_id in range(11):
                timestamp = 1.0 + frame_id * 0.1
                force = gravity + bias
                if frame_id == 10:
                    force = force + external
                writer.write_sensor_frame(
                    SensorFrame(
                        sensor_name="ft",
                        sensor_type="ft_sensor",
                        modality="force_torque",
                        frame_id=frame_id,
                        time=FrameTime(host_time=timestamp, monotonic_time=timestamp),
                        payload={
                            "force": force,
                            "torque": np.array([0.1, 0.2, 0.3], dtype=np.float64),
                        },
                    )
                )
                writer.write_sensor_frame(
                    SensorFrame(
                        sensor_name="imu",
                        sensor_type="imu_sensor",
                        modality="imu",
                        frame_id=frame_id,
                        time=FrameTime(host_time=timestamp, monotonic_time=timestamp),
                        payload={
                            "acceleration": [0.0, 0.0, 0.0],
                            "angular_velocity": [0.0, 0.0, 0.0],
                            "quaternion": [1.0, 0.0, 0.0, 0.0],
                        },
                    )
                )
            writer.close()

            summary = clean_session(
                session.output_dir,
                mass=1.0,
                calibration_duration_sec=0.9,
                minimum_samples=10,
                max_match_age_sec=0.01,
            )

            self.assertEqual(summary.written_frames, 11)
            self.assertEqual(summary.calibration_samples, 10)
            self.assertTrue(summary.output_path.exists())
            records = [
                json.loads(line)
                for line in summary.output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(records), 11)
            np.testing.assert_allclose(records[-1]["payload"]["force"], external, atol=1e-6)
            np.testing.assert_allclose(records[-1]["payload"]["torque"], [0.1, 0.2, 0.3], atol=1e-6)
            np.testing.assert_allclose(records[-1]["gravity_compensation"]["force_raw"], gravity + bias + external)
            np.testing.assert_allclose(records[-1]["gravity_compensation"]["bias"], bias, atol=1e-6)

    def test_clean_session_supports_legacy_force_torque_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                task_name="legacy_ft_clean",
                sensors={
                    "ft": {"sensor_type": "ft_sensor", "modality": "force_torque"},
                    "imu": {"sensor_type": "imu_sensor", "modality": "imu"},
                },
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)
            gravity = np.array([0.0, 0.0, -9.81], dtype=np.float64)
            external = np.array([0.5, 0.5, 0.5], dtype=np.float64)

            for frame_id in range(11):
                timestamp = 1.0 + frame_id * 0.1
                force = gravity if frame_id < 10 else gravity + external
                wrench = np.concatenate([force, np.array([0.4, 0.5, 0.6], dtype=np.float64)])
                writer.write_sensor_frame(
                    SensorFrame(
                        sensor_name="ft",
                        sensor_type="ft_sensor",
                        modality="force_torque",
                        frame_id=frame_id,
                        time=FrameTime(host_time=timestamp, monotonic_time=timestamp),
                        payload={"force_torque": wrench},
                    )
                )
                writer.write_sensor_frame(
                    SensorFrame(
                        sensor_name="imu",
                        sensor_type="imu_sensor",
                        modality="imu",
                        frame_id=frame_id,
                        time=FrameTime(host_time=timestamp, monotonic_time=timestamp),
                        payload={
                            "acceleration": [0.0, 0.0, 0.0],
                            "angular_velocity": [0.0, 0.0, 0.0],
                            "quaternion": [1.0, 0.0, 0.0, 0.0],
                        },
                    )
                )
            writer.close()

            summary = clean_session(
                session.output_dir,
                mass=1.0,
                calibration_duration_sec=0.9,
                minimum_samples=10,
                max_match_age_sec=0.01,
            )

            records = [
                json.loads(line)
                for line in summary.output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            np.testing.assert_allclose(records[-1]["payload"]["force"], external, atol=1e-6)
            np.testing.assert_allclose(records[-1]["payload"]["torque"], [0.4, 0.5, 0.6], atol=1e-6)


if __name__ == "__main__":
    unittest.main()
