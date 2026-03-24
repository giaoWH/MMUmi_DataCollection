import unittest

import numpy as np

from sdk.core import SystemClock
from sdk.sensors.legacy import CameraSensorConfig, LegacyCameraAdapter


class _DummyQueuedCameraSensor:
    def __init__(self) -> None:
        self.running = True
        self.frame_count = 3
        self.latest_timestamp = 1.3
        self._packets = [
            (
                np.full((2, 2, 3), 1, dtype=np.uint8),
                1.1,
                1,
                {
                    "host_capture_time_ns": 1_100_000_000,
                    "monotonic_capture_time_ns": 2_100_000_000,
                    "host_arrival_time_ns": 1_100_100_000,
                    "host_read_start_time_ns": 1_100_050_000,
                    "host_read_end_time_ns": 1_100_150_000,
                },
            ),
            (
                np.full((2, 2, 3), 2, dtype=np.uint8),
                1.2,
                2,
                {
                    "host_capture_time_ns": 1_200_000_000,
                    "monotonic_capture_time_ns": 2_200_000_000,
                    "host_arrival_time_ns": 1_200_100_000,
                    "host_read_start_time_ns": 1_200_050_000,
                    "host_read_end_time_ns": 1_200_150_000,
                },
            ),
            (
                np.full((2, 2, 3), 3, dtype=np.uint8),
                1.3,
                3,
                {
                    "host_capture_time_ns": 1_300_000_000,
                    "monotonic_capture_time_ns": 2_300_000_000,
                    "host_arrival_time_ns": 1_300_100_000,
                    "host_read_start_time_ns": 1_300_050_000,
                    "host_read_end_time_ns": 1_300_150_000,
                },
            ),
        ]

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self.running = False

    def get_next_data_with_time_info(self):
        if not self._packets:
            return None, 0.0, 0, {}
        return self._packets.pop(0)

    def get_all_data_with_time_info(self):
        packets = list(self._packets)
        self._packets.clear()
        return packets

    def get_runtime_status(self):
        return {
            "running": self.running,
            "frame_count": self.frame_count,
            "produced_frame_count": self.frame_count,
            "delivered_frame_count": 0,
            "dropped_frame_count": 1,
            "latest_timestamp": self.latest_timestamp,
            "queue_depth": len(self._packets),
            "max_queue_depth": 8,
            "queue_capacity": 128,
        }


class VisualCaptureQueueTest(unittest.TestCase):
    def test_legacy_camera_adapter_drains_all_available_frames(self) -> None:
        adapter = LegacyCameraAdapter(
            CameraSensorConfig(name="camera", width=2, height=2, frame_queue_size=128),
            SystemClock(),
        )
        adapter.sensor = _DummyQueuedCameraSensor()

        frames = adapter.read_available_frames()

        self.assertEqual([frame.frame_id for frame in frames], [1, 2, 3])
        self.assertEqual(frames[0].time.host_time_ns, 1_100_000_000)
        self.assertEqual(frames[-1].time.monotonic_time_ns, 2_300_000_000)
        self.assertEqual(frames[1].metadata["frame_queue_size"], 128)
        np.testing.assert_array_equal(
            frames[2].payload["color"],
            np.full((2, 2, 3), 3, dtype=np.uint8),
        )

    def test_legacy_camera_adapter_status_exposes_queue_diagnostics(self) -> None:
        adapter = LegacyCameraAdapter(
            CameraSensorConfig(name="camera", frame_queue_size=128),
            SystemClock(),
        )
        adapter.sensor = _DummyQueuedCameraSensor()

        status = adapter.get_status()

        self.assertEqual(status["produced_frame_count"], 3)
        self.assertEqual(status["dropped_frame_count"], 1)
        self.assertEqual(status["queue_depth"], 3)
        self.assertEqual(status["queue_capacity"], 128)
