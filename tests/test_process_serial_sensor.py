import time
import unittest
from unittest import mock

import numpy as np

from sensors.common.serial_base import SerialBaseSensor


class _DummySerial:
    def __init__(self, port, baudrate, timeout=0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.is_open = True
        self._payloads = [b"frame-001"]

    @property
    def in_waiting(self):
        if self._payloads:
            return len(self._payloads[0])
        return 0

    def read(self, size):
        if not self._payloads:
            return b""
        return self._payloads.pop(0)

    def close(self):
        self.is_open = False


class _DummyProcessSerialSensor(SerialBaseSensor):
    def __init__(self):
        super().__init__("DummySerial", "/dev/null", 115200, data_length=1)

    def _parse_protocol(self, buffer):
        if not buffer:
            return None, buffer
        payload = np.array([float(len(buffer))], dtype=np.float64)
        return payload, bytearray()


class ProcessSerialSensorTest(unittest.TestCase):
    def test_serial_sensor_runs_in_process_and_publishes_latest_packet(self):
        sensor = _DummyProcessSerialSensor()

        with mock.patch("sensors.common.serial_base.serial.Serial", _DummySerial):
            sensor.start()
            try:
                self.assertTrue(sensor.running)
                deadline = time.time() + 2.0
                data = None
                timestamp = 0.0
                frame_id = 0
                time_info = {}
                while time.time() < deadline:
                    data, timestamp, frame_id, time_info = sensor.get_data_with_time_info()
                    if data is not None:
                        break
                    time.sleep(0.01)

                self.assertIsNotNone(data)
                self.assertEqual(frame_id, 1)
                self.assertGreater(timestamp, 0.0)
                self.assertIn("host_capture_time_ns", time_info)
                self.assertIn("host_read_start_time_ns", time_info)
                self.assertIn("host_read_end_time_ns", time_info)

                runtime_status = sensor.get_runtime_status()
                self.assertTrue(runtime_status["running"])
                self.assertEqual(runtime_status["frame_count"], 1)
                np.testing.assert_array_equal(data, np.array([9.0], dtype=np.float64))
            finally:
                sensor.stop()

        self.assertFalse(sensor.running)
        self.assertIsNone(sensor.process)

    def test_serial_sensor_drops_stale_packets_and_keeps_latest_frame(self):
        class _BurstDummySerial(_DummySerial):
            def __init__(self, port, baudrate, timeout=0):
                super().__init__(port, baudrate, timeout=timeout)
                self._payloads = [b"a", b"abcd", b"abcdefgh"]

        sensor = _DummyProcessSerialSensor()

        with mock.patch("sensors.common.serial_base.serial.Serial", _BurstDummySerial):
            sensor.start()
            try:
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    runtime_status = sensor.get_runtime_status()
                    if runtime_status["frame_count"] >= 3:
                        break
                    time.sleep(0.01)

                data, _timestamp, frame_id, _time_info = sensor.get_data_with_time_info()
                self.assertIsNotNone(data)
                self.assertEqual(frame_id, 3)
                np.testing.assert_array_equal(data, np.array([8.0], dtype=np.float64))
            finally:
                sensor.stop()

    def test_serial_sensor_exposes_all_queued_packets(self):
        class _BurstDummySerial(_DummySerial):
            def __init__(self, port, baudrate, timeout=0):
                super().__init__(port, baudrate, timeout=timeout)
                self._payloads = [b"a", b"abcd", b"abcdefgh"]

        sensor = _DummyProcessSerialSensor()

        with mock.patch("sensors.common.serial_base.serial.Serial", _BurstDummySerial):
            sensor.start()
            try:
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    runtime_status = sensor.get_runtime_status()
                    if runtime_status["frame_count"] >= 3:
                        break
                    time.sleep(0.01)

                packets = sensor.get_all_data_with_time_info()
                self.assertEqual(len(packets), 3)
                self.assertEqual([frame_id for _data, _ts, frame_id, _info in packets], [1, 2, 3])
                np.testing.assert_array_equal(packets[0][0], np.array([1.0], dtype=np.float64))
                np.testing.assert_array_equal(packets[-1][0], np.array([8.0], dtype=np.float64))

                runtime_status = sensor.get_runtime_status()
                self.assertEqual(runtime_status["delivered_frame_count"], 3)
                self.assertEqual(runtime_status["dropped_frame_count"], 0)
            finally:
                sensor.stop()
