import serial
import time
import abc
from .base_sensor import BaseSensor

class SerialBaseSensor(BaseSensor):
    """
    串口传感器基类，封装了 buffer 管理和基础读取逻辑。
    """
    def __init__(self, name, port, baudrate, data_length):
        super().__init__(name)
        self.port = port
        self.baudrate = baudrate
        self.data_length = data_length
        self._ser = None
        self._buffer = bytearray() # 数据缓冲区

    def _worker(self):
        try:
            # timeout=0 实现非阻塞读取，这对高频采集很重要
            self._ser = serial.Serial(self.port, self.baudrate, timeout=0)
            self._on_open() # 钩子：发送启动指令等
        except Exception as e:
            print(f"[{self.name}] 串口打开失败: {e}")
            self.running = False
            self._close_hardware()
            return

        while self.running:
            try:
                if self._ser.in_waiting:
                    # 1. 记录物理信号到达时刻 (Time of Arrival)
                    arrival_time = time.time()
                    
                    # 2. 读取所有数据并追加到缓冲区
                    data_raw = self._ser.read(self._ser.in_waiting)
                    self._buffer.extend(data_raw)
                    
                    # 3. 解析协议 (由子类实现具体逻辑)
                    # _parse_protocol 负责从 buffer 中切分出完整帧
                    # 并返回 (最新帧, 剩余buffer)
                    new_frame, remaining_buf = self._parse_protocol(self._buffer)
                    self._buffer = remaining_buf
                    
                    # 4. 如果解析出了新帧，更新状态
                    if new_frame is not None:
                        with self.lock:
                            self.latest_data = new_frame
                            self.latest_timestamp = arrival_time
                            self.frame_count += 1
                else:
                    # 极短睡眠避免死循环占用 100% CPU
                    time.sleep(0.0001) 
            except Exception as e:
                print(f"[{self.name}] 运行时错误: {e}")
                time.sleep(0.1)

    def _close_hardware(self):
        if self._ser and self._ser.is_open:
            try:
                self._on_close()
            except: pass
            self._ser.close()

    # --- 虚函数钩子 ---
    def _on_open(self): pass
    def _on_close(self): pass
    
    @abc.abstractmethod
    def _parse_protocol(self, buffer):
        """
        输入: 当前积累的 bytearray
        输出: (解析出的最新一帧numpy数组, 剩余未处理的buffer)
        如果数据不足一帧，返回 (None, buffer)
        """
        return None, buffer
