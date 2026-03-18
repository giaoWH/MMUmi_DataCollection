import multiprocessing as mp
import struct
import time
import numpy as np
from .serial_base import SerialBaseSensor

class FTSensor(SerialBaseSensor):
    CMD_START = bytes.fromhex("09 10 01 9A 00 01 02 02 00 CD CA")
    CMD_STOP = b'\xFF' * 55
    FRAME_HEADER = b'\x20\x4E'
    FRAME_LEN = 16

    def __init__(self, port='COM3', baudrate=115200, calibration_duration=3.0):
        # 数据: [Fx, Fy, Fz, Tx, Ty, Tz]
        super().__init__("FT_Sensor", port, baudrate, data_length=6)
        self.calibration_duration = calibration_duration
        self._calibration_start_time = None
        self._calibration_sum = np.zeros(6, dtype=np.float64)
        self._calibration_count = 0
        self._baseline = np.zeros(6, dtype=np.float64)
        self._calibration_finished = mp.Value("b", False)
        self._calibration_event = mp.Event()

    def _reset_process_state(self):
        self._calibration_finished.value = False
        self._calibration_event.clear()

    def _on_open(self):
        if self._ser:
            time.sleep(0.1)
            self._ser.reset_input_buffer()
            self._calibration_start_time = time.time()
            self._calibration_sum.fill(0.0)
            self._calibration_count = 0
            self._baseline.fill(0.0)
            self._calibration_finished.value = False
            self._calibration_event.clear()
            self._ser.write(self.CMD_START)

    def _on_close(self):
        if self._ser:
            self._ser.write(self.CMD_STOP)
            time.sleep(0.05)

    def _parse_protocol(self, buffer):
        last_valid_frame = None
        
        # 只要 buffer 够长，就一直解析，确保拿到最新的那一帧
        while len(buffer) >= self.FRAME_LEN:
            # 找帧头
            idx = buffer.find(self.FRAME_HEADER)
            if idx == -1:
                # 没找到，保留最后一个字节防误切，其余丢弃
                buffer = buffer[-1:] 
                break
            
            # 对齐帧头
            if idx > 0:
                buffer = buffer[idx:]
            
            if len(buffer) < self.FRAME_LEN:
                break

            # 提取 Payload
            payload = buffer[2:14]
            # 移除已处理数据
            buffer = buffer[self.FRAME_LEN:]

            try:
                # 解析: 6个 short
                vals = struct.unpack('<6h', payload)
                # 转换单位: Force / 100.0, Torque / 1000.0
                frame = np.array([
                    vals[0]/100.0, vals[1]/100.0, vals[2]/100.0,
                    vals[3]/1000.0, vals[4]/1000.0, vals[5]/1000.0
                ])
                if not self._calibration_finished.value:
                    if self._calibration_start_time is None:
                        self._calibration_start_time = time.time()

                    self._calibration_sum += frame
                    self._calibration_count += 1

                    elapsed = time.time() - self._calibration_start_time
                    if elapsed < self.calibration_duration:
                        continue

                    self._baseline = self._calibration_sum / max(self._calibration_count, 1)
                    self._calibration_finished.value = True
                    self._calibration_event.set()
                    print(
                        f"[{self.name}] 零点校准完成，基线: "
                        f"F={self._baseline[:3].round(3).tolist()} "
                        f"T={self._baseline[3:].round(4).tolist()}"
                    )

                last_valid_frame = frame - self._baseline
            except struct.error:
                pass
            
        return last_valid_frame, buffer

    def is_calibrated(self):
        return bool(self._calibration_finished.value)

    def wait_until_calibrated(self, timeout=None):
        return self._calibration_event.wait(timeout=timeout)
