import time


def wait_for_calibrated_sensors(sensors, timeout=None, poll_interval=0.05):
    """
    等待一组传感器全部完成校准。

    约定:
    - 传感器对象实现 is_calibrated() / wait_until_calibrated()
    - 默认无需校准的传感器会立即通过
    """
    deadline = None if timeout is None else (time.time() + timeout)
    pending = list(sensors)

    while pending:
        next_pending = []
        for sensor in pending:
            if sensor.is_calibrated():
                continue
            next_pending.append(sensor)

        if not next_pending:
            return True

        if deadline is not None:
            remaining = deadline - time.time()
            if remaining <= 0:
                return False
            time.sleep(min(poll_interval, remaining))
        else:
            time.sleep(poll_interval)

        pending = next_pending

    return True
