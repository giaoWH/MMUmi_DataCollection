from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class GravityCompensationConfig:
    enabled: bool = False
    mass: float = 0.25
    com: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    stabilization_sec: float = 2.0
    calibration_duration_sec: float = 3.0
    minimum_samples: int = 10


class GravityCompensator:
    def __init__(self, mass: float, com_pos: list[float]) -> None:
        self.mass = mass
        self.com = np.array(com_pos, dtype=np.float64)
        self.g_val = 9.81
        self.bias = np.zeros(3, dtype=np.float64)
        self.is_calibrated = False

    def calibrate_bias(self, f_data_list: list[np.ndarray], q_data_list: list[np.ndarray]) -> bool:
        if len(f_data_list) < 10:
            return False

        f_avg = np.mean(f_data_list, axis=0)
        g_forces = [self._compute_gravity_in_body_frame(q) for q in q_data_list]
        g_avg = np.mean(g_forces, axis=0)
        self.bias = f_avg - g_avg
        self.is_calibrated = True
        return True

    def process(self, f_raw: np.ndarray, quat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if not self.is_calibrated:
            return f_raw, np.zeros(3, dtype=np.float64)

        gravity_force = self._compute_gravity_in_body_frame(quat)
        pure_force = f_raw - gravity_force - self.bias
        return pure_force, gravity_force

    def _compute_gravity_in_body_frame(self, q: np.ndarray) -> np.ndarray:
        w, x, y, z = q
        xx, yy, zz = x * x, y * y, z * z
        xy, xz, yz = x * y, x * z, y * z
        xw, yw, zw = x * w, y * w, z * w

        rotation_transpose = np.array(
            [
                [1 - 2 * (yy + zz), 2 * (xy + zw), 2 * (xz - yw)],
                [2 * (xy - zw), 1 - 2 * (xx + zz), 2 * (yz + xw)],
                [2 * (xz + yw), 2 * (yz - xw), 1 - 2 * (xx + yy)],
            ],
            dtype=np.float64,
        ).T
        gravity_world = np.array([0.0, 0.0, -1.0], dtype=np.float64) * self.mass * self.g_val
        return rotation_transpose @ gravity_world
