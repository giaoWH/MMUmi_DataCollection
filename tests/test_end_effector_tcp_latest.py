import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from scripts import sdk_record
from sdk.transport import recv_frame_message


class EndEffectorTcpLatestIntegrationTest(unittest.TestCase):
    def test_fake_end_effector_sends_frames_without_creating_session(self) -> None:
        received: list[tuple[str, int]] = []
        stop_event = threading.Event()

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(1.0)
        port = server.getsockname()[1]

        def _server_worker() -> None:
            try:
                conn, _addr = server.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(1.0)
                while not stop_event.is_set() and len(received) < 2:
                    try:
                        frame = recv_frame_message(conn)
                    except Exception:
                        break
                    received.append((frame.sensor_name, frame.frame_id))

        thread = threading.Thread(target=_server_worker, daemon=True)
        thread.start()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            config_path = Path(tmp_dir) / "end_effector.json"
            config_path.write_text(
                json.dumps(
                    {
                        "device_role": "end_effector",
                        "output_root": str(output_root),
                        "sensor_source": "fake",
                        "duration_sec": 0.25,
                        "startup_discard_sec": 0.0,
                        "enable_ft": True,
                        "enable_imu": True,
                        "enable_realsense": False,
                        "enable_motors": True,
                        "enable_microphone": False,
                        "enable_camera": False,
                        "enable_gelsight": False,
                        "network_uplink": {
                            "enabled": True,
                            "host": "127.0.0.1",
                            "port": port,
                            "reconnect_interval_sec": 0.05,
                            "send_timeout_sec": 0.5,
                            "poll_interval_sec": 0.002,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            try:
                exit_code = sdk_record.main(["--config", str(config_path)])
            finally:
                stop_event.set()
                server.close()
                thread.join(timeout=1.0)

            self.assertEqual(exit_code, 0)
            self.assertTrue(received)
            self.assertNotIn("motors", {sensor_name for sensor_name, _frame_id in received})
            self.assertFalse(output_root.exists())


if __name__ == "__main__":
    unittest.main()
