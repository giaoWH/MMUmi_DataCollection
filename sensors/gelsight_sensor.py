from __future__ import annotations

import cv2
import numpy as np

from .common.camera_base import CameraBaseSensor
from .gelsight.preprocessing import preprocess_gelsight_mini


class GelSightSensor(CameraBaseSensor):
    """
    GelSight Mini 视触觉传感器。

    当前按独立 USB RGB 设备采集，但在系统内单独作为 visuotactile 模态接入，
    以便与普通 RGB 相机区分。
    """

    device_label = "GelSight"

    def __init__(
        self,
        name="GelSightMini",
        device_index=0,
        width=640,
        height=480,
        fps=30,
    ):
        super().__init__(
            name=name,
            device_index=device_index,
            width=width,
            height=height,
            fps=fps,
            flip_vertical=False,
        )

    def _process_frame(self, frame_bgr: np.ndarray) -> np.ndarray:
        processed_bgr = preprocess_gelsight_mini(
            frame_bgr,
            output_width=self.width,
            output_height=self.height,
        )
        frame_rgb = cv2.cvtColor(processed_bgr, cv2.COLOR_BGR2RGB)
        return np.ascontiguousarray(frame_rgb)
