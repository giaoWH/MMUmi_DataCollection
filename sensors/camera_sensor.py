from .common.camera_base import CameraBaseSensor


class CameraSensor(CameraBaseSensor):
    """
    RGB 相机传感器。

    输出格式:
    shape=(height, width, 3) 的 uint8 RGB 图像
    """

    device_label = "相机"

    def __init__(
        self,
        name="Camera",
        device_index=0,
        width=640,
        height=480,
        fps=30,
        flip_horizontal=False,
        flip_vertical=False,
        frame_queue_size=128,
    ):
        super().__init__(
            name=name,
            device_index=device_index,
            width=width,
            height=height,
            fps=fps,
            flip_horizontal=flip_horizontal,
            flip_vertical=flip_vertical,
            frame_queue_size=frame_queue_size,
        )
