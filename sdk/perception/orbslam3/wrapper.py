from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
ORB_SLAM3_ROOT = REPO_ROOT / "ThirdParty" / "ORB_SLAM3"
DEFAULT_STEREO_INERTIAL_RUNNER = (
    ORB_SLAM3_ROOT / "Examples" / "Stereo-Inertial" / "sdk_stereo_inertial_offline"
)
DEFAULT_LIBRARY_DIRS = [
    ORB_SLAM3_ROOT / "lib",
    ORB_SLAM3_ROOT / "Thirdparty" / "DBoW2" / "lib",
    ORB_SLAM3_ROOT / "Thirdparty" / "g2o" / "lib",
]


@dataclass(frozen=True)
class StereoInertialBundlePaths:
    manifest_path: Path
    bundle_dir: Path
    association_file: Path
    imu_file: Path
    mode: str


def load_stereo_inertial_bundle_paths(bundle_manifest: str | Path) -> StereoInertialBundlePaths:
    manifest_path = Path(bundle_manifest).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    mode = str(payload.get("mode", ""))
    if mode != "stereo_inertial":
        raise ValueError(f"bundle_manifest mode 必须为 stereo_inertial，当前为 {mode or 'unknown'}")

    files = payload.get("files")
    if not isinstance(files, dict):
        raise ValueError("bundle_manifest 缺少 files 对象")

    bundle_dir = manifest_path.parent
    association_file = _resolve_manifest_file(bundle_dir, files, "association_file")
    imu_file = _resolve_manifest_file(bundle_dir, files, "imu_file")

    if not association_file.exists():
        raise FileNotFoundError(f"未找到 stereo association 文件: {association_file}")
    if not imu_file.exists():
        raise FileNotFoundError(f"未找到 IMU 文件: {imu_file}")

    return StereoInertialBundlePaths(
        manifest_path=manifest_path,
        bundle_dir=bundle_dir,
        association_file=association_file,
        imu_file=imu_file,
        mode=mode,
    )


def convert_euroc_trajectory_to_jsonl(
    euroc_trajectory_path: str | Path,
    output_jsonl: str | Path,
    *,
    settings_path: str | Path,
) -> int:
    trajectory_path = Path(euroc_trajectory_path)
    output_path = Path(output_jsonl)
    lines = trajectory_path.read_text(encoding="utf-8").splitlines()
    records: list[str] = []

    for frame_id, line in enumerate(_iter_euroc_data_lines(lines)):
        parts = line.split()
        if len(parts) < 8:
            raise ValueError(f"EuRoC 轨迹行字段不足: {line}")
        timestamp = float(parts[0]) / 1e9
        tx, ty, tz = (float(parts[index]) for index in range(1, 4))
        qx, qy, qz, qw = (float(parts[index]) for index in range(4, 8))
        payload = {
            "frame_id": frame_id,
            "timestamp": timestamp,
            "position": [tx, ty, tz],
            "quaternion": [qw, qx, qy, qz],
            "tracking_state": "OK",
            "metadata": {
                "settings_path": str(Path(settings_path).resolve()),
                "trajectory_format": "euroc_final",
                "coordinate_frame": "orbslam3_world",
                "pose_reference": "imu_body",
            },
        }
        records.append(json.dumps(payload, ensure_ascii=False))

    if not records:
        raise RuntimeError(f"ORB-SLAM3 未生成有效轨迹: {trajectory_path}")

    output_path.write_text("\n".join(records) + "\n", encoding="utf-8")
    return len(records)


def run_stereo_inertial_wrapper(
    *,
    bundle_manifest: str | Path,
    output_jsonl: str | Path,
    env: dict[str, str] | None = None,
) -> int:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    bundle_paths = load_stereo_inertial_bundle_paths(bundle_manifest)
    runner_path = Path(
        merged_env.get("ORB_SLAM3_RUNNER", str(DEFAULT_STEREO_INERTIAL_RUNNER))
    ).expanduser()
    vocab_path = _require_env_path(merged_env, "ORB_SLAM3_VOCAB")
    settings_path = _require_env_path(merged_env, "ORB_SLAM3_SETTINGS")

    if not runner_path.exists():
        raise FileNotFoundError(
            f"未找到 ORB-SLAM3 stereo_inertial runner: {runner_path}"
        )

    merged_env["LD_LIBRARY_PATH"] = _build_ld_library_path(merged_env.get("LD_LIBRARY_PATH"))

    with tempfile.TemporaryDirectory(prefix="orbslam3_stereo_inertial_") as tmp_dir:
        euroc_output = Path(tmp_dir) / "trajectory_euroc.txt"
        command = [
            str(runner_path),
            "--vocab",
            str(vocab_path),
            "--settings",
            str(settings_path),
            "--association",
            str(bundle_paths.association_file),
            "--imu",
            str(bundle_paths.imu_file),
            "--output",
            str(euroc_output),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(REPO_ROOT),
            env=merged_env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "ORB-SLAM3 stereo_inertial runner 执行失败:\n"
                f"command={command}\n"
                f"stdout={result.stdout}\n"
                f"stderr={result.stderr}"
            )
        if not euroc_output.exists():
            raise RuntimeError(f"runner 未生成轨迹文件: {euroc_output}")

        return convert_euroc_trajectory_to_jsonl(
            euroc_output,
            output_jsonl,
            settings_path=settings_path,
        )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ORB-SLAM3 wrapper for SDK trajectory processing")
    parser.add_argument("--mode", choices=["stereo_inertial"], required=True)
    parser.add_argument("--bundle-manifest", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if args.mode != "stereo_inertial":
        raise ValueError(f"当前 wrapper 仅支持 stereo_inertial，收到 {args.mode}")

    run_stereo_inertial_wrapper(
        bundle_manifest=args.bundle_manifest,
        output_jsonl=args.output,
    )
    return 0


def _resolve_manifest_file(bundle_dir: Path, files: dict[str, object], key: str) -> Path:
    raw_value = files.get(key)
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError(f"bundle_manifest 缺少 {key}")
    path = Path(raw_value)
    if not path.is_absolute():
        path = bundle_dir / path
    return path.resolve()


def _require_env_path(env: dict[str, str], key: str) -> Path:
    raw_value = env.get(key)
    if not raw_value:
        raise FileNotFoundError(f"缺少环境变量 {key}")
    path = Path(raw_value).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"{key} 指向的文件不存在: {path}")
    return path.resolve()


def _build_ld_library_path(existing: str | None) -> str:
    values: list[str] = []
    if existing:
        values.extend(part for part in existing.split(":") if part)
    for path in DEFAULT_LIBRARY_DIRS:
        resolved = str(path.resolve())
        if resolved not in values:
            values.append(resolved)
    return ":".join(values)


def _iter_euroc_data_lines(lines: Iterable[str]) -> Iterable[str]:
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        yield stripped
