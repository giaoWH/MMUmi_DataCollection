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

    def __init__(self, port="/dev/ttyUSB0", baudrate=115200):
        super().__init__("Motors", port, baudrate, data_length=6)

    def _on_open(self):
        if self._ser:
            self._ser.reset_input_buffer()

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
                latest_frame = np.array(
                    [float(value.decode("ascii")) for value in match.groups()],
                    dtype=np.float64,
                )
            except ValueError:
                continue

        return latest_frame, buffer
