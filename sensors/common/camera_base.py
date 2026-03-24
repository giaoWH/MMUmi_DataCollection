import time
from collections import deque
from typing import Any

import cv2
import numpy as np

from .base_sensor import BaseSensor


class CameraBaseSensor(BaseSensor):
    """
    基于 OpenCV VideoCapture 的通用相机基类。

    适用于普通 RGB 相机、GelSight 这类以 UVC/USB 摄像头方式接入的设备。
    """

    device_label = "相机"

    def __init__(
        self,
        name,
        device_index=0,
        width=640,
        height=480,
        fps=30,
        flip_vertical=False,
        frame_queue_size=128,
    ):
        super().__init__(name)
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.flip_vertical = flip_vertical
        self.frame_queue_size = max(1, int(frame_queue_size))
        self.data_length = width * height * 3
        self._cap = None
        self._frame_queue = deque()
        self._dropped_frame_count = 0
        self._delivered_frame_count = 0
        self._max_queue_depth = 0

    def start(self):
        with self.lock:
            self.latest_data = None
            self.latest_timestamp = 0.0
            self.frame_count = 0
            self.latest_time_info = {}
            self._frame_queue.clear()
            self._dropped_frame_count = 0
            self._delivered_frame_count = 0
            self._max_queue_depth = 0
        super().start()

    def _worker(self):
        try:
            self._cap = cv2.VideoCapture(self.device_index)
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self._cap.set(cv2.CAP_PROP_FPS, self.fps)
        except Exception as e:
            print(f"[{self.name}] {self.device_label}打开失败: {e}")
            self.running = False
            self._close_hardware()
            return

        if self._cap is None or not self._cap.isOpened():
            print(f"[{self.name}] 无法打开{self.device_label}设备 {self.device_index}")
            self.running = False
            self._close_hardware()
            return

        while self.running:
            try:
                read_start_wall_ns = time.time_ns()
                read_start_mono_ns = time.perf_counter_ns()
                ok, frame_bgr = self._cap.read()
                read_end_wall_ns = time.time_ns()
                read_end_mono_ns = time.perf_counter_ns()
                if not ok or frame_bgr is None:
                    time.sleep(0.01)
                    continue

                frame = self._process_frame(frame_bgr)
                capture_wall_ns = (read_start_wall_ns + read_end_wall_ns) // 2
                capture_mono_ns = (read_start_mono_ns + read_end_mono_ns) // 2

                self._publish_frame_packet(
                    frame,
                    capture_wall_ns=capture_wall_ns,
                    capture_mono_ns=capture_mono_ns,
                    read_start_wall_ns=read_start_wall_ns,
                    read_end_wall_ns=read_end_wall_ns,
                )
            except Exception as e:
                print(f"[{self.name}] 运行时错误: {e}")
                time.sleep(0.05)

    def get_next_data_with_time_info(self):
        with self.lock:
            if not self._frame_queue:
                return None, 0.0, 0, {}
            packet = self._frame_queue.popleft()
            self._delivered_frame_count += 1
            data = self._copy_frame_data(packet["data"])
            return data, packet["timestamp"], packet["frame_id"], dict(packet["time_info"])

    def get_all_data_with_time_info(self):
        packets = []
        with self.lock:
            while self._frame_queue:
                packet = self._frame_queue.popleft()
                self._delivered_frame_count += 1
                data = self._copy_frame_data(packet["data"])
                packets.append((data, packet["timestamp"], packet["frame_id"], dict(packet["time_info"])))
        return packets

    def get_runtime_status(self):
        with self.lock:
            return {
                "running": self.running,
                "frame_count": self.frame_count,
                "produced_frame_count": self.frame_count,
                "delivered_frame_count": self._delivered_frame_count,
                "dropped_frame_count": self._dropped_frame_count,
                "latest_timestamp": self.latest_timestamp,
                "queue_depth": len(self._frame_queue),
                "max_queue_depth": self._max_queue_depth,
                "queue_capacity": self.frame_queue_size,
            }

    def _publish_frame_packet(
        self,
        frame: np.ndarray,
        *,
        capture_wall_ns: int,
        capture_mono_ns: int,
        read_start_wall_ns: int,
        read_end_wall_ns: int,
    ) -> None:
        time_info = {
            "host_capture_time_ns": capture_wall_ns,
            "monotonic_capture_time_ns": capture_mono_ns,
            "host_arrival_time_ns": read_start_wall_ns,
            "host_read_start_time_ns": read_start_wall_ns,
            "host_read_end_time_ns": read_end_wall_ns,
        }
        with self.lock:
            self.frame_count += 1
            packet = {
                "data": frame,
                "timestamp": capture_wall_ns / 1_000_000_000.0,
                "frame_id": self.frame_count,
                "time_info": time_info,
            }
            if len(self._frame_queue) >= self.frame_queue_size:
                self._frame_queue.popleft()
                self._dropped_frame_count += 1
            self._frame_queue.append(packet)
            self._max_queue_depth = max(self._max_queue_depth, len(self._frame_queue))
            self.latest_data = frame
            self.latest_timestamp = packet["timestamp"]
            self.latest_time_info = dict(time_info)

    def _process_frame(self, frame_bgr):
        if self.flip_vertical:
            frame_bgr = cv2.flip(frame_bgr, 0)
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return np.ascontiguousarray(frame_rgb)

    def _copy_frame_data(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "copy"):
            return value.copy()
        return value

    def _close_hardware(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
