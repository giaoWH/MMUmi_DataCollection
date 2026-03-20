import abc
import multiprocessing as mp
import queue
import time

import serial

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
        self.process = None
        self._ser = None
        self._buffer = bytearray()
        self._requested_running = False
        self._run_event = mp.Event()
        self._worker_running = mp.Value("b", False)
        self._shared_frame_count = mp.Value("q", 0)
        self._shared_latest_timestamp = mp.Value("d", 0.0)
        self._latest_packet_queue = mp.Queue(maxsize=1)

    @property
    def running(self):
        if self.process is not None and self.process.is_alive() and self._run_event.is_set():
            return True
        return bool(self._worker_running.value)

    @running.setter
    def running(self, value):
        self._requested_running = bool(value)

    def start(self):
        """启动采集进程"""
        if self.process and self.process.is_alive():
            return

        self._reset_shared_runtime_state()
        self._reset_process_state()
        self.running = True
        self._run_event.set()
        self.process = mp.Process(target=self._worker, daemon=True)
        self.process.start()
        print(f"[{self.name}] 进程已启动")

    def stop(self):
        """停止采集进程并释放资源"""
        self.running = False
        self._run_event.clear()
        if self.process and self.process.is_alive():
            self.process.join(timeout=2.0)
        if self.process and self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=1.0)
        self.process = None
        self._worker_running.value = False
        print(f"[{self.name}] 已停止")

    def get_data(self):
        self._drain_latest_packet()
        return self.latest_data_copy(), self.latest_timestamp, self.frame_count

    def get_data_with_time_info(self):
        self._drain_latest_packet()
        return (
            self.latest_data_copy(),
            self.latest_timestamp,
            self.frame_count,
            dict(self.latest_time_info),
        )

    def latest_data_copy(self):
        if self.latest_data is None:
            return None
        return self.latest_data.copy()

    def _worker(self):
        try:
            self._ser = serial.Serial(self.port, self.baudrate, timeout=0)
            self._on_open()
            self._worker_running.value = True
        except Exception as e:
            print(f"[{self.name}] 串口打开失败: {e}")
            self._worker_running.value = False
            self._close_hardware()
            return

        while self._run_event.is_set():
            try:
                if self._ser.in_waiting:
                    read_start_wall_ns = time.time_ns()
                    read_start_mono_ns = time.perf_counter_ns()
                    data_raw = self._ser.read(self._ser.in_waiting)
                    read_end_wall_ns = time.time_ns()
                    read_end_mono_ns = time.perf_counter_ns()
                    self._buffer.extend(data_raw)

                    new_frame, remaining_buf = self._parse_protocol(self._buffer)
                    self._buffer = remaining_buf

                    if new_frame is not None:
                        capture_wall_ns = (read_start_wall_ns + read_end_wall_ns) // 2
                        capture_mono_ns = (read_start_mono_ns + read_end_mono_ns) // 2
                        frame_count = self._shared_frame_count.value + 1
                        self._shared_frame_count.value = frame_count
                        self._shared_latest_timestamp.value = capture_wall_ns / 1_000_000_000.0
                        packet = {
                            "data": new_frame,
                            "timestamp": capture_wall_ns / 1_000_000_000.0,
                            "frame_id": frame_count,
                            "time_info": {
                                "host_capture_time_ns": capture_wall_ns,
                                "monotonic_capture_time_ns": capture_mono_ns,
                                "host_arrival_time_ns": read_start_wall_ns,
                                "host_read_start_time_ns": read_start_wall_ns,
                                "host_read_end_time_ns": read_end_wall_ns,
                            },
                        }
                        self._publish_latest_packet(packet)
                else:
                    time.sleep(0.0001)
            except Exception as e:
                print(f"[{self.name}] 运行时错误: {e}")
                time.sleep(0.1)

        self._worker_running.value = False
        self._close_hardware()

    def _close_hardware(self):
        if self._ser and self._ser.is_open:
            try:
                self._on_close()
            except Exception:
                pass
            self._ser.close()
        self._ser = None

    def _drain_latest_packet(self):
        latest_packet = None
        while True:
            try:
                latest_packet = self._latest_packet_queue.get_nowait()
            except queue.Empty:
                break

        if latest_packet is None:
            self.frame_count = self._shared_frame_count.value
            self.latest_timestamp = self._shared_latest_timestamp.value
            return

        self.latest_data = latest_packet["data"]
        self.latest_timestamp = latest_packet["timestamp"]
        self.frame_count = latest_packet["frame_id"]
        self.latest_time_info = latest_packet["time_info"]

    def _publish_latest_packet(self, packet):
        try:
            self._latest_packet_queue.put_nowait(packet)
        except queue.Full:
            try:
                self._latest_packet_queue.get_nowait()
            except queue.Empty:
                pass
            self._latest_packet_queue.put_nowait(packet)

    def _reset_shared_runtime_state(self):
        self.latest_data = None
        self.latest_timestamp = 0.0
        self.frame_count = 0
        self.latest_time_info = {}
        self._worker_running.value = False
        self._shared_frame_count.value = 0
        self._shared_latest_timestamp.value = 0.0
        while True:
            try:
                self._latest_packet_queue.get_nowait()
            except queue.Empty:
                break

    def _reset_process_state(self):
        pass

    def get_runtime_status(self):
        self._drain_latest_packet()
        process_alive = self.process is not None and self.process.is_alive()
        worker_running = bool(self._worker_running.value)
        running = bool(self.running)
        return {
            "running": running,
            "process_alive": process_alive,
            "worker_running": worker_running,
            "requested_running": bool(self._requested_running),
            "frame_count": self._shared_frame_count.value,
            "latest_timestamp": self._shared_latest_timestamp.value,
        }

    def _on_open(self):
        pass

    def _on_close(self):
        pass

    @abc.abstractmethod
    def _parse_protocol(self, buffer):
        """
        输入: 当前积累的 bytearray
        输出: (解析出的最新一帧numpy数组, 剩余未处理的buffer)
        如果数据不足一帧，返回 (None, buffer)
        """
        return None, buffer
