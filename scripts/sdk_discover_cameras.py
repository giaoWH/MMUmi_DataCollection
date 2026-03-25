from __future__ import annotations

import sys

from scripts.sdk_discover_ports import run_cli


def main() -> None:
    argv = ["--scope", "visual", *sys.argv[1:]]
    raise SystemExit(run_cli(argv))


if __name__ == "__main__":
    main()
