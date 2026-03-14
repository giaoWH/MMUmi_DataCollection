from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdk.sensors.base import SensorAdapter


class SensorRegistry:
    def __init__(self) -> None:
        self._sensors: dict[str, SensorAdapter] = {}

    @property
    def sensors(self) -> dict[str, "SensorAdapter"]:
        return dict(self._sensors)

    def register(self, sensor: "SensorAdapter") -> None:
        if sensor.name in self._sensors:
            raise ValueError(f"重复的传感器名称: {sensor.name}")
        self._sensors[sensor.name] = sensor

    def start_all(self) -> None:
        started: list[SensorAdapter] = []
        try:
            for sensor in self._sensors.values():
                sensor.start()
                started.append(sensor)
        except Exception:
            for sensor in reversed(started):
                sensor.stop()
            raise

    def stop_all(self) -> None:
        for sensor in reversed(list(self._sensors.values())):
            sensor.stop()

    def get_metadata(self) -> dict[str, dict[str, object]]:
        return {name: sensor.get_metadata() for name, sensor in self._sensors.items()}

    def get_status(self) -> dict[str, dict[str, object]]:
        return {name: sensor.get_status() for name, sensor in self._sensors.items()}
