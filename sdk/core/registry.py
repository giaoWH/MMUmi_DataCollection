from __future__ import annotations

import time
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

    def wait_until_ready(self, timeout: float | None = None, poll_interval: float = 0.05) -> bool:
        deadline = None if timeout is None else (time.time() + timeout)

        while True:
            pending: list[SensorAdapter] = []
            for sensor in self._sensors.values():
                if sensor.is_ready():
                    continue

                status = sensor.get_status()
                if status.get("running") is False:
                    raise RuntimeError(f"传感器未就绪且已停止: {sensor.name}")
                pending.append(sensor)

            if not pending:
                return True

            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                time.sleep(min(poll_interval, remaining))
            else:
                time.sleep(poll_interval)

    def get_metadata(self) -> dict[str, dict[str, object]]:
        return {name: sensor.get_metadata() for name, sensor in self._sensors.items()}

    def get_status(self) -> dict[str, dict[str, object]]:
        return {name: sensor.get_status() for name, sensor in self._sensors.items()}
