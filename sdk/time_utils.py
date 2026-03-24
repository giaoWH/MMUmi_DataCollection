from __future__ import annotations

from datetime import datetime


def format_wall_time(timestamp: float | int | None) -> str | None:
    if timestamp is None:
        return None
    dt = datetime.fromtimestamp(float(timestamp))
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
