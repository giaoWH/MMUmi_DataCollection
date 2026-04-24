from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import socket
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.core import SensorFrame
from sdk.transport import recv_frame_message


is_running = True


def _handle_signal(_sig: int, _frame: object) -> None:
    global is_running
    is_running = False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "接收末端执行器 TCP latest frame。警告: v1 使用 pickle，"
            "只能监听可信网线直连或可信局域网。"
        )
    )
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--status-interval-sec", type=float, default=1.0)
    return parser.parse_args(argv)


def _frame_summary(frame: SensorFrame) -> dict[str, object]:
    return {
        "sensor_name": frame.sensor_name,
        "sensor_type": frame.sensor_type,
        "modality": frame.modality,
        "frame_id": frame.frame_id,
        "host_time_ns": frame.time.host_time_ns,
        "payload_keys": list(frame.payload.keys()),
    }


def run_receiver(args: argparse.Namespace) -> int:
    latest_frames: dict[str, SensorFrame] = {}
    received_count = 0
    last_status_at = time.perf_counter()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((args.host, int(args.port)))
        server.listen(1)
        server.settimeout(0.5)
        print(f"SDK network receiver listening on {args.host}:{args.port}")
        print("WARNING: pickle receiver is only safe on trusted direct/LAN links.")

        while is_running:
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue

            print(f"client connected: {addr[0]}:{addr[1]}")
            with conn:
                conn.settimeout(0.5)
                while is_running:
                    try:
                        frame = recv_frame_message(conn)
                    except socket.timeout:
                        continue
                    except EOFError:
                        print("client disconnected")
                        break
                    except Exception as exc:
                        print(f"receive error: {type(exc).__name__}: {exc}")
                        break

                    latest_frames[frame.sensor_name] = frame
                    received_count += 1
                    now = time.perf_counter()
                    if now - last_status_at >= max(0.1, float(args.status_interval_sec)):
                        summary = {
                            "received_count": received_count,
                            "sensor_count": len(latest_frames),
                            "latest": {
                                sensor_name: _frame_summary(latest)
                                for sensor_name, latest in sorted(latest_frames.items())
                            },
                        }
                        print(json.dumps(summary, ensure_ascii=False))
                        last_status_at = now

    print("receiver stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    return run_receiver(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
