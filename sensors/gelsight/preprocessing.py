from __future__ import annotations

import cv2
import numpy as np


_MINI_INTERMEDIATE_WIDTH = 895
_MINI_INTERMEDIATE_HEIGHT = 672
_MINI_BORDER_RATIO = 1.0 / 7.0


def preprocess_gelsight_mini(
    frame_bgr: np.ndarray,
    output_width: int,
    output_height: int,
) -> np.ndarray:
    """
    Normalize GelSight Mini images using the vendor-provided geometry heuristic.

    The pipeline mirrors the official Mini preprocessing approach:
    resize -> crop borders -> drop the last column -> resize to the requested output.
    """
    resized = cv2.resize(
        frame_bgr,
        (_MINI_INTERMEDIATE_WIDTH, _MINI_INTERMEDIATE_HEIGHT),
        interpolation=cv2.INTER_LINEAR,
    )
    border_y = int(resized.shape[0] * _MINI_BORDER_RATIO)
    border_x = int(np.floor(resized.shape[1] * _MINI_BORDER_RATIO))
    cropped = resized[
        border_y : resized.shape[0] - border_y,
        border_x : resized.shape[1] - border_x,
    ]
    normalized = cropped[:, :-1]
    return cv2.resize(
        normalized,
        (output_width, output_height),
        interpolation=cv2.INTER_LINEAR,
    )
