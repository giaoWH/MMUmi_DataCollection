import unittest

from sdk.core.aligner import BufferedFrameAligner
from sdk.core.frame import FrameTime, SensorFrame


def make_frame(sensor_name: str, frame_id: int, host_time: float) -> SensorFrame:
    return SensorFrame(
        sensor_name=sensor_name,
        sensor_type=sensor_name,
        modality=sensor_name,
        frame_id=frame_id,
        time=FrameTime(host_time=host_time, monotonic_time=host_time),
        payload={"value": frame_id},
    )


class BufferedFrameAlignerTest(unittest.TestCase):
    def test_align_nearest_frame(self) -> None:
        aligner = BufferedFrameAligner(["ft", "imu"], max_frame_age=0.2)
        aligner.add_frame(make_frame("ft", 1, 10.0))
        aligner.add_frame(make_frame("imu", 5, 10.03))

        aligned = aligner.align(10.05)

        self.assertEqual(aligned.frames["ft"].frame_id, 1)
        self.assertEqual(aligned.frames["imu"].frame_id, 5)
        self.assertEqual(aligned.missing_sensors, [])
        self.assertEqual(aligned.metadata["present_sensors"], ["ft", "imu"])
        self.assertEqual(aligned.metadata["dropped_sensors"], [])
        self.assertAlmostEqual(aligned.metadata["age_stats"]["max_abs_age"], 0.05)

    def test_mark_sensor_missing_when_stale(self) -> None:
        aligner = BufferedFrameAligner(["ft"], max_frame_age=0.05)
        aligner.add_frame(make_frame("ft", 1, 10.0))

        aligned = aligner.align(10.2)

        self.assertEqual(aligned.frames, {})
        self.assertEqual(aligned.missing_sensors, ["ft"])
        self.assertEqual(aligned.metadata["present_sensors"], [])
        self.assertEqual(aligned.metadata["dropped_sensors"], ["ft"])
        self.assertEqual(aligned.metadata["age_stats"]["count"], 0)

    def test_align_uses_monotonic_nanoseconds_for_age_calculation(self) -> None:
        aligner = BufferedFrameAligner(["ft"], max_frame_age=0.1)
        aligner.add_frame(
            SensorFrame(
                sensor_name="ft",
                sensor_type="ft",
                modality="force_torque",
                frame_id=1,
                time=FrameTime(
                    host_time=1000.0,
                    monotonic_time=10.0,
                    host_time_ns=1_000_000_000_000,
                    monotonic_time_ns=10_000_000_000,
                ),
                payload={"value": 1},
            )
        )

        aligned = aligner.align(
            1000.5,
            aligned_time_ns=1_000_500_000_000,
            aligned_monotonic_time_ns=10_050_000_000,
        )

        self.assertEqual(aligned.missing_sensors, [])
        self.assertEqual(aligned.frames["ft"].frame_id, 1)
        self.assertEqual(aligned.metadata["age_by_sensor_ns"]["ft"], 50_000_000)
        self.assertAlmostEqual(aligned.age_by_sensor["ft"], 0.05)


if __name__ == "__main__":
    unittest.main()
