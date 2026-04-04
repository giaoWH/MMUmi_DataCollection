import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np

from sdk.annotations import AnnotationSchema, AnnotationService, start_annotation_server
from sdk.core.frame import AlignedFrame, FrameTime, SensorFrame
from sdk.core.session import create_session_info
from sdk.exporters.lerobot_exporter import LeRobotSessionExporter
from sdk.storage import SessionReader, SessionWriter

try:
    import cv2  # noqa: F401
except ImportError:  # pragma: no cover
    cv2 = None
try:
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover
    pq = None


class AnnotationIntegrationTest(unittest.TestCase):
    def _create_session(self, output_root: str) -> Path:
        session = create_session_info(
            output_root,
            sensors={
                "camera": {"sensor_type": "camera_sensor", "modality": "rgb"},
                "ft": {"sensor_type": "ft_sensor", "modality": "force_torque"},
                "imu": {"sensor_type": "imu_sensor", "modality": "imu"},
            },
            config={"align_rate_hz": 30},
        )
        writer = SessionWriter(session)

        camera_frame_0 = SensorFrame(
            sensor_name="camera",
            sensor_type="camera_sensor",
            modality="rgb",
            frame_id=0,
            time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
            payload={"color": np.full((4, 6, 3), 16, dtype=np.uint8)},
        )
        camera_frame_1 = SensorFrame(
            sensor_name="camera",
            sensor_type="camera_sensor",
            modality="rgb",
            frame_id=1,
            time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
            payload={"color": np.full((4, 6, 3), 64, dtype=np.uint8)},
        )
        ft_frame_0 = SensorFrame(
            sensor_name="ft",
            sensor_type="ft_sensor",
            modality="force_torque",
            frame_id=0,
            time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
            payload={"force": [1.0, 2.0, 3.0], "torque": [0.1, 0.2, 0.3]},
        )
        ft_frame_1 = SensorFrame(
            sensor_name="ft",
            sensor_type="ft_sensor",
            modality="force_torque",
            frame_id=1,
            time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
            payload={"force": [4.0, 5.0, 6.0], "torque": [0.4, 0.5, 0.6]},
        )
        imu_frame_0 = SensorFrame(
            sensor_name="imu",
            sensor_type="imu_sensor",
            modality="imu",
            frame_id=0,
            time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
            payload={"acceleration": [0.1, 0.2, 9.8], "angular_velocity": [0.01, 0.02, 0.03]},
        )
        imu_frame_1 = SensorFrame(
            sensor_name="imu",
            sensor_type="imu_sensor",
            modality="imu",
            frame_id=1,
            time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
            payload={"acceleration": [0.2, 0.3, 9.7], "angular_velocity": [0.04, 0.05, 0.06]},
        )

        for frame in (camera_frame_0, camera_frame_1, ft_frame_0, ft_frame_1, imu_frame_0, imu_frame_1):
            writer.write_sensor_frame(frame)

        writer.write_aligned_frame(
            AlignedFrame(
                sequence_id=0,
                aligned_time=1.0,
                frames={"camera": camera_frame_0, "ft": ft_frame_0, "imu": imu_frame_0},
                missing_sensors=[],
                age_by_sensor={"camera": 0.0, "ft": 0.0, "imu": 0.0},
            )
        )
        writer.write_aligned_frame(
            AlignedFrame(
                sequence_id=1,
                aligned_time=1.1,
                frames={"camera": camera_frame_1, "ft": ft_frame_1, "imu": imu_frame_1},
                missing_sensors=[],
                age_by_sensor={"camera": 0.0, "ft": 0.0, "imu": 0.0},
            )
        )
        writer.close()
        return session.output_dir

    def _create_session_with_audio(self, output_root: str) -> Path:
        session = create_session_info(
            output_root,
            sensors={
                "camera": {"sensor_type": "camera_sensor", "modality": "rgb"},
                "ft": {"sensor_type": "ft_sensor", "modality": "force_torque"},
                "imu": {"sensor_type": "imu_sensor", "modality": "imu"},
                "microphone": {"sensor_type": "microphone_sensor", "modality": "audio"},
            },
            config={"align_rate_hz": 30},
        )
        writer = SessionWriter(session)

        camera_frame_0 = SensorFrame(
            sensor_name="camera",
            sensor_type="camera_sensor",
            modality="rgb",
            frame_id=0,
            time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
            payload={"color": np.full((4, 6, 3), 16, dtype=np.uint8)},
        )
        camera_frame_1 = SensorFrame(
            sensor_name="camera",
            sensor_type="camera_sensor",
            modality="rgb",
            frame_id=1,
            time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
            payload={"color": np.full((4, 6, 3), 64, dtype=np.uint8)},
        )
        ft_frame_0 = SensorFrame(
            sensor_name="ft",
            sensor_type="ft_sensor",
            modality="force_torque",
            frame_id=0,
            time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
            payload={"force": [1.0, 2.0, 3.0], "torque": [0.1, 0.2, 0.3]},
        )
        ft_frame_1 = SensorFrame(
            sensor_name="ft",
            sensor_type="ft_sensor",
            modality="force_torque",
            frame_id=1,
            time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
            payload={"force": [4.0, 5.0, 6.0], "torque": [0.4, 0.5, 0.6]},
        )
        imu_frame_0 = SensorFrame(
            sensor_name="imu",
            sensor_type="imu_sensor",
            modality="imu",
            frame_id=0,
            time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
            payload={"acceleration": [0.1, 0.2, 9.8], "angular_velocity": [0.01, 0.02, 0.03]},
        )
        imu_frame_1 = SensorFrame(
            sensor_name="imu",
            sensor_type="imu_sensor",
            modality="imu",
            frame_id=1,
            time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
            payload={"acceleration": [0.2, 0.3, 9.7], "angular_velocity": [0.04, 0.05, 0.06]},
        )
        microphone_frame_0 = SensorFrame(
            sensor_name="microphone",
            sensor_type="microphone_sensor",
            modality="audio",
            frame_id=0,
            time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
            payload={"audio": np.array([0, 1000, -1000, 500], dtype=np.int16)},
        )
        microphone_frame_1 = SensorFrame(
            sensor_name="microphone",
            sensor_type="microphone_sensor",
            modality="audio",
            frame_id=1,
            time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
            payload={"audio": np.array([0, 2000, -2000, 1500], dtype=np.int16)},
        )

        for frame in (
            camera_frame_0,
            camera_frame_1,
            ft_frame_0,
            ft_frame_1,
            imu_frame_0,
            imu_frame_1,
            microphone_frame_0,
            microphone_frame_1,
        ):
            writer.write_sensor_frame(frame)

        writer.write_aligned_frame(
            AlignedFrame(
                sequence_id=0,
                aligned_time=1.0,
                frames={
                    "camera": camera_frame_0,
                    "ft": ft_frame_0,
                    "imu": imu_frame_0,
                    "microphone": microphone_frame_0,
                },
                missing_sensors=[],
                age_by_sensor={"camera": 0.0, "ft": 0.0, "imu": 0.0, "microphone": 0.0},
            )
        )
        writer.write_aligned_frame(
            AlignedFrame(
                sequence_id=1,
                aligned_time=1.1,
                frames={
                    "camera": camera_frame_1,
                    "ft": ft_frame_1,
                    "imu": imu_frame_1,
                    "microphone": microphone_frame_1,
                },
                missing_sensors=[],
                age_by_sensor={"camera": 0.0, "ft": 0.0, "imu": 0.0, "microphone": 0.0},
            )
        )
        writer.close()
        return session.output_dir

    def _create_session_with_motors(self, output_root: str) -> Path:
        session = create_session_info(
            output_root,
            sensors={
                "camera": {"sensor_type": "camera_sensor", "modality": "rgb"},
                "motors": {"sensor_type": "motors_sensor", "modality": "motor_state"},
            },
            config={"align_rate_hz": 30},
        )
        writer = SessionWriter(session)

        camera_frame_0 = SensorFrame(
            sensor_name="camera",
            sensor_type="camera_sensor",
            modality="rgb",
            frame_id=0,
            time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
            payload={"color": np.full((4, 6, 3), 16, dtype=np.uint8)},
        )
        camera_frame_1 = SensorFrame(
            sensor_name="camera",
            sensor_type="camera_sensor",
            modality="rgb",
            frame_id=1,
            time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
            payload={"color": np.full((4, 6, 3), 64, dtype=np.uint8)},
        )
        motors_frame_0 = SensorFrame(
            sensor_name="motors",
            sensor_type="motors_sensor",
            modality="motor_state",
            frame_id=0,
            time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
            payload={
                "motor_1": {"position": 1.0, "velocity": 2.0, "torque": 3.0},
                "motor_2": {"position": 4.0, "velocity": 5.0, "torque": 6.0},
            },
        )
        motors_frame_1 = SensorFrame(
            sensor_name="motors",
            sensor_type="motors_sensor",
            modality="motor_state",
            frame_id=1,
            time=FrameTime(host_time=1.1, monotonic_time=1.1, aligned_time=1.1),
            payload={
                "motor_1": {"position": 1.1, "velocity": 2.1, "torque": 3.1},
                "motor_2": {"position": 4.1, "velocity": 5.1, "torque": 6.1},
            },
        )

        for frame in (camera_frame_0, camera_frame_1, motors_frame_0, motors_frame_1):
            writer.write_sensor_frame(frame)

        writer.write_aligned_frame(
            AlignedFrame(
                sequence_id=0,
                aligned_time=1.0,
                frames={"camera": camera_frame_0, "motors": motors_frame_0},
                missing_sensors=[],
                age_by_sensor={"camera": 0.0, "motors": 0.0},
            )
        )
        writer.write_aligned_frame(
            AlignedFrame(
                sequence_id=1,
                aligned_time=1.1,
                frames={"camera": camera_frame_1, "motors": motors_frame_1},
                missing_sensors=[],
                age_by_sensor={"camera": 0.0, "motors": 0.0},
            )
        )
        writer.close()
        return session.output_dir

    def test_annotation_service_crud_and_reader_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            service = AnnotationService(session_dir, schema=AnnotationSchema.default(), annotator="tester")
            service.ensure_initialized()

            session_annotation = service.upsert_session_annotation(
                {
                    "task_name": "pick_place",
                    "instruction": "pick the cube and place it",
                    "success": True,
                    "usable_for_training": True,
                }
            )
            span = service.create_span(
                {
                    "start_sequence_id": 0,
                    "end_sequence_id": 1,
                    "start_time": 1.0,
                    "end_time": 1.1,
                    "data": {"phase": "manipulate", "valid_segment": True},
                }
            )
            keyframe = service.create_keyframe(
                {
                    "sequence_id": 1,
                    "aligned_time": 1.1,
                    "data": {"event": "released", "object_state": "placed"},
                }
            )

            self.assertEqual(session_annotation["data"]["task_name"], "pick_place")
            self.assertEqual(span["start_sequence_id"], 0)
            self.assertEqual(keyframe["sequence_id"], 1)

            reader = SessionReader(session_dir)
            summary = reader.summary()
            self.assertIn("annotation_summary", summary)
            self.assertEqual(summary["annotation_summary"]["span_count"], 1)
            self.assertEqual(summary["annotation_summary"]["keyframe_count"], 1)
            bundle = reader.load_annotation_bundle()
            self.assertEqual(bundle["session"]["data"]["instruction"], "pick the cube and place it")
            self.assertEqual(len(bundle["spans"]), 1)
            self.assertEqual(len(bundle["keyframes"]), 1)

    def test_schema_snapshot_takes_precedence_over_later_project_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            schema_path_v1 = Path(tmp_dir) / "annotation_schema_v1.json"
            schema_path_v2 = Path(tmp_dir) / "annotation_schema_v2.json"
            schema_path_v1.write_text(
                json.dumps(
                    {
                        "version": "custom-v1",
                        "fields": [
                            {
                                "id": "task_name",
                                "scope": "session",
                                "type": "string",
                                "label": "Task Name",
                            },
                            {
                                "id": "review_note",
                                "scope": "session",
                                "type": "string",
                                "label": "Review Note",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            schema_path_v2.write_text(
                json.dumps(
                    {
                        "version": "custom-v2",
                        "fields": [
                            {
                                "id": "task_name",
                                "scope": "session",
                                "type": "string",
                                "label": "Task Name",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            service = AnnotationService(session_dir, schema=AnnotationSchema.load(str(schema_path_v1)))
            service.ensure_initialized()
            service.upsert_session_annotation({"task_name": "pick", "review_note": "v1"})

            later_service = AnnotationService(session_dir, schema=AnnotationSchema.load(str(schema_path_v2)))
            bundle = later_service.load_bundle()
            field_ids = [field["id"] for field in bundle.to_dict()["schema"]["fields"] if field["scope"] == "session"]
            self.assertIn("review_note", field_ids)
            self.assertEqual(bundle.schema.version, "custom-v1")

    def test_annotation_service_allows_editing_span_and_keyframe_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            service = AnnotationService(session_dir, schema=AnnotationSchema.default())
            service.ensure_initialized()

            span = service.create_span(
                {
                    "id": "span_a",
                    "start_sequence_id": 0,
                    "end_sequence_id": 1,
                    "start_time": 1.0,
                    "end_time": 1.1,
                    "data": {"phase": "manipulate"},
                }
            )
            keyframe = service.create_keyframe(
                {
                    "id": "keyframe_a",
                    "sequence_id": 1,
                    "aligned_time": 1.1,
                    "data": {"event": "released"},
                }
            )

            updated_span = service.update_span(
                span["id"],
                {
                    "id": "span_b",
                    "start_sequence_id": 0,
                    "end_sequence_id": 1,
                    "start_time": 1.0,
                    "end_time": 1.1,
                    "data": {"phase": "contact"},
                },
            )
            updated_keyframe = service.update_keyframe(
                keyframe["id"],
                {
                    "id": "keyframe_b",
                    "sequence_id": 1,
                    "aligned_time": 1.1,
                    "data": {"event": "grasped"},
                },
            )

            self.assertEqual(updated_span["id"], "span_b")
            self.assertEqual(updated_keyframe["id"], "keyframe_b")
            bundle = service.load_bundle().to_dict()
            self.assertEqual(bundle["spans"][0]["id"], "span_b")
            self.assertEqual(bundle["keyframes"][0]["id"], "keyframe_b")

    def test_annotation_service_rejects_duplicate_annotation_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            service = AnnotationService(session_dir, schema=AnnotationSchema.default())
            service.ensure_initialized()

            service.create_span(
                {
                    "id": "duplicate_id",
                    "start_sequence_id": 0,
                    "end_sequence_id": 0,
                    "start_time": 1.0,
                    "end_time": 1.0,
                    "data": {},
                }
            )
            with self.assertRaisesRegex(ValueError, "已存在"):
                service.create_span(
                    {
                        "id": "duplicate_id",
                        "start_sequence_id": 1,
                        "end_sequence_id": 1,
                        "start_time": 1.1,
                        "end_time": 1.1,
                        "data": {},
                    }
                )

    def test_annotation_service_accepts_custom_other_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            service = AnnotationService(session_dir, schema=AnnotationSchema.default())
            service.ensure_initialized()

            span = service.create_span(
                {
                    "start_sequence_id": 0,
                    "end_sequence_id": 1,
                    "start_time": 1.0,
                    "end_time": 1.1,
                    "data": {
                        "phase": "fine_adjustment",
                        "event_tags": ["grasp", "gentle_regrasp"],
                    },
                }
            )
            keyframe = service.create_keyframe(
                {
                    "sequence_id": 1,
                    "aligned_time": 1.1,
                    "data": {
                        "event": "tool_handover",
                        "quality_tags": ["sensor_drop", "minor_blur"],
                    },
                }
            )

            self.assertEqual(span["data"]["phase"], "fine_adjustment")
            self.assertEqual(span["data"]["event_tags"], ["grasp", "gentle_regrasp"])
            self.assertEqual(keyframe["data"]["event"], "tool_handover")
            self.assertEqual(keyframe["data"]["quality_tags"], ["sensor_drop", "minor_blur"])

    def test_annotation_service_rejects_literal_other_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            service = AnnotationService(session_dir, schema=AnnotationSchema.default())
            service.ensure_initialized()

            with self.assertRaisesRegex(ValueError, "必须提供自定义标签"):
                service.create_span(
                    {
                        "start_sequence_id": 0,
                        "end_sequence_id": 0,
                        "start_time": 1.0,
                        "end_time": 1.0,
                        "data": {"phase": "other"},
                    }
                )
            with self.assertRaisesRegex(ValueError, "必须提供自定义标签"):
                service.create_keyframe(
                    {
                        "sequence_id": 0,
                        "aligned_time": 1.0,
                        "data": {"quality_tags": ["other"]},
                    }
                )

    def test_lerobot_export_includes_annotation_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            service = AnnotationService(session_dir, schema=AnnotationSchema.default())
            service.ensure_initialized()
            service.upsert_session_annotation(
                {
                    "task_name": "stack_blocks",
                    "instruction": "stack the red block",
                    "success": True,
                }
            )
            service.create_span(
                {
                    "start_sequence_id": 0,
                    "end_sequence_id": 1,
                    "start_time": 1.0,
                    "end_time": 1.1,
                    "data": {"phase": "manipulate", "human_intervention": False},
                }
            )
            service.create_keyframe(
                {
                    "sequence_id": 1,
                    "aligned_time": 1.1,
                    "data": {"event": "released", "object_state": "placed"},
                }
            )

            result = LeRobotSessionExporter().export(session_dir)
            tasks_payload = (result.output_path / "meta" / "tasks.jsonl").read_text(encoding="utf-8").strip()
            self.assertIn("stack_blocks", tasks_payload)

            table = pq.read_table(result.output_path / "data" / "chunk-000" / "file-000.parquet")
            rows = table.to_pylist()
            self.assertEqual(rows[0]["annotation.session.task_name"], "stack_blocks")
            self.assertEqual(rows[0]["annotation.span.phase"], "manipulate")
            self.assertEqual(rows[1]["annotation.keyframe.event"], "released")

            episode_table = pq.read_table(result.output_path / "meta" / "episodes" / "chunk-000" / "file-000.parquet")
            episode = episode_table.to_pylist()[0]
            self.assertEqual(episode["annotation.session.task_name"], "stack_blocks")

    def test_invalid_schema_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "scope"):
            AnnotationSchema.from_dict(
                {
                    "fields": [
                        {
                            "id": "broken",
                            "scope": "unknown",
                            "type": "string",
                            "label": "Broken",
                        }
                    ]
                }
            )
        with self.assertRaisesRegex(ValueError, "重复"):
            AnnotationSchema.from_dict(
                {
                    "fields": [
                        {"id": "dup", "scope": "session", "type": "string", "label": "A"},
                        {"id": "dup", "scope": "session", "type": "string", "label": "B"},
                    ]
                }
            )

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_annotation_web_server_state_and_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            running = start_annotation_server(session_dir, port=0)
            try:
                state_payload = json.loads(urlopen(f"{running.url}api/state").read().decode("utf-8"))
                self.assertTrue(state_payload["visual_streams"])
                self.assertTrue(state_payload["scalar_streams"])

                frame_response = urlopen(
                    f"{running.url}api/frame?sensor_name=camera&frame_id=0&payload_key=color"
                )
                self.assertEqual(frame_response.headers.get_content_type(), "image/png")

                request = Request(
                    f"{running.url}api/spans",
                    data=json.dumps(
                        {
                            "start_sequence_id": 0,
                            "end_sequence_id": 1,
                            "start_time": 1.0,
                            "end_time": 1.1,
                            "data": {"phase": "contact"},
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                create_payload = json.loads(urlopen(request).read().decode("utf-8"))
                self.assertTrue(create_payload["ok"])
                state_payload = json.loads(urlopen(f"{running.url}api/state").read().decode("utf-8"))
                self.assertEqual(len(state_payload["annotations"]["spans"]), 1)
            finally:
                running.close()

    @unittest.skipIf(cv2 is None, "未安装 opencv-python")
    def test_annotation_web_server_previews_media_backed_rgb_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                sensors={"camera": {"sensor_type": "camera_sensor", "modality": "rgb"}},
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)
            rgb_frame = np.full((2, 2, 3), [255, 0, 0], dtype=np.uint8)
            camera_frame = SensorFrame(
                sensor_name="camera",
                sensor_type="camera_sensor",
                modality="rgb",
                frame_id=0,
                time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                payload={"color": rgb_frame},
            )
            writer.write_sensor_frame(camera_frame)
            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=0,
                    aligned_time=1.0,
                    frames={"camera": camera_frame},
                    missing_sensors=[],
                    age_by_sensor={"camera": 0.0},
                )
            )
            writer.close()

            running = start_annotation_server(session.output_dir, port=0)
            try:
                response = urlopen(
                    f"{running.url}api/frame?sensor_name=camera&frame_id=0&payload_key=color"
                )
                payload = np.frombuffer(response.read(), dtype=np.uint8)
                decoded = cv2.imdecode(payload, cv2.IMREAD_COLOR)
                self.assertIsNotNone(decoded)
                np.testing.assert_allclose(
                    decoded[0, 0],
                    np.array([0, 0, 255], dtype=np.uint8),
                    atol=5,
                )
            finally:
                running.close()

    def test_annotation_web_server_shows_only_six_ft_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            running = start_annotation_server(session_dir, port=0)
            try:
                state_payload = json.loads(urlopen(f"{running.url}api/state").read().decode("utf-8"))
                ft_stream_ids = sorted(
                    item["id"]
                    for item in state_payload["scalar_streams"]
                    if item["id"].startswith("ft.")
                )
                self.assertEqual(
                    ft_stream_ids,
                    [
                        "ft.force.0",
                        "ft.force.1",
                        "ft.force.2",
                        "ft.torque.0",
                        "ft.torque.1",
                        "ft.torque.2",
                    ],
                )
                ft_labels = {
                    item["id"]: item["label"]
                    for item in state_payload["scalar_streams"]
                    if item["id"].startswith("ft.")
                }
                self.assertEqual(
                    ft_labels,
                    {
                        "ft.force.0": "ft.force.x",
                        "ft.force.1": "ft.force.y",
                        "ft.force.2": "ft.force.z",
                        "ft.torque.0": "ft.torque.x",
                        "ft.torque.1": "ft.torque.y",
                        "ft.torque.2": "ft.torque.z",
                    },
                )
            finally:
                running.close()

    def test_annotation_web_server_prefers_standalone_imu_over_realsense_imu_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = create_session_info(
                tmp_dir,
                sensors={
                    "imu": {"sensor_type": "imu_sensor", "modality": "imu"},
                    "realsense": {"sensor_type": "realsense", "modality": "rgbd"},
                },
                config={"align_rate_hz": 30},
            )
            writer = SessionWriter(session)

            imu_frame = SensorFrame(
                sensor_name="imu",
                sensor_type="imu_sensor",
                modality="imu",
                frame_id=0,
                time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                payload={
                    "acceleration": [0.1, 0.2, 9.8],
                    "angular_velocity": [0.01, 0.02, 0.03],
                    "quaternion": [1.0, 0.0, 0.0, 0.0],
                },
            )
            realsense_frame = SensorFrame(
                sensor_name="realsense",
                sensor_type="realsense",
                modality="rgbd",
                frame_id=0,
                time=FrameTime(host_time=1.0, monotonic_time=1.0, aligned_time=1.0),
                payload={
                    "depth": np.zeros((2, 2), dtype=np.uint16),
                    "imu_samples": [
                        {
                            "sample_type": "accel",
                            "frame_number": 10,
                            "timestamp_ms": 1000.0,
                            "x": 1.0,
                            "y": 2.0,
                            "z": 3.0,
                        },
                        {
                            "sample_type": "gyro",
                            "frame_number": 11,
                            "timestamp_ms": 1001.0,
                            "x": 4.0,
                            "y": 5.0,
                            "z": 6.0,
                        },
                    ],
                    # Even if a RealSense payload contains IMU-like scalar keys,
                    # the annotation page should not surface them as IMU curves.
                    "acceleration": [99.0, 98.0, 97.0],
                    "angular_velocity": [0.9, 0.8, 0.7],
                },
            )
            writer.write_sensor_frame(imu_frame)
            writer.write_sensor_frame(realsense_frame)
            writer.write_aligned_frame(
                AlignedFrame(
                    sequence_id=0,
                    aligned_time=1.0,
                    frames={"imu": imu_frame, "realsense": realsense_frame},
                    missing_sensors=[],
                    age_by_sensor={"imu": 0.0, "realsense": 0.0},
                )
            )
            writer.close()

            running = start_annotation_server(session.output_dir, port=0)
            try:
                state_payload = json.loads(urlopen(f"{running.url}api/state").read().decode("utf-8"))
                stream_ids = {item["id"] for item in state_payload["scalar_streams"]}
                self.assertIn("imu.acceleration.0", stream_ids)
                self.assertIn("imu.angular_velocity.2", stream_ids)
                self.assertIn("imu.quaternion.3", stream_ids)
                self.assertFalse(any(item.startswith("realsense.") for item in stream_ids))
            finally:
                running.close()

    def test_annotation_web_server_exposes_audio_as_summary_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session_with_audio(tmp_dir)
            running = start_annotation_server(session_dir, port=0)
            try:
                state_payload = json.loads(urlopen(f"{running.url}api/state").read().decode("utf-8"))
                stream_ids = {item["id"] for item in state_payload["scalar_streams"]}
                self.assertIn("microphone.audio_rms", stream_ids)
                self.assertIn("microphone.audio_peak", stream_ids)
                self.assertNotIn("microphone.audio.0", stream_ids)
                self.assertEqual(state_payload["audio_summary"][0]["sensor_name"], "microphone")
                self.assertEqual(state_payload["audio_streams"][0]["sensor_name"], "microphone")
                self.assertGreater(state_payload["audio_streams"][0]["duration_sec"], 0.0)

                audio_response = urlopen(f"{running.url}api/audio?sensor_name=microphone")
                self.assertEqual(audio_response.headers.get_content_type(), "audio/wav")
                self.assertEqual(audio_response.read(4), b"RIFF")
            finally:
                running.close()

    def test_annotation_web_server_shows_only_six_motor_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session_with_motors(tmp_dir)
            running = start_annotation_server(session_dir, port=0)
            try:
                state_payload = json.loads(urlopen(f"{running.url}api/state").read().decode("utf-8"))
                motor_stream_ids = sorted(
                    item["id"]
                    for item in state_payload["scalar_streams"]
                    if item["id"].startswith("motors.")
                )
                self.assertEqual(
                    motor_stream_ids,
                    [
                        "motors.motor_1.position",
                        "motors.motor_1.torque",
                        "motors.motor_1.velocity",
                        "motors.motor_2.position",
                        "motors.motor_2.torque",
                        "motors.motor_2.velocity",
                    ],
                )
            finally:
                running.close()

    def test_annotation_web_server_shutdown_endpoint_stops_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            running = start_annotation_server(session_dir, port=0)
            try:
                request = Request(
                    f"{running.url}api/shutdown",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                payload = json.loads(urlopen(request).read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                self.assertTrue(payload["result"]["stopping"])
                if running.thread is not None:
                    running.thread.join(timeout=5.0)
                    self.assertFalse(running.thread.is_alive())
            finally:
                running.close()

    def test_annotation_web_server_can_navigate_between_sibling_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_a = self._create_session(tmp_dir)
            renamed_a = Path(tmp_dir) / "session_alpha"
            session_a.rename(renamed_a)
            session_b = self._create_session(tmp_dir)
            renamed_b = Path(tmp_dir) / "session_beta"
            session_b.rename(renamed_b)
            ordered_sessions = sorted([renamed_a, renamed_b], key=lambda item: item.name)
            running = start_annotation_server(ordered_sessions[0], port=0)
            try:
                state_payload = json.loads(urlopen(f"{running.url}api/state").read().decode("utf-8"))
                self.assertEqual(state_payload["navigation"]["total"], 2)
                self.assertEqual(state_payload["navigation"]["position"], 1)
                self.assertFalse(state_payload["navigation"]["has_previous"])
                self.assertTrue(state_payload["navigation"]["has_next"])

                request = Request(
                    f"{running.url}api/navigate",
                    data=json.dumps({"direction": "next"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                payload = json.loads(urlopen(request).read().decode("utf-8"))
                self.assertTrue(payload["ok"])

                next_state = json.loads(urlopen(f"{running.url}api/state").read().decode("utf-8"))
                self.assertEqual(next_state["navigation"]["position"], 2)
                self.assertTrue(next_state["navigation"]["has_previous"])
                self.assertFalse(next_state["navigation"]["has_next"])
                self.assertEqual(next_state["navigation"]["current_session_name"], ordered_sessions[1].name)
                self.assertNotEqual(
                    next_state["navigation"]["current_session_dir"],
                    state_payload["navigation"]["current_session_dir"],
                )
            finally:
                running.close()

    def test_sdk_inspect_includes_annotation_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = self._create_session(tmp_dir)
            service = AnnotationService(session_dir, schema=AnnotationSchema.default())
            service.ensure_initialized()
            service.create_keyframe(
                {
                    "sequence_id": 0,
                    "aligned_time": 1.0,
                    "data": {"event": "contact"},
                }
            )

            repo_root = Path(__file__).resolve().parents[1]
            result = subprocess.run(
                [sys.executable, str(repo_root / "scripts" / "sdk_inspect.py"), str(session_dir)],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(repo_root),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("annotation_summary", payload)
            self.assertEqual(payload["annotation_summary"]["keyframe_count"], 1)
