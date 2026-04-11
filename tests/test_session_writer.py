import json
import subprocess
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from sdk.annotations import AnnotationSchema, AnnotationService
from sdk.core.frame import AlignedFrame, FrameTime, SensorFrame, TrajectoryFrame
from sdk.core.session import create_session_info
from sdk.exporters.hdf5_exporter import HDF5SessionExporter
from sdk.exporters.lerobot_exporter import LeRobotSessionExporter, _split_for_session_id
from sdk.exporters.rlds_exporter import RLDSSessionExporter
from sdk.exporters.csv_exporter import CSVSnapshotExporter
from sdk.perception.orbslam3.bundle import OrbSlam3SessionBundleExporter
from sdk.perception.orbslam3.command_runner import OrbSlam3CommandConfig, OrbSlam3CommandRunner
from sdk.perception.orbslam3.pipeline import OrbSlam3Pipeline, OrbSlam3PipelineConfig
from sdk.perception.orbslam3.wrapper import (
    convert_euroc_trajectory_to_jsonl,
    load_stereo_inertial_bundle_paths,
    run_stereo_inertial_wrapper,
)
from sdk.storage.session_reader import SessionReader
from sdk.storage.session_writer import SessionWriter

try:
    import av
except ImportError:  # pragma: no cover
    av = None

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import h5py
except ImportError:  # pragma: no cover
    h5py = None


