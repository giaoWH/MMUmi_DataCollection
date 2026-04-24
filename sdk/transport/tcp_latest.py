from __future__ import annotations

from dataclasses import dataclass
import pickle
import socket
import struct
import threading
import time
from typing import BinaryIO

from sdk.core.frame import SensorFrame


LENGTH_PREFIX_FORMAT = ">Q"
LENGTH_PREFIX_BYTES = struct.calcsize(LENGTH_PREFIX_FORMAT)
DEFAULT_MAX_MESSAGE_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class NetworkUplinkConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    mode: str = "per_sensor_latest_raw_frame"
    reconnect_interval_sec: float = 1.0
    send_timeout_sec: float = 2.0
    poll_interval_sec: float = 0.005


class LatestFrameStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames: dict[str, tuple[int, SensorFrame]] = {}
        self._generation = 0
        self._update_count = 0

    def update(self, frame: SensorFrame) -> int:
        with self._lock:
            self._generation += 1
            self._frames[frame.sensor_name] = (self._generation, frame)
            self._update_count += 1
            return self._generation

    def snapshot(self) -> dict[str, tuple[int, SensorFrame]]:
        with self._lock:
            return dict(self._frames)

    def snapshot_since(self, sent_generations: dict[str, int]) -> list[tuple[str, int, SensorFrame]]:
        snapshot = self.snapshot()
        pending: list[tuple[str, int, SensorFrame]] = []
        for sensor_name, (generation, frame) in snapshot.items():
            if generation > sent_generations.get(sensor_name, 0):
                pending.append((sensor_name, generation, frame))
        pending.sort(key=lambda item: item[1])
        return pending

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            return {
                "sensor_count": len(self._frames),
                "update_count": self._update_count,
                "latest": {
                    sensor_name: {
                        "generation": generation,
                        "frame_id": frame.frame_id,
                        "modality": frame.modality,
                    }
                    for sensor_name, (generation, frame) in self._frames.items()
                },
            }


def encode_frame_message(frame: SensorFrame) -> bytes:
    payload = pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL)
    return struct.pack(LENGTH_PREFIX_FORMAT, len(payload)) + payload


def recv_frame_message(reader: BinaryIO | socket.socket, *, max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES) -> SensorFrame:
    prefix = _read_exact(reader, LENGTH_PREFIX_BYTES)
    payload_length = struct.unpack(LENGTH_PREFIX_FORMAT, prefix)[0]
    if payload_length <= 0:
        raise ValueError(f"非法消息长度: {payload_length}")
    if payload_length > max_message_bytes:
        raise ValueError(f"消息过大: {payload_length} > {max_message_bytes}")
    payload = _read_exact(reader, payload_length)
    frame = pickle.loads(payload)
    if not isinstance(frame, SensorFrame):
        raise TypeError(f"消息不是 SensorFrame: {type(frame).__name__}")
    return frame


def _read_exact(reader: BinaryIO | socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        if isinstance(reader, socket.socket):
            chunk = reader.recv(remaining)
        else:
            chunk = reader.read(remaining)
        if not chunk:
            raise EOFError("连接在读取完整消息前关闭")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class TcpLatestFrameSender:
    def __init__(
        self,
        store: LatestFrameStore,
        config: NetworkUplinkConfig,
        *,
        name: str = "sdk-tcp-latest-uplink",
    ) -> None:
        self.store = store
        self.config = config
        self.name = name
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sent_generations: dict[str, int] = {}
        self._lock = threading.Lock()
        self._diagnostics: dict[str, object] = {
            "running": False,
            "connected": False,
            "connect_attempts": 0,
            "sent_frames": 0,
            "last_error": None,
            "last_sent_sensor": None,
            "last_sent_frame_id": None,
        }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        with self._lock:
            self._diagnostics["running"] = False
            self._diagnostics["connected"] = False

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            diagnostics = dict(self._diagnostics)
        diagnostics["latest_store"] = self.store.diagnostics()
        return diagnostics

    def _run(self) -> None:
        with self._lock:
            self._diagnostics["running"] = True
        while not self._stop_event.is_set():
            try:
                self._connect_and_send()
            except Exception as exc:
                with self._lock:
                    self._diagnostics["connected"] = False
                    self._diagnostics["last_error"] = str(exc)
                self._wait_or_stop(max(0.0, float(self.config.reconnect_interval_sec)))
        with self._lock:
            self._diagnostics["running"] = False
            self._diagnostics["connected"] = False

    def _connect_and_send(self) -> None:
        with self._lock:
            self._diagnostics["connect_attempts"] = int(self._diagnostics["connect_attempts"]) + 1
        with socket.create_connection(
            (self.config.host, int(self.config.port)),
            timeout=max(0.1, float(self.config.send_timeout_sec)),
        ) as sock:
            sock.settimeout(max(0.1, float(self.config.send_timeout_sec)))
            with self._lock:
                self._diagnostics["connected"] = True
                self._diagnostics["last_error"] = None
            while not self._stop_event.is_set():
                sent_any = False
                for sensor_name, generation, frame in self.store.snapshot_since(self._sent_generations):
                    sock.sendall(encode_frame_message(frame))
                    self._sent_generations[sensor_name] = generation
                    sent_any = True
                    with self._lock:
                        self._diagnostics["sent_frames"] = int(self._diagnostics["sent_frames"]) + 1
                        self._diagnostics["last_sent_sensor"] = sensor_name
                        self._diagnostics["last_sent_frame_id"] = frame.frame_id
                if not sent_any:
                    self._wait_or_stop(max(0.0, float(self.config.poll_interval_sec)))

    def _wait_or_stop(self, timeout_sec: float) -> None:
        self._stop_event.wait(timeout=timeout_sec)
