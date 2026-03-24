import struct
import time
import unittest
from unittest import mock

import numpy as np

from sdk.core import SystemClock
from sdk.sensors.legacy import (
    FTSensorConfig,
    IMUSensorConfig,
    LegacyFTAdapter,
    LegacyIMUAdapter,
    LegacyMotorsAdapter,
    MotorsSensorConfig,
)


def _with_checksum(packet_without_checksum: bytes) -> bytes:
    return packet_without_checksum + bytes([sum(packet_without_checksum) & 0xFF])


def _make_imu_packet(func: int, payload: bytes) -> bytes:
    packet_len = 2 + 1 + 1 + len(payload) + 1
    header = bytes([0x7E, 0x23, packet_len, func])
    return _with_checksum(header + payload)


def _make_ft_frame(force_values, torque_values) -> bytes:
    payload = struct.pack(
        "<6h",
        int(force_values[0] * 100.0),
        int(force_values[1] * 100.0),
        int(force_values[2] * 100.0),
        int(torque_values[0] * 1000.0),
        int(torque_values[1] * 1000.0),
        int(torque_values[2] * 1000.0),
    )
    return b"\x20\x4E" + payload + b"\x00\x00"


class _MappedDummySerial:
    PAYLOADS = {}

    def __init__(self, port, baudrate, timeout=0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.is_open = True
        self._payloads = list(self.PAYLOADS.get(port, []))

    @property
    def in_waiting(self):
        if self._payloads:
            return len(self._payloads[0])
        return 0

    def read(self, size):
        if not self._payloads:
            return b""
        return self._payloads.pop(0)

    def reset_input_buffer(self):
        return None

    def write(self, payload):
        return len(payload)

    def close(self):
        self.is_open = False


class LegacySerialAdaptersTest(unittest.TestCase):
    def _wait_for_frame(self, adapter, timeout=2.0):
        deadline = time.time() + timeout
        frame = None
        while time.time() < deadline:
            frame = adapter.read_frame()
            if frame is not None:
                return frame
            time.sleep(0.01)
        self.fail(f"未在 {timeout} 秒内收到帧: {adapter.name}")

    def _wait_for_frame_count(self, adapter, minimum_count, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = adapter.get_status()
            if status["frame_count"] >= minimum_count:
                return status
            time.sleep(0.01)
        self.fail(f"未在 {timeout} 秒内等到 frame_count >= {minimum_count}: {adapter.name}")

    def _wait_for_frame_matching(self, adapter, predicate, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            frame = adapter.read_frame()
            if frame is not None and predicate(frame):
                return frame
            time.sleep(0.01)
        self.fail(f"未在 {timeout} 秒内等到匹配帧: {adapter.name}")

    def test_legacy_ft_adapter_reports_ready_and_returns_compensated_frame(self):
        _MappedDummySerial.PAYLOADS = {
            "FT_PORT": [
                _make_ft_frame([1.0, 2.0, 3.0], [0.1, 0.2, 0.3])
                + _make_ft_frame([1.5, 2.5, 3.5], [0.2, 0.4, 0.6]),
            ]
        }
        adapter = LegacyFTAdapter(
            FTSensorConfig(port="FT_PORT", calibration_duration=0.0),
            SystemClock(),
        )

        with mock.patch("sensors.common.serial_base.serial.Serial", _MappedDummySerial):
            adapter.start()
            try:
                self.assertTrue(adapter.wait_until_ready(timeout=2.0))
                frame = self._wait_for_frame(adapter)
                status = adapter.get_status()
            finally:
                adapter.stop()

        self.assertTrue(status["calibration_finished"])
        self.assertEqual(frame.modality, "force_torque")
        np.testing.assert_allclose(frame.payload["force"], [0.5, 0.5, 0.5], atol=1e-6)
        np.testing.assert_allclose(frame.payload["torque"], [0.1, 0.2, 0.3], atol=1e-6)
        self.assertNotIn("force_torque", frame.payload)
        self.assertIsNotNone(frame.time.host_time_ns)
        self.assertIsNotNone(frame.time.host_read_end_time_ns)

    def test_legacy_imu_adapter_returns_full_frame_from_process_sensor(self):
        acc_payload = struct.pack("<hhhhhhhhh", 0, 0, 0, 0, 0, 0, 0, 0, 0)
        quat_payload = struct.pack("<ffff", 1.0, 0.0, 0.0, 0.0)
        imu_stream = (
            _make_imu_packet(0x04, acc_payload)
            + _make_imu_packet(0x16, quat_payload)
            + _make_imu_packet(0x26, b"")
        )
        _MappedDummySerial.PAYLOADS = {"IMU_PORT": [imu_stream]}
        adapter = LegacyIMUAdapter(IMUSensorConfig(port="IMU_PORT"), SystemClock())

        with mock.patch("sensors.common.serial_base.serial.Serial", _MappedDummySerial):
            adapter.start()
            try:
                frame = self._wait_for_frame(adapter)
                status = adapter.get_status()
            finally:
                adapter.stop()

        self.assertTrue(status["running"] or status["frame_count"] >= 1)
        self.assertEqual(frame.modality, "imu")
        np.testing.assert_allclose(frame.payload["acceleration"], [0.0, 0.0, 0.0], atol=1e-6)
        np.testing.assert_allclose(frame.payload["angular_velocity"], [0.0, 0.0, 0.0], atol=1e-6)
        np.testing.assert_allclose(frame.payload["quaternion"], [1.0, 0.0, 0.0, 0.0], atol=1e-6)

    def test_legacy_motors_adapter_reports_ready_and_returns_zeroed_delta(self):
        _MappedDummySerial.PAYLOADS = {
            "MOTOR_PORT": [
                b"M1: P: 1 V: 2 T: 3 | M2: P: 4 V: 5 T: 6\n"
                b"M1: P: 2 V: 4 T: 6 | M2: P: 8 V: 10 T: 12\n",
            ]
        }
        adapter = LegacyMotorsAdapter(
            MotorsSensorConfig(port="MOTOR_PORT", calibration_duration=0.0),
            SystemClock(),
        )

        with mock.patch("sensors.common.serial_base.serial.Serial", _MappedDummySerial):
            adapter.start()
            try:
                self.assertTrue(adapter.wait_until_ready(timeout=2.0))
                frame = self._wait_for_frame(adapter)
                status = adapter.get_status()
            finally:
                adapter.stop()

        self.assertTrue(status["calibration_finished"])
        self.assertEqual(frame.modality, "motor_state")
        self.assertEqual(frame.payload["motor_1"]["position"], 1.0)
        self.assertEqual(frame.payload["motor_2"]["torque"], 6.0)
        self.assertNotIn("motor_state", frame.payload)
