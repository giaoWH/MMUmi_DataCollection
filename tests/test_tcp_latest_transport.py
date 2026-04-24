import io
import unittest

import numpy as np

from sdk.core import FrameTime, SensorFrame
from sdk.transport import LatestFrameStore, encode_frame_message, recv_frame_message


def _frame(sensor_name: str, frame_id: int) -> SensorFrame:
    return SensorFrame(
        sensor_name=sensor_name,
        sensor_type="fake_camera_sensor",
        modality="rgb",
        frame_id=frame_id,
        time=FrameTime(host_time_ns=1_700_000_000_000_000_000 + frame_id),
        payload={"color": np.zeros((2, 3, 3), dtype=np.uint8) + frame_id},
        metadata={"source": "test"},
    )


class TcpLatestTransportTest(unittest.TestCase):
    def test_length_prefixed_pickle_round_trips_sensor_frame(self) -> None:
        original = _frame("camera", 7)

        decoded = recv_frame_message(io.BytesIO(encode_frame_message(original)))

        self.assertEqual(decoded.sensor_name, "camera")
        self.assertEqual(decoded.frame_id, 7)
        self.assertEqual(decoded.metadata["source"], "test")
        np.testing.assert_array_equal(decoded.payload["color"], original.payload["color"])

    def test_latest_frame_store_keeps_only_newest_per_sensor(self) -> None:
        store = LatestFrameStore()
        first_generation = store.update(_frame("camera", 1))
        second_generation = store.update(_frame("camera", 2))
        imu_generation = store.update(
            SensorFrame(
                sensor_name="imu",
                sensor_type="fake_imu_sensor",
                modality="imu",
                frame_id=4,
                time=FrameTime(host_time_ns=1),
                payload={"acceleration": [0.0, 0.0, 9.81]},
                metadata={},
            )
        )

        snapshot = store.snapshot()
        self.assertGreater(second_generation, first_generation)
        self.assertEqual(snapshot["camera"][1].frame_id, 2)
        self.assertEqual(snapshot["imu"][0], imu_generation)

        pending = store.snapshot_since({"camera": second_generation})
        self.assertEqual([(sensor_name, frame.frame_id) for sensor_name, _generation, frame in pending], [("imu", 4)])


if __name__ == "__main__":
    unittest.main()
