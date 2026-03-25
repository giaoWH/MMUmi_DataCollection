import unittest

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

if cv2 is not None and np is not None:
    from sensors.camera_sensor import CameraSensor
else:  # pragma: no cover
    CameraSensor = None


@unittest.skipIf(cv2 is None or np is None, "未安装 cv2 或 numpy")
class CameraSensorProcessingTest(unittest.TestCase):
    def test_process_frame_applies_horizontal_flip_before_rgb_conversion(self) -> None:
        sensor = CameraSensor(width=2, height=2, flip_horizontal=True)
        frame_bgr = np.array(
            [
                [[0, 10, 20], [30, 40, 50]],
                [[60, 70, 80], [90, 100, 110]],
            ],
            dtype=np.uint8,
        )

        output = sensor._process_frame(frame_bgr)

        expected_bgr = cv2.flip(frame_bgr, 1)
        expected_rgb = cv2.cvtColor(expected_bgr, cv2.COLOR_BGR2RGB)
        np.testing.assert_array_equal(output, expected_rgb)


if __name__ == "__main__":
    unittest.main()
