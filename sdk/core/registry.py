from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdk.sensors.base import SensorAdapter


class SensorRegistry:
    def __init__(self) -> None:
        self._sensors: dict[str, SensorAdapter] = {}
        self._started_at_monotonic: float | None = None
        self._not_running_since: dict[str, float] = {}

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
            self._started_at_monotonic = time.monotonic()
            self._not_running_since.clear()
        except Exception:
            for sensor in reversed(started):
                sensor.stop()
            self._started_at_monotonic = None
            self._not_running_since.clear()
            raise

    def stop_all(self) -> None:
        self._started_at_monotonic = None
        self._not_running_since.clear()
        for sensor in reversed(list(self._sensors.values())):
            sensor.stop()

    def wait_until_ready(
        self,
        timeout: float | None = None,
        poll_interval: float = 0.05,
        startup_grace_sec: float = 1.0,
        stop_confirmation_sec: float = 0.3,
    ) -> bool:
        deadline = None if timeout is None else (time.monotonic() + timeout)

        while True:
            pending: list[SensorAdapter] = []
            now = time.monotonic()
            for sensor in self._sensors.values():
                if sensor.is_ready():
                    self._not_running_since.pop(sensor.name, None)
                    continue

                status = sensor.get_status()
                if status.get("running") is False:
                    startup_elapsed = None
                    if self._started_at_monotonic is not None:
                        startup_elapsed = now - self._started_at_monotonic

                    if startup_elapsed is not None and startup_elapsed < startup_grace_sec:
                        self._not_running_since.pop(sensor.name, None)
                        pending.append(sensor)
                        continue

                    first_not_running_at = self._not_running_since.setdefault(sensor.name, now)
                    if (now - first_not_running_at) >= stop_confirmation_sec:
                        raise RuntimeError(f"传感器未就绪且已停止: {sensor.name}")
                else:
                    self._not_running_since.pop(sensor.name, None)
                pending.append(sensor)

            if not pending:
                self._not_running_since.clear()
                return True

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                time.sleep(min(poll_interval, remaining))
            else:
                time.sleep(poll_interval)

    def get_metadata(self) -> dict[str, dict[str, object]]:
        return {name: sensor.get_metadata() for name, sensor in self._sensors.items()}

    def get_status(self) -> dict[str, dict[str, object]]:
        return {name: sensor.get_status() for name, sensor in self._sensors.items()}
