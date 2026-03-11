from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExportResult:
    export_format: str
    output_path: Path


class SessionExporter(abc.ABC):
    export_format: str

    @abc.abstractmethod
    def export(self, session_dir: str | Path, output_path: str | Path | None = None) -> ExportResult:
        pass
