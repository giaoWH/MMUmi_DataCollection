import unittest

from scripts.sdk_record import discard_startup_frames


class _DummySensor:
    def __init__(self, batches):
        self._batches = list(batches)

    def read_available_frames(self):
        if not self._batches:
            return []
        return self._batches.pop(0)


class _DummyRegistry:
    def __init__(self):
        self.sensors = {
            "camera": _DummySensor([[1, 2], [3], []]),
            "realsense": _DummySensor([[1], [2, 3], []]),
        }


class StartupDiscardTest(unittest.TestCase):
    def test_discard_startup_frames_drains_available_batches(self) -> None:
        registry = _DummyRegistry()

        discarded = discard_startup_frames(registry, discard_sec=0.01, poll_sleep_sec=0.0)

        self.assertEqual(discarded["camera"], 3)
        self.assertEqual(discarded["realsense"], 3)
