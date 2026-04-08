import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from sdk.core.frame import AlignedFrame, FrameTime, SensorFrame, TrajectoryFrame
from sdk.core.session import create_session_info
from sdk.quality import TrajectoryQcConfig, TrajectoryQcThresholds, run_trajectory_qc, write_trajectory_qc_report
from sdk.storage import SessionReader, SessionWriter


class TrajectoryQcTest(unittest.TestCase):
    def _create_session(
        self,
        output_root: str,
        *,
        aligned_times: list[float] | None = None,
        positions: list[list[float]] | None = None,
        grippers: list[float] | None = None,
        tracking_states: list[str] | None = None,
        trajectory_write_order: list[int] | None = None,
        missing_trajectory_indices: set[int] | None = None,
        missing_gripper_indices: set[int] | None = None,
    ) -> Path:
        aligned_times = aligned_times or [1.0, 1.1, 1.2, 1.3]
        positions = positions or [
            [0.0, 0.0, 0.0],
            [0.01, 0.0, 0.0],
            [0.02, 0.0, 0.0],
            [0.03, 0.0, 0.0],
        ]
        grippers = grippers or [0.0, 0.01, 0.02, 0.03]
        tracking_states = tracking_states or ["OK"] * len(aligned_times)
        trajectory_write_order = trajectory_write_order or list(range(len(aligned_times)))
        missing_trajectory_indices = missing_trajectory_indices or set()
        missing_gripper_indices = missing_gripper_indices or set()

        session = create_session_info(
            output_root,
            sensors={
                "realsense": {"sensor_type": "realsense", "modality": "rgbd"},
                "motors": {"sensor_type": "motors_sensor", "modality": "motor_state"},
            },
            config={"align_rate_hz": 10},
        )
        writer = SessionWriter(session)
        realsense_frames: list[SensorFrame] = []
        motors_frames: list[SensorFrame] = []
        for index, aligned_time in enumerate(aligned_times):
            realsense_frame = SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=index + 1,
                time=FrameTime(
                    host_time=aligned_time,
                    monotonic_time=aligned_time,
                    aligned_time=aligned_time,
                    device_time=aligned_time,
                ),
                payload={"depth": np.full((2, 2), 1000 + index, dtype=np.uint16)},
            )
            motor_payload = (
                {}
                if index in missing_gripper_indices
                else {"motor_2": {"position": grippers[index], "velocity": 0.0, "torque": 0.0}}
            )
            motors_frame = SensorFrame(
                sensor_name="motors",
                sensor_type="motors_sensor",
                modality="motor_state",
                frame_id=index + 101,
                time=FrameTime(
                    host_time=aligned_time,
                    monotonic_time=aligned_time,
                    aligned_time=aligned_time,
                    device_time=aligned_time,
                ),
                payload=motor_payload,
            )
            writer.write_sensor_frame(realsense_frame)
            writer.write_sensor_frame(motors_frame)
            realsense_frames.append(realsense_frame)
            motors_frames.append(motors_frame)

        for index, aligned_time in enumerate(aligned_times):
            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=index,
                    aligned_time=aligned_time,
                    frames={"realsense": realsense_frames[index], "motors": motors_frames[index]},
                    missing_sensors=[],
                    age_by_sensor={"realsense": 0.0, "motors": 0.0},
                )
            )

        for index in trajectory_write_order:
            if index in missing_trajectory_indices:
                continue
            aligned_time = aligned_times[index]
            writer.write_trajectory_frame(
                TrajectoryFrame(
                    source="orbslam3",
                    frame_id=index + 1,
                    time=FrameTime(
                        host_time=aligned_time,
                        monotonic_time=aligned_time,
                        aligned_time=aligned_time,
                    ),
                    position=positions[index],
                    quaternion=[1.0, 0.0, 0.0, 0.0],
                    tracking_state=tracking_states[index],
                )
            )
        writer.close()
        return session.output_dir

    def test_run_trajectory_qc_passes_smooth_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)

            result = run_trajectory_qc(session_dir)

            self.assertEqual(result.session_status, "pass")
            self.assertEqual(result.report["summary"]["pass_step_count"], 3)
            self.assertEqual(result.report["summary"]["warn_step_count"], 0)
            self.assertEqual(result.report["summary"]["fail_step_count"], 0)
            self.assertEqual(result.report["summary"]["coverage_ratio"], 1.0)
            self.assertEqual(result.report["summary"]["action_eligible_ratio"], 1.0)

    def test_run_trajectory_qc_fails_when_trajectory_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir, missing_trajectory_indices={0, 1, 2, 3})

            result = run_trajectory_qc(session_dir)

            self.assertEqual(result.session_status, "fail")
            self.assertIn("missing_trajectory_file", result.report["session_flags"])
            self.assertIn("low_trajectory_coverage", result.report["session_flags"])
            self.assertIn("low_action_eligible_ratio", result.report["session_flags"])
            self.assertEqual(result.report["summary"]["fail_step_count"], 3)
            self.assertIn("missing_current_trajectory", result.report["step_results"][0]["flags"])

    def test_run_trajectory_qc_fails_when_trajectory_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir, missing_trajectory_indices={0, 1, 2, 3})
            trajectory_path = Path(session_dir) / "trajectory" / "frames.jsonl"
            trajectory_path.parent.mkdir(parents=True, exist_ok=True)
            trajectory_path.write_text("", encoding="utf-8")

            result = run_trajectory_qc(session_dir)

            self.assertEqual(result.session_status, "fail")
            self.assertIn("empty_trajectory", result.report["session_flags"])

    def test_run_trajectory_qc_fails_on_non_monotonic_trajectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir, trajectory_write_order=[0, 2, 1, 3])

            result = run_trajectory_qc(session_dir)

            self.assertEqual(result.session_status, "fail")
            self.assertIn("non_monotonic_trajectory", result.report["session_flags"])
            self.assertEqual(result.report["summary"]["coverage_ratio"], 1.0)

    def test_run_trajectory_qc_fails_when_coverage_too_low(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir, missing_trajectory_indices={3})

            result = run_trajectory_qc(session_dir)

            self.assertEqual(result.session_status, "fail")
            self.assertIn("low_trajectory_coverage", result.report["session_flags"])
            self.assertAlmostEqual(result.report["summary"]["coverage_ratio"], 0.75)

    def test_run_trajectory_qc_marks_missing_gripper_step_as_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir, missing_gripper_indices={2})

            result = run_trajectory_qc(
                session_dir,
                TrajectoryQcConfig(
                    thresholds=TrajectoryQcThresholds(
                        min_trajectory_coverage_ratio=0.95,
                        min_action_eligible_ratio=0.5,
                        max_fail_step_ratio=1.0,
                    )
                ),
            )

            self.assertEqual(result.session_status, "fail")
            self.assertIn("low_action_eligible_ratio", result.report["session_flags"])
            failing_steps = [item for item in result.report["step_results"] if item["status"] == "fail"]
            self.assertEqual(len(failing_steps), 2)
            self.assertIn("missing_next_gripper", failing_steps[0]["flags"])
            self.assertIn("missing_current_gripper", failing_steps[1]["flags"])

    def test_run_trajectory_qc_warns_on_large_jump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(
                tmp_dir,
                positions=[
                    [0.0, 0.0, 0.0],
                    [0.01, 0.0, 0.0],
                    [0.51, 0.0, 0.0],
                    [0.52, 0.0, 0.0],
                ],
            )

            result = run_trajectory_qc(session_dir)

            self.assertEqual(result.session_status, "warn")
            warned_steps = [item for item in result.report["step_results"] if item["status"] == "warn"]
            self.assertEqual(len(warned_steps), 1)
            self.assertIn("translation_delta_exceeds_threshold", warned_steps[0]["flags"])
            self.assertIn("translation_speed_exceeds_threshold", warned_steps[0]["flags"])

    def test_sdk_qc_trajectory_cli_writes_report_and_sdk_inspect_reads_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            repo_root = Path(__file__).resolve().parents[1]
            config_path = Path(tmp_dir) / "qc_config.json"
            config_path.write_text(
                json.dumps({"trajectory_qc": {"enabled": True}}, ensure_ascii=False),
                encoding="utf-8",
            )

            qc_result = subprocess.run(
                [
                    sys.executable,
                    str(repo_root / "scripts" / "sdk_qc_trajectory.py"),
                    str(session_dir),
                    "--config",
                    str(config_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_root),
            )
            self.assertEqual(qc_result.returncode, 0, msg=qc_result.stderr)
            qc_payload = json.loads(qc_result.stdout)
            self.assertEqual(qc_payload["session_status"], "pass")
            report_path = Path(qc_payload["output_path"])
            self.assertTrue(report_path.exists())

            inspect_result = subprocess.run(
                [sys.executable, str(repo_root / "scripts" / "sdk_inspect.py"), str(session_dir)],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_root),
            )
            self.assertEqual(inspect_result.returncode, 0, msg=inspect_result.stderr)
            inspect_payload = json.loads(inspect_result.stdout)
            self.assertIn("trajectory_qc_summary", inspect_payload)
            self.assertEqual(inspect_payload["trajectory_qc_summary"]["session_status"], "pass")
            self.assertEqual(
                inspect_payload["trajectory_qc_summary"]["path"],
                "quality/trajectory_qc.json",
            )

    def test_sdk_qc_trajectory_cli_returns_exit_code_one_on_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir, missing_trajectory_indices={0, 1, 2, 3})
            repo_root = Path(__file__).resolve().parents[1]
            config_path = Path(tmp_dir) / "qc_config.json"
            config_path.write_text(
                json.dumps({"trajectory_qc": {"enabled": True}}, ensure_ascii=False),
                encoding="utf-8",
            )

            qc_result = subprocess.run(
                [
                    sys.executable,
                    str(repo_root / "scripts" / "sdk_qc_trajectory.py"),
                    str(session_dir),
                    "--config",
                    str(config_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_root),
            )
            self.assertEqual(qc_result.returncode, 1)
            payload = json.loads(qc_result.stdout)
            self.assertEqual(payload["session_status"], "fail")

    def test_session_reader_summary_omits_qc_when_report_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)

            summary = SessionReader(session_dir).summary()

            self.assertNotIn("trajectory_qc_summary", summary)

    def test_write_trajectory_qc_report_persists_full_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)

            result = run_trajectory_qc(session_dir)
            output_path = write_trajectory_qc_report(result)

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIn("step_results", payload)
            self.assertEqual(payload["session_status"], "pass")
