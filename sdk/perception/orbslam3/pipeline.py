from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sdk.core.frame import TrajectoryFrame

from .bundle import OrbSlam3Bundle, OrbSlam3SessionBundleExporter
from .command_runner import OrbSlam3CommandConfig, OrbSlam3CommandRunner


@dataclass(frozen=True)
class OrbSlam3PipelineConfig:
    command: str
    mode: str = "rgbd_inertial"
    source_name: str = "orbslam3"
    working_dir: str | None = None
    env: dict[str, str] | None = None
    output_mode: str = "stdout_jsonl"
    bundle_dir: str | None = None


class OrbSlam3Pipeline:
    def __init__(self, config: OrbSlam3PipelineConfig) -> None:
        self.config = config
        self.bundle_exporter = OrbSlam3SessionBundleExporter()

    def run(self, session_dir: str | Path) -> tuple[OrbSlam3Bundle, list[TrajectoryFrame]]:
        bundle = self.bundle_exporter.export(
            session_dir,
            output_dir=self.config.bundle_dir,
            mode=self.config.mode,
        )
        runner = OrbSlam3CommandRunner(
            OrbSlam3CommandConfig(
                command=self.config.command,
                source_name=self.config.source_name,
                working_dir=self.config.working_dir,
                env=self.config.env,
                output_mode=self.config.output_mode,
                template_vars=bundle.template_vars(),
            )
        )
        frames = runner.run(session_dir)
        return bundle, frames
