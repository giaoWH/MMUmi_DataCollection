from .common.camera_base import CameraBaseSensor


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
        )
