import time
import numpy as np
import pyaudio
from .base_sensor import BaseSensor


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
        rate=44100,
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

    def _worker(self):
        try:
            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self.chunk,
            )
        except Exception as e:
            print(f"[{self.name}] 麦克风打开失败: {e}")
            self.running = False
            self._close_hardware()
            return

        while self.running:
            try:
                data_raw = self._stream.read(self.chunk, exception_on_overflow=False)
                arrival_time = time.time()

                frame = np.frombuffer(data_raw, dtype=np.int16).copy()
                if self.channels > 1:
                    frame = frame.reshape(-1, self.channels)

                with self.lock:
                    self.latest_data = frame
                    self.latest_timestamp = arrival_time
                    self.frame_count += 1
            except Exception as e:
                print(f"[{self.name}] 运行时错误: {e}")
                time.sleep(0.05)

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
