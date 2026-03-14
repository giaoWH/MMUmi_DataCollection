import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from sdk.core.frame import AlignedFrame, FrameTime, SensorFrame, TrajectoryFrame
from sdk.core.session import create_session_info
from sdk.exporters.hdf5_exporter import HDF5SessionExporter
from sdk.exporters.lerobot_exporter import LeRobotSessionExporter
from sdk.exporters.rlds_exporter import RLDSSessionExporter
from sdk.exporters.csv_exporter import CSVSnapshotExporter
from sdk.perception.orbslam3.bundle import OrbSlam3SessionBundleExporter
from sdk.perception.orbslam3.command_runner import OrbSlam3CommandConfig, OrbSlam3CommandRunner
from sdk.perception.orbslam3.pipeline import OrbSlam3Pipeline, OrbSlam3PipelineConfig
from sdk.storage.session_reader import SessionReader
from sdk.storage.session_writer import SessionWriter

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
            sensors={
                "realsense": {"sensor_type": "realsense_rgbd", "modality": "rgbd"},
                "imu": {"sensor_type": "imu_sensor", "modality": "imu"},
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
                time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                payload={
                    "color": np.full((4, 6, 3), 32, dtype=np.uint8),
                    "depth": np.full((4, 6), 1200, dtype=np.uint16),
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
                time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
                payload={
                    "color": np.full((4, 6, 3), 64, dtype=np.uint8),
                    "depth": np.full((4, 6), 1250, dtype=np.uint16),
                },
                metadata={"stream": "rgbd"},
            )
        )
        writer.write_sensor_frame(
            SensorFrame(
                sensor_name="imu",
                sensor_type="imu_sensor",
                modality="imu",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                payload={
                    "acceleration": [0.1, 0.2, 0.3],
                    "angular_velocity": [0.01, 0.02, 0.03],
                    "quaternion": [1.0, 0.0, 0.0, 0.0],
                },
            )
        )
        writer.write_sensor_frame(
            SensorFrame(
                sensor_name="imu",
                sensor_type="imu_sensor",
                modality="imu",
                frame_id=2,
                time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
                payload={
                    "acceleration": [0.4, 0.5, 0.6],
                    "angular_velocity": [0.04, 0.05, 0.06],
                    "quaternion": [0.0, 1.0, 0.0, 0.0],
                },
            )
        )
        writer.close()
        return session.output_dir

    def test_write_sensor_frame_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                sensors={"realsense": {"modality": "rgbd"}},
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)
            frame = SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=2.0),
                payload={
                    "depth": np.ones((2, 2), dtype=np.uint16),
                    "acceleration": np.array([1.0, 2.0, 3.0]),
                },
            )

            writer.write_sensor_frame(frame)

            meta_path = session.output_dir / "meta.json"
            manifest_path = session.output_dir / "manifest.json"
            sensor_log = session.output_dir / "streams" / "realsense" / "frames.jsonl"
            self.assertTrue(meta_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertTrue(sensor_log.exists())

            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["config"]["align_rate_hz"], 30)

            records = sensor_log.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(records), 1)
            record = json.loads(records[0])
            self.assertEqual(record["payload"]["acceleration"], [1.0, 2.0, 3.0])

            depth_path = Path(session.output_dir) / record["payload"]["depth"]["path"]
            self.assertTrue(depth_path.exists())

            reader = SessionReader(session.output_dir)
            loaded_frame = next(reader.iter_sensor_frames("realsense", load_payload=True))
            self.assertEqual(loaded_frame.payload["acceleration"], [1.0, 2.0, 3.0])
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
            self.assertEqual(imu_lines[0], "timestamp,ax,ay,az,gx,gy,gz,qw,qx,qy,qz")
            self.assertEqual(len(imu_lines), 3)

            manifest_payload = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["mode"], "rgbd_inertial")
            self.assertEqual(manifest_payload["frame_count"], 2)
            self.assertEqual(manifest_payload["imu_rows"], 2)

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
                "print(json.dumps(payload, ensure_ascii=False))"
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
                    "rgbd_inertial",
                    "--output-mode",
                    "stdout_jsonl",
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
            self.assertEqual(manifest_payload["notes"]["orbslam3_mode"], "rgbd_inertial")
            self.assertEqual(manifest_payload["notes"]["trajectory_source"], "orbslam3")
            self.assertEqual(manifest_payload["notes"]["orbslam3_bundle"], str(bundle_dir))

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_sdk_process_trajectory_cli_supports_config_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_orbslam3_session(tmp_dir)
            bundle_dir = Path(tmp_dir) / "orb_bundle_config"
            config_path = Path(tmp_dir) / "orbslam3.json"
            script = (
                "import json, os; "
                "payload = {"
                "'timestamp': 4.5, "
                "'position': [4.0, 5.0, 6.0], "
                "'quaternion': [1.0, 0.0, 0.0, 0.0], "
                "'tracking_state': os.environ['ORB_TEST_FLAG']"
                "}; "
                "print(json.dumps(payload, ensure_ascii=False))"
            )
            config_path.write_text(
                json.dumps(
                    {
                        "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
                        "mode": "rgbd_inertial",
                        "output_mode": "stdout_jsonl",
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

    @unittest.skipIf(h5py is None, "未安装 h5py")
    def test_export_hdf5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
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
                sensors={
                    "ft": {"sensor_type": "ft_sensor", "modality": "force_torque"},
                    "imu": {"sensor_type": "imu_sensor", "modality": "imu"},
                },
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)
            writer.write_sensor_frame(
                SensorFrame(
                    sensor_name="ft",
                    sensor_type="ft_sensor",
                    modality="force_torque",
                    frame_id=1,
                    time=FrameTime(host_time=1.0, monotonic_time=2.0),
                    payload={
                        "force_torque": [1.0, 2.0, 3.0, 0.1, 0.2, 0.3],
                        "force": [1.0, 2.0, 3.0],
                        "torque": [0.1, 0.2, 0.3],
                    },
                )
            )
            writer.write_sensor_frame(
                SensorFrame(
                    sensor_name="imu",
                    sensor_type="imu_sensor",
                    modality="imu",
                    frame_id=1,
                    time=FrameTime(host_time=1.0, monotonic_time=2.0),
                    payload={"acceleration": [0.1, 0.2, 0.3]},
                )
            )
            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=0,
                    aligned_time=1.0,
                    frames={
                        "ft": SensorFrame(
                            sensor_name="ft",
                            sensor_type="ft_sensor",
                            modality="force_torque",
                            frame_id=1,
                            time=FrameTime(host_time=1.0, monotonic_time=2.0),
                            payload={
                                "force_torque": [1.0, 2.0, 3.0, 0.1, 0.2, 0.3],
                                "force": [1.0, 2.0, 3.0],
                                "torque": [0.1, 0.2, 0.3],
                            },
                        ),
                        "imu": SensorFrame(
                            sensor_name="imu",
                            sensor_type="imu_sensor",
                            modality="imu",
                            frame_id=1,
                            time=FrameTime(host_time=1.0, monotonic_time=2.0),
                            payload={"acceleration": [0.1, 0.2, 0.3]},
                        )
                    },
                    missing_sensors=[],
                    age_by_sensor={"ft": 0.0, "imu": 0.0},
                    metadata={
                        "gravity_compensation": {
                            "applied": True,
                            "pure_force": [0.7, 1.8, 0.4],
                            "gravity_force": [0.0, 0.0, -2.45],
                        }
                    },
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
            row = table.to_pylist()[0]
            self.assertEqual(row["observation.ft.force.0"], 1.0)
            self.assertEqual(row["observation.gravity_compensation.pure_force.2"], 0.4)
            self.assertEqual(row["observation.trajectory.position.1"], 2.0)

    def test_export_csv_snapshot_with_compensation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                sensors={
                    "ft": {"sensor_type": "ft_sensor", "modality": "force_torque"},
                    "imu": {"sensor_type": "imu_sensor", "modality": "imu"},
                    "realsense": {"sensor_type": "realsense", "modality": "rgbd"},
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
            writer.write_sensor_frame(ft_frame)
            writer.write_sensor_frame(imu_frame)
            writer.write_sensor_frame(realsense_frame)
            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=0,
                    aligned_time=1.05,
                    frames={
                        "ft": ft_frame,
                        "imu": imu_frame,
                        "realsense": realsense_frame,
                    },
                    missing_sensors=[],
                    age_by_sensor={"ft": 0.05, "imu": 0.04, "realsense": 0.03},
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

            result = CSVSnapshotExporter().export(session.output_dir)

            lines = result.output_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            header = lines[0].split(",")
            self.assertIn("Fx_Pure", header)
            self.assertIn("Gravity_Fz", header)
            self.assertIn("RealSense_Frame_ID", header)
            self.assertIn("Missing_Sensors", header)
            self.assertIn("1.050000", lines[1])


if __name__ == "__main__":
    unittest.main()
