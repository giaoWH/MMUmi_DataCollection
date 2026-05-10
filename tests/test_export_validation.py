import tempfile
import unittest
from pathlib import Path

import numpy as np

from sdk.core.frame import AlignedFrame, FrameTime, SensorFrame, TrajectoryFrame
from sdk.core.session import create_session_info
from sdk.exporters.csv_exporter import CSVSnapshotExporter
from sdk.exporters.lerobot_exporter import LeRobotSessionExporter
from sdk.exporters.rlds_exporter import RLDSSessionExporter
from sdk.exporters.validation import SessionExportValidator
from sdk.storage.session_writer import SessionWriter

try:
    import h5py  # noqa: F401
except ImportError:  # pragma: no cover
    h5py = None
else:
    from sdk.exporters.hdf5_exporter import HDF5SessionExporter


class SessionExportValidationTest(unittest.TestCase):
    def _create_session(self, output_root: str) -> Path:
        session = create_session_info(
            output_root,
            task_name="export_validation",
            sensors={
                "ft": {"sensor_type": "ft_sensor", "modality": "force_torque"},
                "imu": {"sensor_type": "imu_sensor", "modality": "imu"},
                "realsense": {"sensor_type": "realsense", "modality": "rgbd"},
                "camera": {"sensor_type": "camera_sensor", "modality": "rgb"},
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
                time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                payload={"force_torque": [1.0, 2.0, 3.0, 0.1, 0.2, 0.3], "force": [1.0, 2.0, 3.0]},
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
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                payload={
                    "color": np.full((2, 2, 3), 16, dtype=np.uint8),
                    "depth": np.full((2, 2), 1200, dtype=np.uint16),
                },
                metadata={"intrinsics": {"color": {"fx": 1.0, "fy": 1.0}}},
            )
        )
        writer.write_sensor_frame(
            SensorFrame(
                sensor_name="camera",
                sensor_type="camera_sensor",
                modality="rgb",
                frame_id=1,
                time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                payload={"color": np.full((2, 2, 3), 32, dtype=np.uint8)},
                metadata={"device_index": 0},
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
                        time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                        payload={"force_torque": [1.0, 2.0, 3.0, 0.1, 0.2, 0.3], "force": [1.0, 2.0, 3.0]},
                    ),
                    "imu": SensorFrame(
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
                    ),
                    "realsense": SensorFrame(
                        sensor_name="realsense",
                        sensor_type="realsense",
                        modality="rgbd",
                        frame_id=1,
                        time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                        payload={},
                    ),
                    "camera": SensorFrame(
                        sensor_name="camera",
                        sensor_type="camera_sensor",
                        modality="rgb",
                        frame_id=1,
                        time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                        payload={},
                    ),
                },
                missing_sensors=[],
                age_by_sensor={"ft": 0.0, "imu": 0.0, "realsense": 0.0, "camera": 0.0},
                metadata={},
            )
        )
        writer.close()
        return session.output_dir

    def _create_session_with_lerobot_action(self, output_root: str) -> Path:
        session = create_session_info(
            output_root,
            task_name="export_validation_lerobot",
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
            payload={"motor_2": {"position": 2.0, "velocity": 0.0, "torque": 0.0}},
        )
        motors_frame_1 = SensorFrame(
            sensor_name="motors",
            sensor_type="motors_sensor",
            modality="motor_state",
            frame_id=11,
            time=FrameTime(host_time=1.1, monotonic_time=1.1, device_time=1.1),
            payload={"motor_2": {"position": 2.2, "velocity": 0.0, "torque": 0.0}},
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
        writer.write_trajectory_frame(
            TrajectoryFrame(
                source="orbslam3",
                frame_id=2,
                time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
                position=[0.1, 0.0, 0.0],
                quaternion=[1.0, 0.0, 0.0, 0.0],
                tracking_state="OK",
            )
        )
        writer.close()
        return session.output_dir

    def test_validator_accepts_csv_rlds_lerobot_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)

            csv_result = CSVSnapshotExporter().export(session_dir)
            csv_header = csv_result.output_path.read_text(encoding="utf-8").splitlines()[0].split(",")
            rlds_result = RLDSSessionExporter().export(session_dir)
            lerobot_result = LeRobotSessionExporter().export(session_dir)

            self.assertIn("Camera_Frame_ID", csv_header)
            validator = SessionExportValidator(session_dir)
            self.assertEqual(validator.validate("csv", csv_result.output_path).issues, [])
            self.assertEqual(validator.validate("rlds", rlds_result.output_path).issues, [])
            self.assertEqual(validator.validate("lerobot", lerobot_result.output_path).issues, [])

    def test_validator_accepts_lerobot_action_filtered_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session_with_lerobot_action(tmp_dir)
            lerobot_result = LeRobotSessionExporter().export(session_dir)

            validator = SessionExportValidator(session_dir)
            self.assertEqual(validator.validate("lerobot", lerobot_result.output_path).issues, [])

    @unittest.skipIf(h5py is None, "未安装 h5py")
    def test_validator_accepts_hdf5_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            hdf5_result = HDF5SessionExporter().export(session_dir)

            validator = SessionExportValidator(session_dir)
            self.assertEqual(validator.validate("hdf5", hdf5_result.output_path).issues, [])
