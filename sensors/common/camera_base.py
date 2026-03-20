import time

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
    ):
        super().__init__(name)
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.flip_vertical = flip_vertical
        self.data_length = width * height * 3
        self._cap = None

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

                with self.lock:
                    self.latest_data = frame
                    self.latest_timestamp = capture_wall_ns / 1_000_000_000.0
                    self.frame_count += 1
                    self.latest_time_info = {
                        "host_capture_time_ns": capture_wall_ns,
                        "monotonic_capture_time_ns": capture_mono_ns,
                        "host_arrival_time_ns": read_start_wall_ns,
                        "host_read_start_time_ns": read_start_wall_ns,
                        "host_read_end_time_ns": read_end_wall_ns,
                    }
            except Exception as e:
                print(f"[{self.name}] 运行时错误: {e}")
                time.sleep(0.05)

    def _process_frame(self, frame_bgr):
        if self.flip_vertical:
            frame_bgr = cv2.flip(frame_bgr, 0)
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return np.ascontiguousarray(frame_rgb)

    def _close_hardware(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
