import time
import threading
import re
import numpy as np
from .serial_base import SerialBaseSensor


class MotorsSensor(SerialBaseSensor):
    """
    电机串口传感器。

    输出格式:
    [M1_P, M1_V, M1_T, M2_P, M2_V, M2_T]
    """

    LINE_PATTERN = re.compile(
        rb"M1:\s*P:\s*([-\d.]+)\s*V:\s*([-\d.]+)\s*T:\s*([-\d.]+)\s*\|\s*"
        rb"M2:\s*P:\s*([-\d.]+)\s*V:\s*([-\d.]+)\s*T:\s*([-\d.]+)"
    )

    def __init__(self, port="/dev/ttyUSB0", baudrate=115200, calibration_duration=3.0):
        super().__init__("Motors", port, baudrate, data_length=6)
        self.calibration_duration = calibration_duration
        self._calibration_start_time = None
        self._calibration_sum = np.zeros(6, dtype=np.float64)
        self._calibration_count = 0
        self._baseline = np.zeros(6, dtype=np.float64)
        self._calibration_finished = False
        self._calibration_event = threading.Event()

    def _on_open(self):
        if self._ser:
            self._ser.reset_input_buffer()
            self._calibration_start_time = time.time()
            self._calibration_sum.fill(0.0)
            self._calibration_count = 0
            self._baseline.fill(0.0)
            self._calibration_finished = False
            self._calibration_event.clear()

    def _parse_protocol(self, buffer):
        latest_frame = None

        while True:
            newline_idx = buffer.find(b"\n")
            if newline_idx == -1:
                break

            line = bytes(buffer[:newline_idx]).strip()
            buffer = buffer[newline_idx + 1:]

            if not line:
                continue

            match = self.LINE_PATTERN.search(line)
            if not match:
                continue

            try:
                frame = np.array(
                    [float(value.decode("ascii")) for value in match.groups()],
                    dtype=np.float64,
                )
                if not self._calibration_finished:
                    if self._calibration_start_time is None:
                        self._calibration_start_time = time.time()

                    self._calibration_sum += frame
                    self._calibration_count += 1

                    elapsed = time.time() - self._calibration_start_time
                    if elapsed < self.calibration_duration:
                        continue

                    self._baseline = self._calibration_sum / max(self._calibration_count, 1)
                    self._calibration_finished = True
                    self._calibration_event.set()
                    print(
                        f"[{self.name}] 零点校准完成，基线: "
                        f"{self._baseline.round(4).tolist()}"
                    )

                latest_frame = frame - self._baseline
            except ValueError:
                continue

        return latest_frame, buffer

    def is_calibrated(self):
        return self._calibration_finished

    def wait_until_calibrated(self, timeout=None):
        return self._calibration_event.wait(timeout=timeout)
