from collections import deque
import threading
import time
import numpy as np
from .common.base_sensor import BaseSensor
from .microphone.audio_utils import create_audio_interface, open_input_stream

try:
    import pyaudio
except ImportError:  # pragma: no cover
    pyaudio = None


class MicrophoneSensor(BaseSensor):
    """
    麦克风传感器。

    输出格式:
    单声道时为 [sample_0, sample_1, ...]
    多声道时为 shape=(chunk, channels) 的 int16 数组
    """

    def __init__(
        self,
        name="Microphone",
        channels=1,
        rate=48000,
        chunk=1024,
        device_index=None,
    ):
        super().__init__(name)
        self.channels = channels
        self.rate = rate
        self.chunk = chunk
        self.device_index = device_index
        self.data_length = chunk * channels

        self._audio = None
        self._stream = None
        self.effective_rate = rate
        self.resolved_device_index = device_index
        self.device_name = None
        self.open_error = None
        self._ready_event = threading.Event()
        self._frame_queue = deque()

    def start(self):
        if pyaudio is None:
            raise RuntimeError("未安装 pyaudio，无法启动麦克风传感器")
        self.effective_rate = self.rate
        self.resolved_device_index = self.device_index
        self.device_name = None
        self.open_error = None
        self._ready_event.clear()
        with self.lock:
            self._frame_queue.clear()
        super().start()

    def _worker(self):
        try:
            self._audio = create_audio_interface(pyaudio)
            self._stream, device_info, actual_rate = open_input_stream(
                self._audio,
                audio_format=pyaudio.paInt16,
                channels=self.channels,
                preferred_rate=self.rate,
                chunk=self.chunk,
                device_index=self.device_index,
            )
            self.effective_rate = actual_rate
            self.resolved_device_index = int(device_info["index"])
            self.device_name = str(device_info.get("name", "unknown"))
            self._ready_event.set()
        except Exception as e:
            self.open_error = str(e)
            print(f"[{self.name}] 麦克风打开失败: {e}")
            self.running = False
            self._close_hardware()
            return

        while self.running:
            try:
                read_start_wall_ns = time.time_ns()
                read_start_mono_ns = time.perf_counter_ns()
                data_raw = self._stream.read(self.chunk, exception_on_overflow=False)
                read_end_wall_ns = time.time_ns()
                read_end_mono_ns = time.perf_counter_ns()

                frame = np.frombuffer(data_raw, dtype=np.int16).copy()
                if self.channels > 1:
                    frame = frame.reshape(-1, self.channels)

                capture_wall_ns = (read_start_wall_ns + read_end_wall_ns) // 2
                capture_mono_ns = (read_start_mono_ns + read_end_mono_ns) // 2
                with self.lock:
                    self.frame_count += 1
                    packet = {
                        "data": frame,
                        "timestamp": capture_wall_ns / 1_000_000_000.0,
                        "frame_id": self.frame_count,
                        "time_info": {
                            "host_capture_time_ns": capture_wall_ns,
                            "monotonic_capture_time_ns": capture_mono_ns,
                            "host_arrival_time_ns": read_start_wall_ns,
                            "host_read_start_time_ns": read_start_wall_ns,
                            "host_read_end_time_ns": read_end_wall_ns,
                        },
                    }
                    self._frame_queue.append(packet)
                    self.latest_data = frame
                    self.latest_timestamp = packet["timestamp"]
                    self.latest_time_info = dict(packet["time_info"])
            except Exception as e:
                print(f"[{self.name}] 运行时错误: {e}")
                time.sleep(0.05)

    def get_next_data_with_time_info(self):
        with self.lock:
            if not self._frame_queue:
                return None, 0.0, 0, {}
            packet = self._frame_queue.popleft()
            data = packet["data"].copy() if packet["data"] is not None else None
            return data, packet["timestamp"], packet["frame_id"], dict(packet["time_info"])

    def get_all_data_with_time_info(self):
        packets = []
        with self.lock:
            while self._frame_queue:
                packet = self._frame_queue.popleft()
                data = packet["data"].copy() if packet["data"] is not None else None
                packets.append((data, packet["timestamp"], packet["frame_id"], dict(packet["time_info"])))
        return packets

    def _close_hardware(self):
        if self._stream is not None:
            try:
                if self._stream.is_active():
                    self._stream.stop_stream()
            except Exception:
                pass

            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._audio is not None:
            try:
                self._audio.terminate()
            except Exception:
                pass
            self._audio = None

        self._ready_event.clear()
        with self.lock:
            self._frame_queue.clear()

    def is_calibrated(self):
        return self._ready_event.is_set()

    def wait_until_calibrated(self, timeout=None):
        return self._ready_event.wait(timeout=timeout)
