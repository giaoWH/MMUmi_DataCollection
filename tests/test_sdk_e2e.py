import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sdk.storage import SessionReader


try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import h5py
except ImportError:  # pragma: no cover
    h5py = None


class SDKEndToEndTest(unittest.TestCase):
    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    @unittest.skipIf(h5py is None, "未安装 h5py")
    def test_fake_record_to_trajectory_and_exports(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "sessions"
            bundle_dir = Path(tmp_dir) / "bundle"
            config_path = Path(tmp_dir) / "record_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "output_root": str(output_root),
                        "sensor_source": "fake",
                        "align_rate_hz": 20,
                        "duration_sec": 0.2,
                        "gravity_compensation": {
                            "enabled": True,
                            "mass": 0.25,
                            "com": [0.0, 0.0, 0.1],
                            "stabilization_sec": 0.0,
                            "calibration_duration_sec": 0.2,
                            "minimum_samples": 5,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            record_result = subprocess.run(
                [
                    sys.executable,
                    str(repo_root / "scripts" / "sdk_record.py"),
                    "--config",
                    str(config_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_root),
            )
            self.assertEqual(record_result.returncode, 0, msg=record_result.stderr)

            sessions = sorted(output_root.glob("session_*"))
            self.assertEqual(len(sessions), 1)
            session_dir = sessions[0]

            inspect_result = subprocess.run(
                [
                    sys.executable,
                    str(repo_root / "scripts" / "sdk_inspect.py"),
                    str(session_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_root),
            )
            self.assertEqual(inspect_result.returncode, 0, msg=inspect_result.stderr)
            summary = json.loads(inspect_result.stdout)
            self.assertIn("ft", summary["sensor_names"])
            self.assertIn("imu", summary["sensor_names"])
            self.assertIn("realsense", summary["sensor_names"])
            self.assertTrue((session_dir / "logs" / "sdk_record.log").exists())

            reader = SessionReader(session_dir)
            aligned_records = list(reader.iter_aligned_records())
            self.assertTrue(aligned_records)
            self.assertTrue(
                any("gravity_compensation" in record.get("metadata", {}) for record in aligned_records),
                "录制结果中缺少重力补偿记录",
            )

            orb_script = (
                "import json, pathlib; "
                "manifest = json.loads(pathlib.Path(r'{bundle_manifest}').read_text(encoding='utf-8')); "
                "payload = {"
                "'timestamp': 5.0, "
                "'position': [manifest['frame_count'], manifest['imu_rows'], 1.0], "
                "'quaternion': [1.0, 0.0, 0.0, 0.0], "
                "'tracking_state': 'OK'"
                "}; "
                "print(json.dumps(payload, ensure_ascii=False))"
            )
            process_result = subprocess.run(
                [
                    sys.executable,
                    str(repo_root / "scripts" / "sdk_process_trajectory.py"),
                    str(session_dir),
                    "--command",
                    f"{sys.executable} -c \"{orb_script}\"",
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
                cwd=str(repo_root),
            )
            self.assertEqual(process_result.returncode, 0, msg=process_result.stderr)

            for export_format in ("hdf5", "rlds", "lerobot"):
                export_result = subprocess.run(
                    [
                        sys.executable,
                        str(repo_root / "scripts" / "sdk_export.py"),
                        str(session_dir),
                        "--format",
                        export_format,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=str(repo_root),
                )
                self.assertEqual(export_result.returncode, 0, msg=export_result.stderr)
                if export_format != "hdf5":
                    validate_result = subprocess.run(
                        [
                            sys.executable,
                            str(repo_root / "scripts" / "sdk_validate_export.py"),
                            str(session_dir),
                            "--format",
                            export_format,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        cwd=str(repo_root),
                    )
                    self.assertEqual(validate_result.returncode, 0, msg=validate_result.stderr)

            csv_export_result = subprocess.run(
                [
                    sys.executable,
                    str(repo_root / "scripts" / "sdk_export.py"),
                    str(session_dir),
                    "--format",
                    "csv",
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_root),
            )
            self.assertEqual(csv_export_result.returncode, 0, msg=csv_export_result.stderr)

            csv_validate_result = subprocess.run(
                [
                    sys.executable,
                    str(repo_root / "scripts" / "sdk_validate_export.py"),
                    str(session_dir),
                    "--format",
                    "csv",
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_root),
            )
            self.assertEqual(csv_validate_result.returncode, 0, msg=csv_validate_result.stderr)
