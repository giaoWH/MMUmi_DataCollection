from __future__ import annotations

import abc

from sdk.core.frame import SensorFrame


class SensorAdapter(abc.ABC):
    def __init__(self, name: str, sensor_type: str, modality: str) -> None:
        self.name = name
        self.sensor_type = sensor_type
        self.modality = modality

    @abc.abstractmethod
    def start(self) -> None:
        pass

    @abc.abstractmethod
    def stop(self) -> None:
        pass

    @abc.abstractmethod
    def read_frame(self) -> SensorFrame | None:
        pass

    @abc.abstractmethod
    def get_metadata(self) -> dict[str, object]:
        pass

    @abc.abstractmethod
    def get_status(self) -> dict[str, object]:
        pass

    def is_ready(self) -> bool:
        return True

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        return True
