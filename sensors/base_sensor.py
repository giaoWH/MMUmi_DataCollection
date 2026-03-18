import abc
import threading
import numpy as np


class BaseSensor(abc.ABC):
    """
    所有传感器的抽象基类。
    规定了必须有 start, stop, get_data 方法。
    """
    def __init__(self, name):
        self.name = name
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        
        # 标准输出格式：(数据, 硬件到达时间戳, 帧计数)
        self.latest_data = None
        self.latest_timestamp = 0.0
        self.frame_count = 0

    def start(self):
        """启动采集线程"""
        if self.running: return
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        print(f"[{self.name}] 线程已启动")

    def stop(self):
        """停止采集并释放资源"""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self._close_hardware()
        print(f"[{self.name}] 已停止")

    def get_data(self):
        """
        线程安全地获取最新一帧数据。
        Return: (data, timestamp, frame_id)
        """
        with self.lock:
            # 返回数据的副本，防止外部修改影响内部
            data = self.latest_data.copy() if self.latest_data is not None else None
            return data, self.latest_timestamp, self.frame_count

    def is_calibrated(self):
        """
        默认认为无需校准，子类可覆盖。
        """
        return True

    def wait_until_calibrated(self, timeout=None):
        """
        默认无需等待，子类可覆盖为阻塞等待。
        """
        return True

    @property
    def calibration_finished(self):
        return self.is_calibrated()

    @abc.abstractmethod
    def _worker(self):
        """子类必须实现的线程主循环"""
        pass

    @abc.abstractmethod
    def _close_hardware(self):
        """子类必须实现的硬件关闭逻辑"""
        pass
