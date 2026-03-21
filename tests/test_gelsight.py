import unittest
from unittest.mock import patch

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

if cv2 is not None and np is not None:
    from sensors.gelsight.preprocessing import preprocess_gelsight_mini
    from sensors.gelsight_sensor import GelSightSensor
else:  # pragma: no cover
    preprocess_gelsight_mini = None
    GelSightSensor = None


@unittest.skipIf(cv2 is None or np is None, "未安装 cv2 或 numpy")
class GelSightMiniPreprocessingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.frame_bgr = np.arange(480 * 640 * 3, dtype=np.uint8).reshape(480, 640, 3)

    def test_preprocess_outputs_requested_size_640x480(self) -> None:
        output = preprocess_gelsight_mini(self.frame_bgr, 640, 480)

        self.assertEqual(output.shape, (480, 640, 3))
        self.assertEqual(output.dtype, np.uint8)

    def test_preprocess_outputs_requested_size_320x240(self) -> None:
        output = preprocess_gelsight_mini(self.frame_bgr, 320, 240)

        self.assertEqual(output.shape, (240, 320, 3))
        self.assertEqual(output.dtype, np.uint8)

    def test_preprocess_matches_official_intermediate_geometry(self) -> None:
        captured_shapes: list[tuple[int, ...]] = []
        original_resize = cv2.resize

        def tracking_resize(image, dsize, interpolation=cv2.INTER_LINEAR):
            captured_shapes.append(tuple(image.shape))
            return original_resize(image, dsize, interpolation=interpolation)

        with patch("sensors.gelsight.preprocessing.cv2.resize", side_effect=tracking_resize):
            output = preprocess_gelsight_mini(self.frame_bgr, 320, 240)

        self.assertEqual(output.shape, (240, 320, 3))
        self.assertEqual(captured_shapes[0], self.frame_bgr.shape)
        self.assertEqual(captured_shapes[1], (480, 640, 3))


@unittest.skipIf(cv2 is None or np is None, "未安装 cv2 或 numpy")
class GelSightSensorProcessingTest(unittest.TestCase):
    def test_process_frame_uses_preprocessing_and_returns_rgb_without_flip(self) -> None:
        sensor = GelSightSensor(width=2, height=2)
        sensor.flip_vertical = True
        frame_bgr = np.array(
            [
                [[0, 10, 20], [30, 40, 50]],
                [[60, 70, 80], [90, 100, 110]],
            ],
            dtype=np.uint8,
        )

        with patch(
            "sensors.gelsight_sensor.preprocess_gelsight_mini",
            side_effect=lambda image, output_width, output_height: image.copy(),
        ) as preprocess_mock:
            output = sensor._process_frame(frame_bgr)

        preprocess_mock.assert_called_once()
        args, kwargs = preprocess_mock.call_args
        np.testing.assert_array_equal(args[0], frame_bgr)
        self.assertEqual(kwargs, {"output_width": 2, "output_height": 2})
        expected_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        np.testing.assert_array_equal(output, expected_rgb)
