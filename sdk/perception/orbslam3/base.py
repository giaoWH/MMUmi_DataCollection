from __future__ import annotations

import abc
from pathlib import Path

from sdk.core.frame import TrajectoryFrame


class TrajectoryRunner(abc.ABC):
    @abc.abstractmethod
    def run(self, session_dir: str | Path) -> list[TrajectoryFrame]:
        pass
