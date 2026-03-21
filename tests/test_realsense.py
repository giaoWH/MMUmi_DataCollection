import tempfile
import unittest
from pathlib import Path

import numpy as np

from sensors.realsense.realsense_communication import save_pointcloud_as_ply
from sensors.realsense_sensor import (
    RealsenseConfig,
    RealsenseImageStreamConfig,
    RealsenseSensor,
    clone_realsense_payload,
    depth_to_preview,
    infrared_to_preview,
    normalize_frame_to_uint8,
    _sample_point_colors,
)


class RealsenseUtilitiesTest(unittest.TestCase):
    def test_default_config_matches_expected_bringup_defaults(self) -> None:
        config = RealsenseConfig()

        self.assertEqual(config.name, "Realsense")
        self.assertTrue(config.enable_color)
        self.assertTrue(config.enable_depth)
        self.assertTrue(config.enable_ir1)
        self.assertTrue(config.enable_ir2)
        self.assertTrue(config.enable_imu)
        self.assertTrue(config.enable_aligned_depth_to_color)
        self.assertTrue(config.enable_pointcloud)
        self.assertEqual(config.color, RealsenseImageStreamConfig())
        self.assertEqual(config.depth, RealsenseImageStreamConfig())
        self.assertEqual(config.infrared, RealsenseImageStreamConfig())

    def test_sensor_uses_default_config_when_none_provided(self) -> None:
        sensor = RealsenseSensor()

        self.assertIsInstance(sensor.config, RealsenseConfig)
        self.assertEqual(sensor.name, sensor.config.name)

    def test_clone_realsense_payload_deep_copies_arrays_and_lists(self) -> None:
        payload = {
            "color": np.arange(12, dtype=np.uint8).reshape(2, 2, 3),
            "pointcloud": {
                "vertices": np.array([[1.0, 2.0, 3.0]], dtype=np.float32),
            },
            "imu_samples": [{"sample_type": "accel", "x": 1.0}],
        }

        cloned = clone_realsense_payload(payload)
        cloned["color"][0, 0, 0] = 255
        cloned["pointcloud"]["vertices"][0, 0] = 9.0
        cloned["imu_samples"][0]["x"] = 8.0

        self.assertEqual(int(payload["color"][0, 0, 0]), 0)
        self.assertEqual(float(payload["pointcloud"]["vertices"][0, 0]), 1.0)
        self.assertEqual(float(payload["imu_samples"][0]["x"]), 1.0)

    def test_normalize_frame_to_uint8_preserves_uint8_input(self) -> None:
        frame = np.array([[0, 10], [20, 30]], dtype=np.uint8)

        normalized = normalize_frame_to_uint8(frame)

        self.assertEqual(normalized.dtype, np.uint8)
        np.testing.assert_array_equal(normalized, frame)

    def test_depth_and_infrared_preview_return_uint8_arrays(self) -> None:
        depth = np.array([[0, 1000], [2000, 4000]], dtype=np.uint16)
        infrared = np.array([[5, 15], [25, 35]], dtype=np.uint16)

        depth_preview = depth_to_preview(depth)
        infrared_preview = infrared_to_preview(infrared)

        self.assertEqual(depth_preview.dtype, np.uint8)
        self.assertEqual(infrared_preview.dtype, np.uint8)
        self.assertEqual(depth_preview.shape, depth.shape)
        self.assertEqual(infrared_preview.shape, infrared.shape)

    def test_sample_point_colors_maps_texcoords_to_rgb_pixels(self) -> None:
        color_image = np.array(
            [
                [[255, 0, 0], [0, 255, 0]],
                [[0, 0, 255], [255, 255, 0]],
            ],
            dtype=np.uint8,
        )
        texcoords = np.array(
            [
                [0.0, 0.0],
                [1.0, 0.0],
                [0.0, 1.0],
                [1.0, 1.0],
            ],
            dtype=np.float32,
        )

        sampled = _sample_point_colors(color_image, texcoords)

        expected = np.array(
            [
                [255, 0, 0],
                [0, 255, 0],
                [0, 0, 255],
                [255, 255, 0],
            ],
            dtype=np.uint8,
        )
        np.testing.assert_array_equal(sampled, expected)

    def test_save_pointcloud_as_ply_writes_ascii_ply_file(self) -> None:
        vertices = np.array(
            [
                [0.0, 1.0, 2.0],
                [3.0, 4.0, 5.0],
            ],
            dtype=np.float32,
        )
        colors = np.array(
            [
                [255, 0, 0],
                [0, 255, 0],
            ],
            dtype=np.uint8,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = save_pointcloud_as_ply(Path(tmp_dir) / "cloud.ply", vertices, colors)
            content = path.read_text(encoding="utf-8")

        self.assertIn("ply", content)
        self.assertIn("format ascii 1.0", content)
        self.assertIn("element vertex 2", content)
        self.assertIn("property uchar red", content)
        self.assertIn("0.000000 1.000000 2.000000 255 0 0", content)


if __name__ == "__main__":
    unittest.main()
