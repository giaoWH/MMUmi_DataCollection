import abc
from collections.abc import Iterable
import multiprocessing as mp
import queue
import time
import traceback

import serial

from .base_sensor import BaseSensor


class SerialBaseSensor(BaseSensor):
    """
    串口传感器基类，封装了 buffer 管理和基础读取逻辑。
    """

    def __init__(self, name, port, baudrate, data_length, frame_queue_size=1024):
        super().__init__(name)
        self.port = port
        self.baudrate = baudrate
        self.data_length = data_length
        self.frame_queue_size = max(1, int(frame_queue_size))
        self.process = None
        self._ser = None
        self._buffer = bytearray()
        self._requested_running = False
        self._run_event = mp.Event()
        self._worker_running = mp.Value("b", False)
        self._shared_frame_count = mp.Value("q", 0)
        self._shared_latest_timestamp = mp.Value("d", 0.0)
        self._latest_packet_queue = mp.Queue(maxsize=1)
        self._frame_packet_queue = mp.Queue(maxsize=self.frame_queue_size)
        self._shared_dropped_frame_count = mp.Value("q", 0)
        self._shared_max_queue_depth = mp.Value("q", 0)
        self._delivered_frame_count = 0

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

    def get_next_data_with_time_info(self):
        packet = self._pop_next_packet()
        if packet is None:
            return None, 0.0, 0, {}
        return (
            self._copy_packet_data(packet["data"]),
            packet["timestamp"],
            packet["frame_id"],
            dict(packet["time_info"]),
        )

    def get_all_data_with_time_info(self):
        packets = []
        while True:
            packet = self._pop_next_packet()
            if packet is None:
                break
            packets.append(
                (
                    self._copy_packet_data(packet["data"]),
                    packet["timestamp"],
                    packet["frame_id"],
                    dict(packet["time_info"]),
                )
            )
        return packets

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

                    for frame in self._normalize_parsed_frames(new_frame):
                        capture_wall_ns = (read_start_wall_ns + read_end_wall_ns) // 2
                        capture_mono_ns = (read_start_mono_ns + read_end_mono_ns) // 2
                        frame_count = self._shared_frame_count.value + 1
                        self._shared_frame_count.value = frame_count
                        self._shared_latest_timestamp.value = capture_wall_ns / 1_000_000_000.0
                        packet = {
                            "data": frame,
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
                        self._publish_frame_packet(packet)
                else:
                    time.sleep(0.0001)
            except Exception as e:
                if not self._run_event.is_set():
                    break
                print(f"[{self.name}] 运行时错误: {type(e).__name__}: {e!r}")
                traceback.print_exc()
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
        # This queue is only used as a "latest packet" handoff cache.
        # If the consumer lags behind, it is acceptable to drop the stale
        # packet and keep only the newest one.
        for _ in range(3):
            try:
                self._latest_packet_queue.put_nowait(packet)
                return
            except queue.Full:
                try:
                    self._latest_packet_queue.get_nowait()
                except queue.Empty:
                    pass
                time.sleep(0.0005)
        # If the queue is still reported as full after retries, drop this
        # packet silently. The next loop iteration will publish a newer one.
        return

    def _publish_frame_packet(self, packet):
        for _ in range(3):
            try:
                self._frame_packet_queue.put_nowait(packet)
                self._update_max_queue_depth()
                return
            except queue.Full:
                self._shared_dropped_frame_count.value += 1
                try:
                    self._frame_packet_queue.get_nowait()
                except queue.Empty:
                    pass
                time.sleep(0.0005)
        return

    def _pop_next_packet(self):
        try:
            packet = self._frame_packet_queue.get_nowait()
        except queue.Empty:
            return None
        self._delivered_frame_count += 1
        self.latest_data = packet["data"]
        self.latest_timestamp = packet["timestamp"]
        self.frame_count = packet["frame_id"]
        self.latest_time_info = packet["time_info"]
        return packet

    def _update_max_queue_depth(self):
        try:
            depth = self._frame_packet_queue.qsize()
        except (NotImplementedError, OSError):
            return
        if depth > self._shared_max_queue_depth.value:
            self._shared_max_queue_depth.value = depth

    def _copy_packet_data(self, value):
        if value is None:
            return None
        if hasattr(value, "copy"):
            return value.copy()
        return value

    def _normalize_parsed_frames(self, value) -> list:
        if value is None:
            return []
        if isinstance(value, Iterable) and not hasattr(value, "shape") and not isinstance(value, (bytes, bytearray, dict)):
            return [item for item in value if item is not None]
        return [value]

    def _reset_shared_runtime_state(self):
        self.latest_data = None
        self.latest_timestamp = 0.0
        self.frame_count = 0
        self.latest_time_info = {}
        self._delivered_frame_count = 0
        self._worker_running.value = False
        self._shared_frame_count.value = 0
        self._shared_latest_timestamp.value = 0.0
        self._shared_dropped_frame_count.value = 0
        self._shared_max_queue_depth.value = 0
        while True:
            try:
                self._latest_packet_queue.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                self._frame_packet_queue.get_nowait()
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
            "produced_frame_count": self._shared_frame_count.value,
            "delivered_frame_count": self._delivered_frame_count,
            "dropped_frame_count": self._shared_dropped_frame_count.value,
            "latest_timestamp": self._shared_latest_timestamp.value,
            "queue_depth": self._safe_queue_depth(),
            "max_queue_depth": self._shared_max_queue_depth.value,
            "queue_capacity": self.frame_queue_size,
        }

    def _safe_queue_depth(self):
        try:
            return self._frame_packet_queue.qsize()
        except (NotImplementedError, OSError):
            return 0

    def _on_open(self):
        pass

    def _on_close(self):
        pass

    @abc.abstractmethod
    def _parse_protocol(self, buffer):
        """
        输入: 当前积累的 bytearray
        输出: (解析出的一帧或多帧, 剩余未处理的buffer)
        如果数据不足一帧，返回 (None, buffer)
        """
        return None, buffer
