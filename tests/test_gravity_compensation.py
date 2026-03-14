import unittest

import numpy as np

from sdk.processors.gravity_compensation import GravityCompensator


class GravityCompensationTest(unittest.TestCase):
    def test_calibrate_and_process_force(self) -> None:
        compensator = GravityCompensator(mass=0.25, com_pos=[0.0, 0.0, 0.1])
        quaternions = [np.array([1.0, 0.0, 0.0, 0.0]) for _ in range(12)]
        gravity_force = np.array([0.0, 0.0, -0.25 * 9.81])
        samples = [gravity_force + np.array([0.2, -0.1, 0.3]) for _ in range(12)]

        calibrated = compensator.calibrate_bias(samples, quaternions)

        self.assertTrue(calibrated)
        pure_force, gravity_component = compensator.process(
            np.array([1.2, 0.9, 3.1]),
            np.array([1.0, 0.0, 0.0, 0.0]),
        )
        np.testing.assert_allclose(gravity_component, gravity_force, atol=1e-6)
        np.testing.assert_allclose(pure_force, np.array([1.0, 1.0, 5.2525]), atol=1e-6)


if __name__ == "__main__":
    unittest.main()
