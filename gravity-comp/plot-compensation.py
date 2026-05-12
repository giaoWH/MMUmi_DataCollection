import json
import os
import glob
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime


def parse_host_time(s: str) -> float:
    parts = s.split("-")
    dt = datetime(
        int(parts[0]), int(parts[1]), int(parts[2]),
        int(parts[3]), int(parts[4]), int(parts[5]),
        int(parts[6]) * 1000,
    )
    return dt.timestamp()


def load_force(path: str, key: str = "force") -> tuple:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    ts = np.array([parse_host_time(r["host_time"]) for r in records])
    force = np.array([r["payload"][key] for r in records])
    return ts, force


def plot_session(session_dir: str, session_name: str, out_path: str):
    # 查找 raw FT 文件
    import glob
    raw_files = glob.glob(os.path.join(session_dir, "streams", "ft", "*_s.jsonl"))
    if not raw_files:
        raise FileNotFoundError(f"No *_s.jsonl in {session_dir}/streams/ft/")
    raw_path = raw_files[0]
    comp_path = os.path.join(session_dir, "streams", "ft", "pure-force.jsonl")

    ts_raw, force_raw = load_force(raw_path)
    ts_comp, force_comp = load_force(comp_path)

    t0 = ts_raw[0]
    t_raw = ts_raw - t0
    t_comp = ts_comp - t0

    axis_labels = ["X", "Y", "Z"]
    colors_raw = "#4C72B0"
    colors_comp = "#C44E52"

    fig, axes = plt.subplots(3, 1, figsize=(12, 7), sharex=True)
    fig.suptitle(
        f"Gravity Compensation Result — Session {session_name} (No-Load)",
        fontsize=14, fontweight="bold",
    )

    for i, ax in enumerate(axes):
        ax.plot(t_raw, force_raw[:, i], color=colors_raw, alpha=0.55,
                linewidth=0.8, label="Raw FT")
        ax.plot(t_comp, force_comp[:, i], color=colors_comp, alpha=0.85,
                linewidth=0.8, label="Compensated")
        ax.axhline(0, color="gray", linestyle="--", linewidth=0.5)

        rms_raw = np.sqrt(np.mean(force_raw[:, i] ** 2))
        rms_comp = np.sqrt(np.mean(force_comp[:, i] ** 2))
        ax.text(
            0.98, 0.95,
            f"RMS  raw: {rms_raw:.3f} N\n     comp: {rms_comp:.3f} N",
            transform=ax.transAxes, fontsize=9, verticalalignment="top",
            horizontalalignment="right",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8),
        )

        ax.set_ylabel(f"Force {axis_labels[i]} (N)")
        ax.legend(loc="upper left", fontsize=8)

    axes[-1].set_xlabel("Time (s)")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Saved to {out_path}")


def main():
    testdata_dir = "gravity-comp-testdata"
    for session_name in ["01", "02", "03", "04", "05"]:
        session_dir = os.path.join(testdata_dir, session_name)
        if not os.path.isdir(session_dir):
            continue
        out_path = f"gravity-comp-plot-{session_name}.png"
        plot_session(session_dir, session_name, out_path)


if __name__ == "__main__":
    main()