class SessionWriterTest(unittest.TestCase):
    def _create_orbslam3_session(self, output_root: str) -> Path:
        session = create_session_info(
            output_root,
            task_name="orbslam3_session",
            sensors={
                "realsense": {"sensor_type": "realsense_rgbd", "modality": "rgbd"},
            },
            config={"align_rate_hz": 30},
        )
        writer = SessionWriter(session)
        writer.write_sensor_frame(
            SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense_rgbd",
                modality="rgbd",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0, device_time=1.0),
                payload={
                    "color": np.full((4, 6, 3), 32, dtype=np.uint8),
                    "depth": np.full((4, 6), 1200, dtype=np.uint16),
                    "aligned_depth_to_color": np.full((4, 6), 1190, dtype=np.uint16),
                    "ir1": np.full((4, 6), 80, dtype=np.uint8),
                    "ir2": np.full((4, 6), 90, dtype=np.uint8),
                    "imu_samples": [
                        {
                            "sample_type": "accel",
                            "timestamp_ms": 1000.0,
                            "x": 0.1,
                            "y": 0.2,
                            "z": 9.81,
                        }
                    ],
                },
                metadata={"stream": "rgbd"},
            )
        )
        writer.write_sensor_frame(
            SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense_rgbd",
                modality="rgbd",
                frame_id=2,
                time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1, device_time=1.1),
                payload={
                    "color": np.full((4, 6, 3), 64, dtype=np.uint8),
                    "depth": np.full((4, 6), 1250, dtype=np.uint16),
                    "aligned_depth_to_color": np.full((4, 6), 1240, dtype=np.uint16),
                    "ir1": np.full((4, 6), 100, dtype=np.uint8),
                    "ir2": np.full((4, 6), 110, dtype=np.uint8),
                    "imu_samples": [
                        {
                            "sample_type": "gyro",
                            "timestamp_ms": 1100.0,
                            "x": 0.01,
                            "y": 0.02,
                            "z": 0.03,
                        }
                    ],
                },
                metadata={"stream": "rgbd"},
            )
        )
        writer.close()
        return session.output_dir

    def _create_fake_stereo_inertial_runner(self, output_root: str, *, empty_output: bool = False) -> Path:
        script_path = Path(output_root) / "fake_stereo_inertial_runner.py"
        trajectory_body = "" if empty_output else "\n".join(
            [
                "# fake euroc trajectory",
                "1000000000 1.0 2.0 3.0 0.0 0.0 0.0 1.0",
                "1100000000 4.0 5.0 6.0 0.1 0.2 0.3 0.9",
            ]
        )
        script_path.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import argparse",
                    "from pathlib import Path",
                    "",
                    "parser = argparse.ArgumentParser()",
                    "parser.add_argument('--vocab', required=True)",
                    "parser.add_argument('--settings', required=True)",
                    "parser.add_argument('--association', required=True)",
                    "parser.add_argument('--imu', required=True)",
                    "parser.add_argument('--output', required=True)",
                    "args = parser.parse_args()",
                    "Path(args.output).write_text(" + repr(trajectory_body) + ", encoding='utf-8')",
                ]
            ),
            encoding="utf-8",
        )
        script_path.chmod(0o755)
        return script_path

    def _create_lerobot_merge_session(
        self,
        output_root: str,
        *,
        session_name: str,
        task_name: str = "unspecified",
        num_steps: int = 2,
        include_final_trajectory: bool = True,
        include_final_motor_2: bool = True,
        include_camera: bool = False,
        include_microphone: bool = False,
    ) -> Path:
        sensors = {
            "realsense": {"sensor_type": "realsense", "modality": "rgbd"},
            "motors": {"sensor_type": "motors_sensor", "modality": "motor_state"},
        }
        if include_camera:
            sensors["camera"] = {"sensor_type": "camera_sensor", "modality": "rgb"}
        if include_microphone:
            sensors["microphone"] = {"sensor_type": "microphone_sensor", "modality": "audio"}

        session = create_session_info(
            output_root,
            task_name=task_name,
            sensors=sensors,
            config={"align_rate_hz": 30},
            session_name=session_name,
        )
        writer = SessionWriter(session)

        realsense_frames: list[SensorFrame] = []
        motors_frames: list[SensorFrame] = []
        camera_frames: list[SensorFrame] = []
        microphone_frames: list[SensorFrame] = []
        for step in range(num_steps):
            timestamp = 1.0 + 0.1 * step
            realsense_frame = SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=step + 1,
                time=FrameTime(host_time=timestamp, monotonic_time=timestamp, aligned_time=timestamp, device_time=timestamp),
                payload={"depth": np.full((2, 2), 1000 + step, dtype=np.uint16)},
            )
            realsense_frames.append(realsense_frame)

            motors_payload = {}
            if include_final_motor_2 or step < (num_steps - 1):
                motors_payload["motor_2"] = {
                    "position": 2.0 + 0.2 * step,
                    "velocity": 0.0,
                    "torque": 0.0,
                }
            motors_frame = SensorFrame(
                sensor_name="motors",
                sensor_type="motors_sensor",
                modality="motor_state",
                frame_id=100 + step,
                time=FrameTime(host_time=timestamp, monotonic_time=timestamp, aligned_time=timestamp, device_time=timestamp),
                payload=motors_payload,
            )
            motors_frames.append(motors_frame)

            if include_camera:
                camera_frame = SensorFrame(
                    sensor_name="camera",
                    sensor_type="camera_sensor",
                    modality="rgb",
                    frame_id=200 + step,
                    time=FrameTime(host_time=timestamp, monotonic_time=timestamp, aligned_time=timestamp),
                    payload={"color": np.full((4, 4, 3), 32 + step * 16, dtype=np.uint8)},
                )
                camera_frames.append(camera_frame)
            if include_microphone:
                microphone_frame = SensorFrame(
                    sensor_name="microphone",
                    sensor_type="microphone_sensor",
                    modality="audio",
                    frame_id=300 + step,
                    time=FrameTime(host_time=timestamp, monotonic_time=timestamp, aligned_time=timestamp),
                    payload={"audio": np.array([0, 1000 + step, -1000 - step, 500 + step], dtype=np.int16)},
                )
                microphone_frames.append(microphone_frame)

        for frame in realsense_frames + motors_frames + camera_frames + microphone_frames:
            writer.write_sensor_frame(frame)

        for step in range(num_steps):
            frames = {
                "realsense": realsense_frames[step],
                "motors": motors_frames[step],
            }
            age_by_sensor = {"realsense": 0.0, "motors": 0.0}
            if include_camera:
                frames["camera"] = camera_frames[step]
                age_by_sensor["camera"] = 0.0
            if include_microphone:
                frames["microphone"] = microphone_frames[step]
                age_by_sensor["microphone"] = 0.0
            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=step,
                    aligned_time=1.0 + 0.1 * step,
                    frames=frames,
                    missing_sensors=[],
                    age_by_sensor=age_by_sensor,
                )
            )

        trajectory_steps = num_steps if include_final_trajectory else max(1, num_steps - 1)
        for step in range(trajectory_steps):
            timestamp = 1.0 + 0.1 * step
            writer.write_trajectory_frame(
                TrajectoryFrame(
                    source="orbslam3",
                    frame_id=400 + step,
                    time=FrameTime(host_time=timestamp, monotonic_time=timestamp, aligned_time=timestamp),
                    position=[0.1 * step, 0.0, 0.0],
                    quaternion=[1.0, 0.0, 0.0, 0.0],
                    tracking_state="OK",
                )
            )
        writer.close()

        service = AnnotationService(session.output_dir, schema=AnnotationSchema.default())
        service.ensure_initialized()
        service.upsert_session_annotation({"task_name": task_name})
        return session.output_dir

    def test_write_sensor_frame_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                task_name="realsense_capture",
                sensors={"realsense": {"modality": "rgbd"}},
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)
            frame = SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=1,
                time=FrameTime(
                    host_time=1.0,
                    monotonic_time=2.0,
                    host_arrival_time=1.0002,
                    host_read_start_time_ns=999_900_000,
                    host_read_end_time_ns=1_000_100_000,
                ),
                payload={
                    "depth": np.ones((2, 2), dtype=np.uint16),
                    "acceleration": np.array([1.0, 2.0, 3.0]),
                },
            )

            writer.write_sensor_frame(frame)

            meta_path = session.output_dir / "meta.json"
            manifest_path = session.output_dir / "manifest.json"
            sensor_log = session.output_dir / "streams" / "realsense" / "realsense_capture_rgbd_001.jsonl"
            self.assertTrue(meta_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertTrue(sensor_log.exists())

            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["config"]["align_rate_hz"], 30)
            self.assertEqual(payload["task_name"], "realsense_capture")
            self.assertEqual(payload["task_slug"], "realsense_capture")
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest_payload["sensors"]["realsense"]["frames_path"],
                "streams/realsense/realsense_capture_rgbd_001.jsonl",
            )
            self.assertEqual(
                manifest_payload["sensors"]["realsense"]["media_path"],
                "streams/realsense/realsense_capture_rgbd_001.mp4",
            )

            records = sensor_log.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(records), 1)
            record = json.loads(records[0])
            self.assertEqual(record["payload"]["acceleration"], [1.0, 2.0, 3.0])
            self.assertEqual(record["time"]["host_time_ns"], 1_000_000_000)
            self.assertEqual(record["time"]["monotonic_time_ns"], 2_000_000_000)
            self.assertNotIn("host_arrival_time", record["time"])
            self.assertNotIn("host_arrival_time_ns", record["time"])
            self.assertNotIn("host_read_start_time_ns", record["time"])
            self.assertNotIn("host_read_end_time_ns", record["time"])
            self.assertEqual(
                record["payload"]["depth"]["path"],
                "streams/realsense/artifacts/realsense_capture_rgbd_001_000001_depth.npy",
            )

            depth_path = Path(session.output_dir) / record["payload"]["depth"]["path"]
            self.assertTrue(depth_path.exists())

            reader = SessionReader(session.output_dir)
            loaded_frame = next(reader.iter_sensor_frames("realsense", load_payload=True))
            self.assertEqual(loaded_frame.payload["acceleration"], [1.0, 2.0, 3.0])
            self.assertEqual(loaded_frame.time.host_time_ns, 1_000_000_000)
            self.assertEqual(loaded_frame.time.monotonic_time_ns, 2_000_000_000)
            self.assertIsNone(loaded_frame.time.host_arrival_time)
            self.assertIsNone(loaded_frame.time.host_arrival_time_ns)
            self.assertIsNone(loaded_frame.time.host_read_start_time_ns)
            self.assertIsNone(loaded_frame.time.host_read_end_time_ns)
            np.testing.assert_array_equal(
                loaded_frame.payload["depth"],
                np.ones((2, 2), dtype=np.uint16),
            )

            appender = SessionWriter.open_existing(session.output_dir)
            appender.write_trajectory_frame(
                TrajectoryFrame(
                    source="orbslam3",
                    frame_id=7,
                    time=FrameTime(host_time=3.0, monotonic_time=4.0, aligned_time=3.0),
                    position=[0.0, 0.0, 0.0],
                    quaternion=[1.0, 0.0, 0.0, 0.0],
                    tracking_state="OK",
                )
            )
            trajectory = list(reader.iter_trajectory_frames())
            self.assertEqual(len(trajectory), 1)
            self.assertEqual(trajectory[0].source, "orbslam3")

    def test_create_session_info_rejects_invalid_task_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaisesRegex(ValueError, "task_name"):
                create_session_info(
                    tmp_dir,
                    task_name=" / \\\\ ",
                    sensors={"camera": {"modality": "rgb"}},
                    config={"align_rate_hz": 30},
                )

    def test_async_writer_flushes_records_and_persists_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                task_name="camera_async",
                sensors={"camera": {"modality": "rgb"}},
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session, async_writes=True, write_queue_size=8)
            writer.write_sensor_frame(
                SensorFrame(
                    sensor_name="camera",
                    sensor_type="camera_sensor",
                    modality="rgb",
                    frame_id=1,
                    time=FrameTime(host_time=1.0, monotonic_time=2.0),
                    payload={"color": np.full((2, 2, 3), 16, dtype=np.uint8)},
                    metadata={"device_index": 0},
                )
            )
            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=0,
                    aligned_time=1.0,
                    frames={},
                    missing_sensors=[],
                    age_by_sensor={},
                )
            )
            writer.close()

            sensor_log = session.output_dir / "streams" / "camera" / "camera_async_rgb_001.jsonl"
            aligned_log = session.output_dir / "aligned" / "frames.jsonl"
            self.assertTrue(sensor_log.exists())
            self.assertTrue(aligned_log.exists())
            self.assertEqual(len(sensor_log.read_text(encoding="utf-8").strip().splitlines()), 1)
            self.assertEqual(len(aligned_log.read_text(encoding="utf-8").strip().splitlines()), 1)

            meta_payload = json.loads((session.output_dir / "meta.json").read_text(encoding="utf-8"))
            diagnostics = meta_payload["notes"]["writer_diagnostics"]
            self.assertTrue(diagnostics["async_writes"])
            self.assertEqual(diagnostics["written_counts"]["sensor"], 1)
            self.assertEqual(diagnostics["written_counts"]["aligned"], 1)
            self.assertGreaterEqual(diagnostics["write_latency"]["samples"], 2)

    @unittest.skipIf(av is None, "未安装 PyAV")
    def test_camera_media_round_trip_uses_standard_h264_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                task_name="camera_media",
                sensors={"camera": {"sensor_type": "camera_sensor", "modality": "rgb"}},
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)
            rgb_frame = np.zeros((16, 16, 3), dtype=np.uint8)
            rgb_frame[:8, :8] = [255, 0, 0]
            rgb_frame[:8, 8:] = [0, 255, 0]
            rgb_frame[8:, :8] = [0, 0, 255]
            rgb_frame[8:, 8:] = [200, 200, 200]
            writer.write_sensor_frame(
                SensorFrame(
                    sensor_name="camera",
                    sensor_type="camera_sensor",
                    modality="rgb",
                    frame_id=1,
                    time=FrameTime(host_time=1.0, monotonic_time=2.0),
                    payload={"color": rgb_frame},
                )
            )
            writer.close()

            sensor_log = session.output_dir / "streams" / "camera" / "camera_media_rgb_001.jsonl"
            record = json.loads(sensor_log.read_text(encoding="utf-8").strip())
            reference = record["payload"]["color"]
            self.assertEqual(reference["storage"], "mp4_frame")
            self.assertEqual(reference["channel_order"], "rgb")
            media_path = session.output_dir / reference["path"]
            self.assertTrue(media_path.exists())
            self.assertEqual(media_path.name, "camera_media_rgb_001.mp4")
            self.assertEqual(
                writer.manifest.sensors["camera"].frames_path,
                "streams/camera/camera_media_rgb_001.jsonl",
            )
            self.assertEqual(
                writer.manifest.sensors["camera"].media_path,
                "streams/camera/camera_media_rgb_001.mp4",
            )
            self.assertEqual(
                writer.manifest.sensors["camera"].metadata["media_encoding"]["video_codec"],
                "libx264",
            )
            self.assertEqual(
                writer.manifest.sensors["camera"].metadata["media_encoding"]["video_pixel_format"],
                "yuv420p",
            )

            container = av.open(str(media_path))
            try:
                video_stream = next(stream for stream in container.streams if stream.type == "video")
                self.assertEqual(video_stream.codec_context.name, "h264")
                self.assertEqual(video_stream.codec_context.pix_fmt, "yuv420p")
            finally:
                container.close()

            reader = SessionReader(session.output_dir)
            loaded_frame = next(reader.iter_sensor_frames("camera", load_payload=True))
            loaded = loaded_frame.payload["color"]
            self.assertEqual(loaded.shape, rgb_frame.shape)
            self.assertEqual(loaded.dtype, np.uint8)
            self.assertLess(np.mean(np.abs(loaded.astype(np.int16) - rgb_frame.astype(np.int16))), 25.0)
            quadrant_means = {
                "red": loaded[:8, :8].mean(axis=(0, 1)),
                "green": loaded[:8, 8:].mean(axis=(0, 1)),
                "blue": loaded[8:, :8].mean(axis=(0, 1)),
            }
            self.assertGreater(quadrant_means["red"][0], quadrant_means["red"][1])
            self.assertGreater(quadrant_means["red"][0], quadrant_means["red"][2])
            self.assertGreater(quadrant_means["green"][1], quadrant_means["green"][0])
            self.assertGreater(quadrant_means["green"][1], quadrant_means["green"][2])
            self.assertGreater(quadrant_means["blue"][2], quadrant_means["blue"][0])
            self.assertGreater(quadrant_means["blue"][2], quadrant_means["blue"][1])

    def test_microphone_audio_round_trip_uses_camera_media_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                task_name="camera_microphone",
                sensors={
                    "camera": {"sensor_type": "camera_sensor", "modality": "rgb", "fps": 30},
                    "microphone": {
                        "sensor_type": "microphone_sensor",
                        "modality": "audio",
                        "channels": 1,
                        "sample_rate": 48000,
                    },
                },
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)
            audio_frame = SensorFrame(
                sensor_name="microphone",
                sensor_type="microphone_sensor",
                modality="audio",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=2.0),
                payload={"audio": np.array([0, 1000, -1000, 500], dtype=np.int16)},
            )
            camera_frame = SensorFrame(
                sensor_name="camera",
                sensor_type="camera_sensor",
                modality="rgb",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=2.0),
                payload={"color": np.full((2, 2, 3), 16, dtype=np.uint8)},
            )

            writer.write_sensor_frame(audio_frame)
            writer.write_sensor_frame(camera_frame)
            writer.close()

            sensor_log = session.output_dir / "streams" / "microphone" / "camera_microphone_audio_001.jsonl"
            record = json.loads(sensor_log.read_text(encoding="utf-8").strip())
            reference = record["payload"]["audio"]
            self.assertEqual(reference["storage"], "mp4_audio")
            self.assertEqual(reference["path"], "streams/camera/camera_microphone_rgb_001.mp4")

            reader = SessionReader(session.output_dir)
            loaded_frame = next(reader.iter_sensor_frames("microphone", load_payload=True))
            np.testing.assert_array_equal(
                loaded_frame.payload["audio"],
                np.array([0, 1000, -1000, 500], dtype=np.int16),
            )

    def test_orbslam3_command_runner_parse_stdout_jsonl(self) -> None:
        payload = {
            "timestamp": 12.5,
            "position": [1.0, 2.0, 3.0],
            "quaternion": [1.0, 0.0, 0.0, 0.0],
            "tracking_state": "OK",
        }
        command = (
            f"{shlex.quote(sys.executable)} -c "
            f"\"import json,sys; sys.stdout.write(json.dumps({payload!r}) + '\\\\n')\""
        )
        runner = OrbSlam3CommandRunner(
            OrbSlam3CommandConfig(command=command, output_mode="stdout_jsonl")
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            frames = runner.run(tmp_dir)

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].position, [1.0, 2.0, 3.0])
        self.assertEqual(frames[0].tracking_state, "OK")

    def test_orbslam3_command_runner_rejects_invalid_jsonl_payload(self) -> None:
        payload = {
            "timestamp": 12.5,
            "position": [1.0, 2.0],
            "quaternion": [1.0, 0.0, 0.0, 0.0],
        }
        command = (
            f"{shlex.quote(sys.executable)} -c "
            f"\"import json,sys; sys.stdout.write(json.dumps({payload!r}) + '\\\\n')\""
        )
        runner = OrbSlam3CommandRunner(
            OrbSlam3CommandConfig(command=command, output_mode="stdout_jsonl")
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaisesRegex(ValueError, "position"):
                runner.run(tmp_dir)

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_orbslam3_bundle_exporter_generates_rgbd_and_imu_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)

            bundle = OrbSlam3SessionBundleExporter().export(session_dir, mode="rgbd_inertial")

            self.assertTrue(bundle.manifest_path.exists())
            self.assertTrue((bundle.rgb_dir / "000000.png").exists())
            self.assertTrue((bundle.rgb_dir / "000001.png").exists())
            self.assertTrue((bundle.depth_dir / "000000.png").exists())
            self.assertTrue((bundle.depth_dir / "000001.png").exists())

            association_lines = bundle.association_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(association_lines), 2)
            self.assertEqual(
                association_lines[0],
                "1.000000000 rgb/000000.png 1.000000000 depth/000000.png",
            )

            imu_lines = bundle.imu_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(imu_lines[0], "timestamp,sample_type,x,y,z")
            self.assertEqual(len(imu_lines), 3)

            manifest_payload = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["mode"], "rgbd_inertial")
            self.assertEqual(manifest_payload["frame_count"], 2)
            self.assertEqual(manifest_payload["imu_rows"], 2)

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_orbslam3_bundle_exporter_generates_stereo_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)

            bundle = OrbSlam3SessionBundleExporter().export(session_dir, mode="stereo")

            self.assertTrue((bundle.left_dir / "000000.png").exists())
            self.assertTrue((bundle.right_dir / "000001.png").exists())
            association_lines = bundle.association_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(association_lines), 2)
            self.assertIn("left/000000.png", association_lines[0])
            self.assertIn("right/000000.png", association_lines[0])

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_orbslam3_wrapper_loads_stereo_bundle_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)
            bundle = OrbSlam3SessionBundleExporter().export(session_dir, mode="stereo_inertial")

            bundle_paths = load_stereo_inertial_bundle_paths(bundle.manifest_path)

            self.assertEqual(bundle_paths.mode, "stereo_inertial")
            self.assertEqual(bundle_paths.association_file, bundle.association_file.resolve())
            self.assertEqual(bundle_paths.imu_file, bundle.imu_file.resolve())

    def test_orbslam3_wrapper_converts_euroc_trajectory_to_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            euroc_path = Path(tmp_dir) / "trajectory.txt"
            output_jsonl = Path(tmp_dir) / "trajectory.jsonl"
            settings_path = Path(tmp_dir) / "RealSense_D435i.yaml"
            settings_path.write_text("%YAML:1.0\n", encoding="utf-8")
            euroc_path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "1000000000 1.0 2.0 3.0 0.0 0.0 0.0 1.0",
                        "1100000000 4.0 5.0 6.0 0.1 0.2 0.3 0.9",
                    ]
                ),
                encoding="utf-8",
            )

            rows = convert_euroc_trajectory_to_jsonl(
                euroc_path,
                output_jsonl,
                settings_path=settings_path,
            )

            self.assertEqual(rows, 2)
            payloads = [
                json.loads(line)
                for line in output_jsonl.read_text(encoding="utf-8").strip().splitlines()
            ]
            self.assertEqual(payloads[0]["timestamp"], 1.0)
            self.assertEqual(payloads[0]["position"], [1.0, 2.0, 3.0])
            self.assertEqual(payloads[0]["quaternion"], [1.0, 0.0, 0.0, 0.0])
            self.assertEqual(payloads[0]["metadata"]["trajectory_format"], "euroc_final")
            self.assertEqual(payloads[0]["metadata"]["pose_reference"], "imu_body")
            self.assertEqual(payloads[1]["quaternion"], [0.9, 0.1, 0.2, 0.3])

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_orbslam3_wrapper_runs_fake_runner_from_bundle_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)
            bundle = OrbSlam3SessionBundleExporter().export(session_dir, mode="stereo_inertial")
            output_jsonl = Path(tmp_dir) / "trajectory.jsonl"
            runner_path = self._create_fake_stereo_inertial_runner(tmp_dir)
            vocab_path = Path(tmp_dir) / "ORBvoc.txt"
            settings_path = Path(tmp_dir) / "RealSense_D435i.yaml"
            vocab_path.write_text("fake vocab", encoding="utf-8")
            settings_path.write_text("%YAML:1.0\n", encoding="utf-8")

            row_count = run_stereo_inertial_wrapper(
                bundle_manifest=bundle.manifest_path,
                output_jsonl=output_jsonl,
                env={
                    "ORB_SLAM3_RUNNER": str(runner_path),
                    "ORB_SLAM3_VOCAB": str(vocab_path),
                    "ORB_SLAM3_SETTINGS": str(settings_path),
                },
            )

            self.assertEqual(row_count, 2)
            payloads = [
                json.loads(line)
                for line in output_jsonl.read_text(encoding="utf-8").strip().splitlines()
            ]
            self.assertEqual(payloads[0]["position"], [1.0, 2.0, 3.0])
            self.assertEqual(payloads[1]["quaternion"], [0.9, 0.1, 0.2, 0.3])

    def test_orbslam3_wrapper_requires_settings_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            euroc_path = Path(tmp_dir) / "trajectory.jsonl"
            manifest_path = Path(tmp_dir) / "bundle_manifest.json"
            association_file = Path(tmp_dir) / "stereo_associations.txt"
            imu_file = Path(tmp_dir) / "imu.csv"
            association_file.write_text("1.0 left/000000.png 1.0 right/000000.png\n", encoding="utf-8")
            imu_file.write_text("timestamp,sample_type,x,y,z\n1.0,accel,0,0,9.81\n", encoding="utf-8")
            manifest_path.write_text(
                json.dumps(
                    {
                        "mode": "stereo_inertial",
                        "files": {
                            "association_file": str(association_file),
                            "imu_file": str(imu_file),
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            vocab_path = Path(tmp_dir) / "ORBvoc.txt"
            vocab_path.write_text("fake vocab", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "ORB_SLAM3_SETTINGS"):
                run_stereo_inertial_wrapper(
                    bundle_manifest=manifest_path,
                    output_jsonl=euroc_path,
                    env={"ORB_SLAM3_VOCAB": str(vocab_path)},
                )

    def test_orbslam3_wrapper_requires_runner_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            euroc_path = Path(tmp_dir) / "trajectory.jsonl"
            association_file = Path(tmp_dir) / "stereo_associations.txt"
            imu_file = Path(tmp_dir) / "imu.csv"
            manifest_path = Path(tmp_dir) / "bundle_manifest.json"
            association_file.write_text("1.0 left/000000.png 1.0 right/000000.png\n", encoding="utf-8")
            imu_file.write_text("timestamp,sample_type,x,y,z\n1.0,gyro,0,0,0\n1.0,accel,0,0,9.81\n", encoding="utf-8")
            manifest_path.write_text(
                json.dumps(
                    {
                        "mode": "stereo_inertial",
                        "files": {
                            "association_file": str(association_file),
                            "imu_file": str(imu_file),
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            vocab_path = Path(tmp_dir) / "ORBvoc.txt"
            settings_path = Path(tmp_dir) / "RealSense_D435i.yaml"
            vocab_path.write_text("fake vocab", encoding="utf-8")
            settings_path.write_text("%YAML:1.0\n", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "runner"):
                run_stereo_inertial_wrapper(
                    bundle_manifest=manifest_path,
                    output_jsonl=euroc_path,
                    env={
                        "ORB_SLAM3_RUNNER": str(Path(tmp_dir) / "missing_runner"),
                        "ORB_SLAM3_VOCAB": str(vocab_path),
                        "ORB_SLAM3_SETTINGS": str(settings_path),
                    },
                )

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_orbslam3_wrapper_rejects_empty_trajectory_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)
            bundle = OrbSlam3SessionBundleExporter().export(session_dir, mode="stereo_inertial")
            output_jsonl = Path(tmp_dir) / "trajectory.jsonl"
            runner_path = self._create_fake_stereo_inertial_runner(tmp_dir, empty_output=True)
            vocab_path = Path(tmp_dir) / "ORBvoc.txt"
            settings_path = Path(tmp_dir) / "RealSense_D435i.yaml"
            vocab_path.write_text("fake vocab", encoding="utf-8")
            settings_path.write_text("%YAML:1.0\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "未生成有效轨迹"):
                run_stereo_inertial_wrapper(
                    bundle_manifest=bundle.manifest_path,
                    output_jsonl=output_jsonl,
                    env={
                        "ORB_SLAM3_RUNNER": str(runner_path),
                        "ORB_SLAM3_VOCAB": str(vocab_path),
                        "ORB_SLAM3_SETTINGS": str(settings_path),
                    },
                )

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_orbslam3_pipeline_consumes_bundle_template_vars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)
            script = (
                "import json, pathlib; "
                "manifest = json.loads(pathlib.Path(r'{bundle_manifest}').read_text(encoding='utf-8')); "
                "payload = {"
                "'timestamp': 2.5, "
                "'position': [manifest['frame_count'], manifest['imu_rows'], 9.0], "
                "'quaternion': [1.0, 0.0, 0.0, 0.0], "
                "'tracking_state': 'OK', "
                "'metadata': {'association_exists': pathlib.Path(manifest['files']['association_file']).exists()}"
                "}; "
                "print(json.dumps(payload, ensure_ascii=False))"
            )
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
            pipeline = OrbSlam3Pipeline(
                OrbSlam3PipelineConfig(
                    command=command,
                    mode="rgbd_inertial",
                    output_mode="stdout_jsonl",
                )
            )

            bundle, frames = pipeline.run(session_dir)

            self.assertTrue(bundle.manifest_path.exists())
            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0].position, [2.0, 2.0, 9.0])
            self.assertTrue(frames[0].metadata["association_exists"])

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_sdk_process_trajectory_cli_writes_manifest_and_trajectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)
            bundle_dir = Path(tmp_dir) / "orb_bundle_cli"
            script = (
                "import json, pathlib; "
                "manifest = json.loads(pathlib.Path(r'{bundle_manifest}').read_text(encoding='utf-8')); "
                "payload = {"
                "'timestamp': 3.5, "
                "'position': [1.0, 2.0, 3.0], "
                "'quaternion': [1.0, 0.0, 0.0, 0.0], "
                "'tracking_state': 'OK', "
                "'metadata': {'imu_rows': manifest['imu_rows']}"
                "}; "
                "pathlib.Path(r'{output_jsonl}').write_text(json.dumps(payload, ensure_ascii=False) + '\\n', encoding='utf-8')"
            )
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
            result = __import__("subprocess").run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "scripts" / "sdk_process_trajectory.py"),
                    str(session_dir),
                    "--command",
                    command,
                    "--mode",
                    "stereo_inertial",
                    "--bundle-dir",
                    str(bundle_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(Path(__file__).resolve().parents[1]),
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)

            reader = SessionReader(session_dir)
            trajectory_frames = list(reader.iter_trajectory_frames())
            self.assertEqual(len(trajectory_frames), 1)
            self.assertEqual(trajectory_frames[0].metadata["imu_rows"], 2)

            manifest_payload = json.loads((Path(session_dir) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["notes"]["orbslam3_mode"], "stereo_inertial")
            self.assertEqual(manifest_payload["notes"]["trajectory_source"], "orbslam3")
            self.assertEqual(manifest_payload["notes"]["orbslam3_bundle"], str(bundle_dir))

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_sdk_process_trajectory_cli_supports_config_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)
            bundle_dir = Path(tmp_dir) / "orb_bundle_config"
            config_path = Path(tmp_dir) / "orbslam3.json"
            script = (
                "import json, os, pathlib; "
                "payload = {"
                "'timestamp': 4.5, "
                "'position': [4.0, 5.0, 6.0], "
                "'quaternion': [1.0, 0.0, 0.0, 0.0], "
                "'tracking_state': os.environ['ORB_TEST_FLAG']"
                "}; "
                "pathlib.Path(r'{output_jsonl}').write_text(json.dumps(payload, ensure_ascii=False) + '\\n', encoding='utf-8')"
            )
            config_path.write_text(
                json.dumps(
                    {
                        "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
                        "mode": "stereo_inertial",
                        "output_mode": "jsonl_file",
                        "source_name": "orbslam3_config",
                        "bundle_dir": str(bundle_dir),
                        "env": {"ORB_TEST_FLAG": "CONFIG_OK"},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = __import__("subprocess").run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "scripts" / "sdk_process_trajectory.py"),
                    str(session_dir),
                    "--config",
                    str(config_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(Path(__file__).resolve().parents[1]),
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)

            reader = SessionReader(session_dir)
            trajectory_frames = list(reader.iter_trajectory_frames())
            self.assertEqual(len(trajectory_frames), 1)
            self.assertEqual(trajectory_frames[0].source, "orbslam3_config")
            self.assertEqual(trajectory_frames[0].tracking_state, "CONFIG_OK")

            manifest_payload = json.loads((Path(session_dir) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["notes"]["trajectory_source"], "orbslam3_config")
            self.assertEqual(manifest_payload["notes"]["orbslam3_bundle"], str(bundle_dir))

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_sdk_process_trajectory_cli_supports_stereo_inertial_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)
            bundle_dir = Path(tmp_dir) / "orb_bundle_stereo_wrapper"
            config_path = Path(tmp_dir) / "orbslam3_stereo_wrapper.json"
            runner_path = self._create_fake_stereo_inertial_runner(tmp_dir)
            vocab_path = Path(tmp_dir) / "ORBvoc.txt"
            settings_path = Path(tmp_dir) / "RealSense_D435i.yaml"
            vocab_path.write_text("fake vocab", encoding="utf-8")
            settings_path.write_text("%YAML:1.0\n", encoding="utf-8")

            config_path.write_text(
                json.dumps(
                    {
                        "mode": "stereo_inertial",
                        "source_name": "orbslam3_stereo_wrapper",
                        "bundle_dir": str(bundle_dir),
                        "env": {
                            "ORB_SLAM3_RUNNER": str(runner_path),
                            "ORB_SLAM3_VOCAB": str(vocab_path),
                            "ORB_SLAM3_SETTINGS": str(settings_path),
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = __import__("subprocess").run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "scripts" / "sdk_process_trajectory.py"),
                    str(session_dir),
                    "--config",
                    str(config_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(Path(__file__).resolve().parents[1]),
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)

            reader = SessionReader(session_dir)
            trajectory_frames = list(reader.iter_trajectory_frames())
            self.assertEqual(len(trajectory_frames), 2)
            self.assertEqual(trajectory_frames[0].source, "orbslam3_stereo_wrapper")
            self.assertEqual(trajectory_frames[0].position, [1.0, 2.0, 3.0])
            self.assertEqual(trajectory_frames[0].metadata["trajectory_format"], "euroc_final")

            manifest_payload = json.loads((Path(session_dir) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["notes"]["orbslam3_mode"], "stereo_inertial")
            self.assertEqual(manifest_payload["notes"]["trajectory_source"], "orbslam3_stereo_wrapper")
            self.assertEqual(manifest_payload["notes"]["orbslam3_bundle"], str(bundle_dir))

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_sdk_process_trajectory_cli_supports_record_config_trajectory_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)
            bundle_dir = Path(tmp_dir) / "orb_bundle_record_cfg"
            config_path = Path(tmp_dir) / "record.yaml"
            script = (
                "import json, pathlib; "
                "payload = {"
                "'timestamp': 6.5, "
                "'position': [6.0, 5.0, 4.0], "
                "'quaternion': [1.0, 0.0, 0.0, 0.0], "
                "'tracking_state': 'FROM_RECORD_CONFIG'"
                "}; "
                "pathlib.Path(r'{output_jsonl}').write_text(json.dumps(payload, ensure_ascii=False) + '\\n', encoding='utf-8')"
            )
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
            config_path.write_text(
                "\n".join(
                    [
                        "trajectory:",
                        f"  command: {json.dumps(command)}",
                        "  mode: stereo_inertial",
                        "  output_mode: jsonl_file",
                        "  source_name: orbslam3_record_config",
                        f"  bundle_dir: {bundle_dir}",
                    ]
                ),
                encoding="utf-8",
            )

            result = __import__("subprocess").run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "scripts" / "sdk_process_trajectory.py"),
                    str(session_dir),
                    "--config",
                    str(config_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(Path(__file__).resolve().parents[1]),
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)

            reader = SessionReader(session_dir)
            trajectory_frames = list(reader.iter_trajectory_frames())
            self.assertEqual(len(trajectory_frames), 1)
            self.assertEqual(trajectory_frames[0].source, "orbslam3_record_config")
            self.assertEqual(trajectory_frames[0].tracking_state, "FROM_RECORD_CONFIG")

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_sdk_process_trajectory_cli_overwrites_existing_trajectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)
            first_script = (
                "import json, pathlib; "
                "payload = {"
                "'timestamp': 1.0, "
                "'position': [1.0, 1.0, 1.0], "
                "'quaternion': [1.0, 0.0, 0.0, 0.0], "
                "'tracking_state': 'FIRST'"
                "}; "
                "pathlib.Path(r'{output_jsonl}').write_text(json.dumps(payload, ensure_ascii=False) + '\\n', encoding='utf-8')"
            )
            second_script = (
                "import json, pathlib; "
                "payload = {"
                "'timestamp': 2.0, "
                "'position': [2.0, 2.0, 2.0], "
                "'quaternion': [1.0, 0.0, 0.0, 0.0], "
                "'tracking_state': 'SECOND'"
                "}; "
                "pathlib.Path(r'{output_jsonl}').write_text(json.dumps(payload, ensure_ascii=False) + '\\n', encoding='utf-8')"
            )
            script_path = Path(__file__).resolve().parents[1] / "scripts" / "sdk_process_trajectory.py"

            for script in (first_script, second_script):
                result = __import__("subprocess").run(
                    [
                        sys.executable,
                        str(script_path),
                        str(session_dir),
                        "--command",
                        f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=str(Path(__file__).resolve().parents[1]),
                )
                self.assertEqual(result.returncode, 0, msg=result.stderr)

            trajectory_frames = list(SessionReader(session_dir).iter_trajectory_frames())
            self.assertEqual(len(trajectory_frames), 1)
            self.assertEqual(trajectory_frames[0].tracking_state, "SECOND")
            self.assertEqual(trajectory_frames[0].position, [2.0, 2.0, 2.0])

    @unittest.skipIf(h5py is None, "未安装 h5py")
    def test_export_hdf5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                task_name="hdf5_export",
                sensors={"imu": {"sensor_type": "imu_sensor", "modality": "imu"}},
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)
            writer.write_sensor_frame(
                SensorFrame(
                    sensor_name="imu",
                    sensor_type="imu_sensor",
                    modality="imu",
                    frame_id=3,
                    time=FrameTime(host_time=1.0, monotonic_time=2.0),
                    payload={"acceleration": [0.1, 0.2, 0.3]},
                )
            )

            exporter = HDF5SessionExporter()
            result = exporter.export(session.output_dir)

            self.assertTrue(result.output_path.exists())
            with h5py.File(result.output_path, "r") as handle:
                self.assertIn("streams", handle)
                self.assertIn("imu", handle["streams"])

    def test_export_rlds_and_lerobot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                task_name="rlds_lerobot_export",
                sensors={
                    "ft": {"sensor_type": "ft_sensor", "modality": "force_torque"},
                    "imu": {"sensor_type": "imu_sensor", "modality": "imu"},
                    "realsense": {"sensor_type": "realsense", "modality": "rgbd"},
                    "motors": {"sensor_type": "motors_sensor", "modality": "motor_state"},
                },
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)
            ft_frame_0 = SensorFrame(
                sensor_name="ft",
                sensor_type="ft_sensor",
                modality="force_torque",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=2.0, device_time=1.0),
                payload={
                    "force_torque": [1.0, 2.0, 3.0, 0.1, 0.2, 0.3],
                    "force": [1.0, 2.0, 3.0],
                    "torque": [0.1, 0.2, 0.3],
                },
            )
            ft_frame_1 = SensorFrame(
                sensor_name="ft",
                sensor_type="ft_sensor",
                modality="force_torque",
                frame_id=2,
                time=FrameTime(host_time=1.1, monotonic_time=2.1, device_time=1.1),
                payload={
                    "force_torque": [1.2, 2.2, 3.2, 0.2, 0.3, 0.4],
                    "force": [1.2, 2.2, 3.2],
                    "torque": [0.2, 0.3, 0.4],
                },
            )
            imu_frame_0 = SensorFrame(
                sensor_name="imu",
                sensor_type="imu_sensor",
                modality="imu",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=2.0, device_time=1.0),
                payload={"acceleration": [0.1, 0.2, 0.3]},
            )
            imu_frame_1 = SensorFrame(
                sensor_name="imu",
                sensor_type="imu_sensor",
                modality="imu",
                frame_id=2,
                time=FrameTime(host_time=1.1, monotonic_time=2.1, device_time=1.1),
                payload={"acceleration": [0.4, 0.5, 0.6]},
            )
            realsense_frame_0 = SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=10,
                time=FrameTime(host_time=1.0, monotonic_time=2.0, device_time=1.0),
                payload={"depth": np.full((2, 2), 1000, dtype=np.uint16)},
            )
            realsense_frame_1 = SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=11,
                time=FrameTime(host_time=1.1, monotonic_time=2.1, device_time=1.1),
                payload={"depth": np.full((2, 2), 1010, dtype=np.uint16)},
            )
            motors_frame_0 = SensorFrame(
                sensor_name="motors",
                sensor_type="motors_sensor",
                modality="motor_state",
                frame_id=20,
                time=FrameTime(host_time=1.0, monotonic_time=2.0, device_time=1.0),
                payload={
                    "motor_1": {"position": 1.0, "velocity": 0.0, "torque": 0.0},
                    "motor_2": {"position": 4.0, "velocity": 5.0, "torque": 6.0},
                },
            )
            motors_frame_1 = SensorFrame(
                sensor_name="motors",
                sensor_type="motors_sensor",
                modality="motor_state",
                frame_id=21,
                time=FrameTime(host_time=1.1, monotonic_time=2.1, device_time=1.1),
                payload={
                    "motor_1": {"position": 1.2, "velocity": 0.0, "torque": 0.0},
                    "motor_2": {"position": 4.3, "velocity": 5.1, "torque": 6.1},
                },
            )

            for frame in (
                ft_frame_0,
                ft_frame_1,
                imu_frame_0,
                imu_frame_1,
                realsense_frame_0,
                realsense_frame_1,
                motors_frame_0,
                motors_frame_1,
            ):
                writer.write_sensor_frame(frame)

            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=0,
                    aligned_time=1.0,
                    frames={
                        "ft": ft_frame_0,
                        "imu": imu_frame_0,
                        "realsense": realsense_frame_0,
                        "motors": motors_frame_0,
                    },
                    missing_sensors=[],
                    age_by_sensor={"ft": 0.0, "imu": 0.0, "realsense": 0.0, "motors": 0.0},
                    metadata={
                        "gravity_compensation": {
                            "applied": True,
                            "pure_force": [0.7, 1.8, 0.4],
                            "gravity_force": [0.0, 0.0, -2.45],
                        }
                    },
                )
            )
            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=1,
                    aligned_time=1.1,
                    frames={
                        "ft": ft_frame_1,
                        "imu": imu_frame_1,
                        "realsense": realsense_frame_1,
                        "motors": motors_frame_1,
                    },
                    missing_sensors=[],
                    age_by_sensor={"ft": 0.0, "imu": 0.0, "realsense": 0.0, "motors": 0.0},
                )
            )
            writer.write_trajectory_frame(
                TrajectoryFrame(
                    source="orbslam3",
                    frame_id=1,
                    time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                    position=[1.0, 2.0, 3.0],
                    quaternion=[1.0, 0.0, 0.0, 0.0],
                    tracking_state="OK",
                )
            )
            writer.write_trajectory_frame(
                TrajectoryFrame(
                    source="orbslam3",
                    frame_id=2,
                    time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
                    position=[1.5, 2.25, 3.5],
                    quaternion=[0.7071067811865476, 0.0, 0.0, 0.7071067811865476],
                    tracking_state="OK",
                )
            )
            writer.close()

            rlds_result = RLDSSessionExporter().export(session.output_dir)
            lerobot_result = LeRobotSessionExporter().export(session.output_dir)

            self.assertTrue((rlds_result.output_path / "dataset_info.json").exists())
            self.assertTrue((rlds_result.output_path / "episodes" / "episode_000000.json").exists())
            self.assertTrue((lerobot_result.output_path / "meta" / "info.json").exists())
            self.assertTrue((lerobot_result.output_path / "data" / "chunk-000" / "file-000.parquet").exists())

            rlds_payload = json.loads((rlds_result.output_path / "episodes" / "episode_000000.json").read_text(encoding="utf-8"))
            step = rlds_payload["steps"][0]
            self.assertEqual(step["observation"]["ft"]["payload"]["force"], [1.0, 2.0, 3.0])
            self.assertEqual(step["observation"]["gravity_compensation"]["pure_force"], [0.7, 1.8, 0.4])
            self.assertEqual(step["observation"]["trajectory"]["position"], [1.0, 2.0, 3.0])

            import pyarrow.parquet as pq
            table = pq.read_table(lerobot_result.output_path / "data" / "chunk-000" / "file-000.parquet")
            rows = table.to_pylist()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            info_payload = json.loads((lerobot_result.output_path / "meta" / "info.json").read_text(encoding="utf-8"))
            self.assertEqual(row["observation.ft.force.0"], 1.0)
            self.assertEqual(row["observation.gravity_compensation.pure_force.2"], 0.4)
            self.assertEqual(row["observation.trajectory.position.1"], 2.0)
            self.assertEqual(row["observation.motors.motor_2.position"], 4.0)
            self.assertEqual(len(row["action"]), 8)
            self.assertGreater(len(row["observation.state"]), 0)
            self.assertAlmostEqual(row["action.ee_delta.position.0"], 0.5)
            self.assertAlmostEqual(row["action.ee_delta.position.1"], 0.25)
            self.assertAlmostEqual(row["action.ee_delta.position.2"], 0.5)
            self.assertAlmostEqual(row["action.ee_delta.quaternion.0"], 0.7071067811865476)
            self.assertAlmostEqual(row["action.ee_delta.quaternion.1"], 0.0)
            self.assertAlmostEqual(row["action.ee_delta.quaternion.2"], 0.0)
            self.assertAlmostEqual(row["action.ee_delta.quaternion.3"], 0.7071067811865476)
            self.assertAlmostEqual(row["action.gripper.position_delta"], 0.3)
            self.assertIn("observation.images.realsense_depth", row)
            self.assertEqual(info_payload["features"]["action"]["dtype"], "float32")
            self.assertEqual(info_payload["features"]["observation.state"]["dtype"], "float32")
            self.assertEqual(info_payload["features"]["observation.images.realsense_depth"]["dtype"], "image")
            self.assertTrue((lerobot_result.output_path / row["observation.images.realsense_depth"]["path"]).exists())

    def test_lerobot_export_skips_ineligible_action_samples(self) -> None:
        def build_session(
            output_root: str,
            *,
            include_next_trajectory: bool = True,
            next_tracking_state: str = "OK",
            include_next_motor_2: bool = True,
        ) -> Path:
            session = create_session_info(
                output_root,
                task_name="ineligible_action",
                sensors={
                    "realsense": {"sensor_type": "realsense", "modality": "rgbd"},
                    "motors": {"sensor_type": "motors_sensor", "modality": "motor_state"},
                },
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)

            realsense_frame_0 = SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=1.0, device_time=1.0),
                payload={"depth": np.full((2, 2), 1000, dtype=np.uint16)},
            )
            realsense_frame_1 = SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=2,
                time=FrameTime(host_time=1.1, monotonic_time=1.1, device_time=1.1),
                payload={"depth": np.full((2, 2), 1010, dtype=np.uint16)},
            )
            motors_frame_0 = SensorFrame(
                sensor_name="motors",
                sensor_type="motors_sensor",
                modality="motor_state",
                frame_id=10,
                time=FrameTime(host_time=1.0, monotonic_time=1.0, device_time=1.0),
                payload={"motor_2": {"position": 3.0, "velocity": 0.0, "torque": 0.0}},
            )
            motors_payload_1 = {}
            if include_next_motor_2:
                motors_payload_1["motor_2"] = {"position": 3.2, "velocity": 0.0, "torque": 0.0}
            motors_frame_1 = SensorFrame(
                sensor_name="motors",
                sensor_type="motors_sensor",
                modality="motor_state",
                frame_id=11,
                time=FrameTime(host_time=1.1, monotonic_time=1.1, device_time=1.1),
                payload=motors_payload_1,
            )

            for frame in (realsense_frame_0, realsense_frame_1, motors_frame_0, motors_frame_1):
                writer.write_sensor_frame(frame)

            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=0,
                    aligned_time=1.0,
                    frames={"realsense": realsense_frame_0, "motors": motors_frame_0},
                    missing_sensors=[],
                    age_by_sensor={"realsense": 0.0, "motors": 0.0},
                )
            )
            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=1,
                    aligned_time=1.1,
                    frames={"realsense": realsense_frame_1, "motors": motors_frame_1},
                    missing_sensors=[],
                    age_by_sensor={"realsense": 0.0, "motors": 0.0},
                )
            )
            writer.write_trajectory_frame(
                TrajectoryFrame(
                    source="orbslam3",
                    frame_id=1,
                    time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                    position=[0.0, 0.0, 0.0],
                    quaternion=[1.0, 0.0, 0.0, 0.0],
                    tracking_state="OK",
                )
            )
            if include_next_trajectory:
                writer.write_trajectory_frame(
                    TrajectoryFrame(
                        source="orbslam3",
                        frame_id=2,
                        time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
                        position=[0.1, 0.0, 0.0],
                        quaternion=[1.0, 0.0, 0.0, 0.0],
                        tracking_state=next_tracking_state,
                    )
                )
            writer.close()
            return session.output_dir

        with tempfile.TemporaryDirectory() as tmp_dir:
            single_step_session = create_session_info(
                tmp_dir,
                task_name="single_step",
                sensors={
                    "realsense": {"sensor_type": "realsense", "modality": "rgbd"},
                    "motors": {"sensor_type": "motors_sensor", "modality": "motor_state"},
                },
                config={"align_rate_hz": 30},
            )
            single_step_writer = SessionWriter(single_step_session)
            realsense_frame = SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=1.0, device_time=1.0),
                payload={"depth": np.full((2, 2), 1000, dtype=np.uint16)},
            )
            motors_frame = SensorFrame(
                sensor_name="motors",
                sensor_type="motors_sensor",
                modality="motor_state",
                frame_id=2,
                time=FrameTime(host_time=1.0, monotonic_time=1.0, device_time=1.0),
                payload={"motor_2": {"position": 3.0, "velocity": 0.0, "torque": 0.0}},
            )
            single_step_writer.write_sensor_frame(realsense_frame)
            single_step_writer.write_sensor_frame(motors_frame)
            single_step_writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=0,
                    aligned_time=1.0,
                    frames={"realsense": realsense_frame, "motors": motors_frame},
                    missing_sensors=[],
                    age_by_sensor={"realsense": 0.0, "motors": 0.0},
                )
            )
            single_step_writer.write_trajectory_frame(
                TrajectoryFrame(
                    source="orbslam3",
                    frame_id=1,
                    time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                    position=[0.0, 0.0, 0.0],
                    quaternion=[1.0, 0.0, 0.0, 0.0],
                    tracking_state="OK",
                )
            )
            single_step_writer.close()

            import pyarrow.parquet as pq

            result = LeRobotSessionExporter().export(single_step_session.output_dir)
            rows = pq.read_table(result.output_path / "data" / "chunk-000" / "file-000.parquet").to_pylist()
            self.assertEqual(rows, [])

        scenarios = [
            ("缺少下一步轨迹", {"include_next_trajectory": False}),
            ("缺少下一步 motor_2", {"include_next_motor_2": False}),
            ("轨迹状态不是 OK", {"next_tracking_state": "LOST"}),
        ]
        for label, overrides in scenarios:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    session_dir = build_session(tmp_dir, **overrides)
                    result = LeRobotSessionExporter().export(session_dir)
                    import pyarrow.parquet as pq

                    rows = pq.read_table(result.output_path / "data" / "chunk-000" / "file-000.parquet").to_pylist()
                    self.assertEqual(rows, [])

    def test_lerobot_merge_export_batches_sessions_with_split_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            session_a = self._create_lerobot_merge_session(
                tmp_dir,
                session_name="session_alpha",
                task_name="task_pick",
                num_steps=3,
            )
            session_b = self._create_lerobot_merge_session(
                tmp_dir,
                session_name="session_beta",
                task_name="task_place",
                num_steps=2,
            )
            session_c = self._create_lerobot_merge_session(
                tmp_dir,
                session_name="session_gamma",
                task_name="task_pick",
                num_steps=2,
            )
            self._create_lerobot_merge_session(
                tmp_dir,
                session_name="session_missing_trajectory",
                task_name="task_skip",
                num_steps=2,
                include_final_trajectory=False,
            )
            self._create_lerobot_merge_session(
                tmp_dir,
                session_name="session_missing_gripper",
                task_name="task_skip",
                num_steps=2,
                include_final_motor_2=False,
            )
            (root / "not_a_session").mkdir()

            result = LeRobotSessionExporter().export_sessions_dir(root)

            import pyarrow.parquet as pq

            rows = pq.read_table(result.output_path / "data" / "chunk-000" / "file-000.parquet").to_pylist()
            episodes = pq.read_table(result.output_path / "meta" / "episodes" / "chunk-000" / "file-000.parquet").to_pylist()
            info_payload = json.loads((result.output_path / "meta" / "info.json").read_text(encoding="utf-8"))
            merge_report = json.loads((result.output_path / "meta" / "merge_report.json").read_text(encoding="utf-8"))
            tasks = [
                json.loads(line)
                for line in (result.output_path / "meta" / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(info_payload["total_episodes"], 3)
            self.assertEqual(len(episodes), 3)
            self.assertEqual(info_payload["total_frames"], len(rows))
            self.assertEqual(sum(item["length"] for item in episodes), len(rows))
            self.assertEqual({row["index"] for row in rows}, set(range(len(rows))))
            self.assertEqual({episode["episode_index"] for episode in episodes}, {0, 1, 2})
            self.assertEqual({task["task"] for task in tasks}, {"task_pick", "task_place"})
            self.assertEqual(info_payload["total_tasks"], 2)

            grouped_rows: dict[int, list[dict[str, object]]] = {}
            for row in rows:
                grouped_rows.setdefault(int(row["episode_index"]), []).append(row)
                self.assertIn("source_session_id", row)
            for episode_index, grouped in grouped_rows.items():
                grouped.sort(key=lambda item: int(item["frame_index"]))
                self.assertEqual(
                    [int(item["frame_index"]) for item in grouped],
                    list(range(len(grouped))),
                )
                self.assertEqual({item["task_index"] for item in grouped}, {episodes[episode_index]["task_index"]})

            expected_ranges = {"train": [], "val": [], "test": []}
            for episode in episodes:
                split_name = _split_for_session_id(str(episode["source_session_id"]))
                expected_ranges[split_name].append(int(episode["episode_index"]))
            for split_name, indices in expected_ranges.items():
                range_text = info_payload["splits"][split_name]
                start_text, end_text = range_text.split(":")
                self.assertEqual(indices, list(range(int(start_text), int(end_text))))

            self.assertEqual(merge_report["valid_sessions"], 3)
            self.assertEqual(merge_report["scanned_directories"], 6)
            skipped_reasons = {item["reason"] for item in merge_report["skipped"]}
            self.assertIn("missing_trajectory", skipped_reasons)
            self.assertIn("no_valid_rows", skipped_reasons)
            self.assertIn("not_a_session_dir", skipped_reasons)

            source_ids = {episode["source_session_id"] for episode in episodes}
            self.assertEqual(
                source_ids,
                {
                    SessionReader(session_a).manifest.session_id,
                    SessionReader(session_b).manifest.session_id,
                    SessionReader(session_c).manifest.session_id,
                },
            )

    @unittest.skipIf(av is None or cv2 is None, "未安装 PyAV 或 opencv-python")
    def test_lerobot_merge_export_preserves_unique_media_and_cli_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._create_lerobot_merge_session(
                tmp_dir,
                session_name="session_media_one",
                task_name="task_media",
                include_camera=True,
                include_microphone=True,
            )
            self._create_lerobot_merge_session(
                tmp_dir,
                session_name="session_media_two",
                task_name="task_media",
                include_camera=True,
                include_microphone=True,
            )

            repo_root = Path(__file__).resolve().parents[1]
            cli_result = subprocess.run(
                [
                    sys.executable,
                    str(repo_root / "scripts" / "sdk_export.py"),
                    str(root),
                    "--format",
                    "lerobot",
                    "--merge-sessions",
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_root),
            )
            self.assertEqual(cli_result.returncode, 0, msg=cli_result.stderr)

            import pyarrow.parquet as pq

            output_path = root / "exports" / "lerobot_merged"
            rows = pq.read_table(output_path / "data" / "chunk-000" / "file-000.parquet").to_pylist()
            image_paths = [row["observation.images.camera"]["path"] for row in rows]
            audio_paths = [row["observation.audios.microphone"]["path"] for row in rows]
            self.assertEqual(len(image_paths), len(set(image_paths)))
            self.assertEqual(len(audio_paths), len(set(audio_paths)))
            for relative_path in image_paths + audio_paths:
                self.assertTrue((output_path / relative_path).exists())

    def test_export_csv_snapshot_with_compensation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                task_name="csv_snapshot",
                sensors={
                    "ft": {"sensor_type": "ft_sensor", "modality": "force_torque"},
                    "imu": {"sensor_type": "imu_sensor", "modality": "imu"},
                    "realsense": {"sensor_type": "realsense", "modality": "rgbd"},
                    "camera": {"sensor_type": "camera_sensor", "modality": "rgb"},
                },
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)
            ft_frame = SensorFrame(
                sensor_name="ft",
                sensor_type="ft_sensor",
                modality="force_torque",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=1.0),
                payload={
                    "force_torque": [1.0, 2.0, 3.0, 0.1, 0.2, 0.3],
                    "force": [1.0, 2.0, 3.0],
                    "torque": [0.1, 0.2, 0.3],
                },
            )
            imu_frame = SensorFrame(
                sensor_name="imu",
                sensor_type="imu_sensor",
                modality="imu",
                frame_id=2,
                time=FrameTime(host_time=1.01, monotonic_time=1.01),
                payload={
                    "acceleration": [0.1, 0.2, 9.81],
                    "angular_velocity": [0.01, 0.02, 0.03],
                    "quaternion": [1.0, 0.0, 0.0, 0.0],
                },
            )
            realsense_frame = SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=3,
                time=FrameTime(host_time=1.02, monotonic_time=1.02),
                payload={"color": np.zeros((2, 2, 3), dtype=np.uint8)},
            )
            camera_frame = SensorFrame(
                sensor_name="camera",
                sensor_type="camera_sensor",
                modality="rgb",
                frame_id=4,
                time=FrameTime(host_time=1.03, monotonic_time=1.03),
                payload={"color": np.zeros((2, 2, 3), dtype=np.uint8)},
            )
            writer.write_sensor_frame(ft_frame)
            writer.write_sensor_frame(imu_frame)
            writer.write_sensor_frame(realsense_frame)
            writer.write_sensor_frame(camera_frame)
            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=0,
                    aligned_time=1.05,
                    frames={
                        "ft": ft_frame,
                        "imu": imu_frame,
                        "realsense": realsense_frame,
                        "camera": camera_frame,
                    },
                    missing_sensors=[],
                    age_by_sensor={"ft": 0.05, "imu": 0.04, "realsense": 0.03, "camera": 0.02},
                    metadata={
                        "gravity_compensation": {
                            "applied": True,
                            "pure_force": [0.7, 1.8, 0.4],
                            "gravity_force": [0.0, 0.0, -2.45],
                            "bias": [0.3, 0.2, 0.15],
                        }
                    },
                )
            )
            writer.close()

            result = CSVSnapshotExporter().export(session.output_dir)

            lines = result.output_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            header = lines[0].split(",")
            self.assertIn("Fx_Pure", header)
            self.assertIn("Gravity_Fz", header)
            self.assertIn("RealSense_Frame_ID", header)
            self.assertIn("Camera_Frame_ID", header)
            self.assertIn("Missing_Sensors", header)
            self.assertIn("1.050000", lines[1])


if __name__ == "__main__":
    unittest.main()
