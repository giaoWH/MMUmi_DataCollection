from .ft_sensor import FTSensor
from .gelsight_sensor import GelSightSensor
from .imu_sensor import IMUSensor
from .motors_sensor import MotorsSensor
from .calibration_utils import wait_for_calibrated_sensors

try:
    from .microphone_sensor import MicrophoneSensor
except ImportError as exc:  # pragma: no cover
    _MICROPHONE_IMPORT_ERROR = exc

    class MicrophoneSensor:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("未安装 pyaudio，无法使用麦克风传感器") from _MICROPHONE_IMPORT_ERROR

try:
    from .realsense_sensor import RealSenseSensor, RealsenseSensor
except ImportError as exc:  # pragma: no cover
    _REALSENSE_IMPORT_ERROR = exc

    class RealsenseSensor:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("未安装 pyrealsense2，无法使用 RealSense 传感器") from _REALSENSE_IMPORT_ERROR

    RealSenseSensor = RealsenseSensor
