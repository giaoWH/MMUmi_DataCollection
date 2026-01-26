import sys
import time
import numpy as np
# 引入你提供的驱动文件
try:
    from sensor_driver import FTSensorDriver, IMUDriver
except ImportError:
    print("错误: 找不到 sensor_driver.py，请确保该文件在当前目录下。")
    sys.exit(1)


class GravityCompensator:
    def __init__(self, mass_kg, com_pos_meter, port_ft, port_imu):
        """
        初始化重力补偿器
        :param mass_kg: 负载质量 (kg)
        :param com_pos_meter: 重心位置 [x, y, z] (米)
        :param port_ft: 六维力传感器串口号
        :param port_imu: IMU串口号
        """
        self.mass = mass_kg
        self.com = np.array(com_pos_meter)
        self.g_value = 9.81  # 重力加速度 m/s^2

        # 1. 初始化驱动
        self.ft_driver = FTSensorDriver(port=port_ft)
        self.imu_driver = IMUDriver(port=port_imu)

        # 2. 状态变量
        self.bias_force = np.zeros(3)  # Fxyz 的 Bias
        self.is_calibrated = False

        # 定义世界坐标系下的重力向量 (假设 Z 轴垂直向上，重力为负)
        self.G_world_vector = np.array([0.0, 0.0, -1.0]) * self.g_value

    def start_drivers(self):
        """启动底层驱动线程"""
        print("[System] 正在启动传感器驱动...")
        self.ft_driver.start()
        self.imu_driver.start()
        time.sleep(2)  # 等待数据稳定
        print("[System] 驱动已就绪")

    def stop_drivers(self):
        """停止驱动"""
        self.ft_driver.stop()
        self.imu_driver.stop()

    def _quat_to_rot_matrix(self, q):
        """
        将四元数转换为旋转矩阵
        q = [w, x, y, z]
        """
        w, x, y, z = q
        xx = x * x
        yy = y * y
        zz = z * z
        xy = x * y
        xz = x * z
        yz = y * z
        xw = x * w
        yw = y * w
        zw = z * w

        R = np.array([
            [1 - 2 * (yy + zz), 2 * (xy - zw), 2 * (xz + yw)],
            [2 * (xy + zw), 1 - 2 * (xx + zz), 2 * (yz - xw)],
            [2 * (xz - yw), 2 * (yz + xw), 1 - 2 * (xx + yy)]
        ])
        return R

    def calculate_gravity_in_sensor_frame(self, quat):
        """
        根据当前四元数，计算重力在传感器坐标系下的分量
        """
        norm = np.linalg.norm(quat)
        if norm < 1e-6:
            return np.zeros(3)
        q_norm = quat / norm

        # R 代表 Body 到 World 的旋转
        R = self._quat_to_rot_matrix(q_norm)

        # 世界系重力向量
        g_world = np.array([0.0, 0.0, -1.0]) * self.g_value

        # 映射到传感器系: F_g = R.T @ g_world
        g_sensor = R.T @ g_world

        return g_sensor * self.mass

    def calibrate_bias(self, duration=3.0):
        """
        执行静态校准：Bias = Avg(F_measured) - Avg(F_gravity_model)
        """
        print(f"[Calibration] 开始 {duration} 秒静态校准，请勿触碰夹爪...")

        start_time = time.time()
        f_samples = []
        g_samples = []

        while (time.time() - start_time) < duration:
            ft_data, _ = self.ft_driver.get_latest_data()
            imu_state = self.imu_driver.get_full_state()
            quat = imu_state['quat']

            if np.all(ft_data == 0) and np.all(quat == 0):
                time.sleep(0.01)
                continue

            f_curr = ft_data[:3]
            f_samples.append(f_curr)

            f_g_curr = self.calculate_gravity_in_sensor_frame(quat)
            g_samples.append(f_g_curr)

            time.sleep(0.01)

        if len(f_samples) == 0:
            print("[Calibration] 错误：未采集到有效数据！")
            return False

        avg_f = np.mean(f_samples, axis=0)
        avg_g = np.mean(g_samples, axis=0)

        self.bias_force = avg_f - avg_g

        self.is_calibrated = True
        print(f"[Calibration] 校准完成")
        print(f"  > 测量力均值 (F_raw_avg): {avg_f}")
        print(f"  > 理论重力 (F_g_avg):     {avg_g}")
        print(f"  > 计算出的 Bias:          {self.bias_force}")
        print("-" * 60)
        return True

    def get_compensated_reading(self):
        """
        获取数据
        :return: (F_pure, T_raw, F_raw, F_gravity)
                 F_pure:    补偿后的纯交互力
                 T_raw:     原始力矩
                 F_raw:     原始力（用于调试）
                 F_gravity: 计算出的当前重力分量（用于调试）[新增]
        """
        if not self.is_calibrated:
            print("[Warning] 尚未校准！")
            # 更新返回值数量以匹配修改
            return np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3)

        # 1. 获取最新原始数据
        ft_data, _ = self.ft_driver.get_latest_data()
        imu_state = self.imu_driver.get_full_state()
        quat = imu_state['quat']

        f_raw = ft_data[:3]
        t_raw = ft_data[3:]

        # 2. 计算重力
        f_gravity_now = self.calculate_gravity_in_sensor_frame(quat)

        # 3. 计算纯净交互力
        # Formula: F_pure = F_raw - F_gravity_now - Bias
        f_pure = f_raw - f_gravity_now - self.bias_force

        # [修改] 返回值增加了 f_gravity_now
        return f_pure, t_raw, f_raw, f_gravity_now


def main():
    # === 用户配置区 ===
    LOAD_MASS = 0.25  # kg
    COM_POS = [-0.00834301, 0.00394673, 0.0939077]  # meter

    # 串口设置
    PORT_FT = 'COM3'
    PORT_IMU = 'COM4'

    # [新增选项] 是否输出原始力 Fraw 以便调试
    SHOW_RAW_DEBUG = True

    # 实例化
    compensator = GravityCompensator(LOAD_MASS, COM_POS, PORT_FT, PORT_IMU)

    try:
        compensator.start_drivers()

        if not compensator.calibrate_bias(duration=10.0):
            return

        print("\n=== 开始实时输出 (Ctrl+C 停止) ===")
        if SHOW_RAW_DEBUG:
            # [修改] 表头增加 "重力 F_g"
            print("格式: [纯力 F_pure]  |  [原始力 F_raw]  |  [重力 F_g]  |  [力矩 T_raw]")
        else:
            print("格式: [纯力 F_pure]  |  [力矩 T_raw]")

        while True:
            start_loop = time.perf_counter()

            # [修改] 接收 4 个返回值
            f_pure, t_raw, f_raw, f_gravity = compensator.get_compensated_reading()

            # 格式化字符串
            f_pure_str = f"[{f_pure[0]:6.2f}, {f_pure[1]:6.2f}, {f_pure[2]:6.2f}]"
            t_str = f"[{t_raw[0]:.3f}, {t_raw[1]:.3f}, {t_raw[2]:.3f}]"
            f_raw_str = f"[{f_raw[0]:6.2f}, {f_raw[1]:6.2f}, {f_raw[2]:6.2f}]"
            # [新增] 格式化重力分量
            f_g_str = f"[{f_gravity[0]:6.2f}, {f_gravity[1]:6.2f}, {f_gravity[2]:6.2f}]"

            if SHOW_RAW_DEBUG:
                # [修改] 打印包含重力的信息
                print(f"\r纯力:{f_pure_str} | 原力:{f_raw_str} | 重力:{f_g_str} | 力矩:{t_str}", end="")
            else:
                # 打印: Pure | Torque
                print(f"\r力:{f_pure_str} | 力矩:{t_str}", end="")

            # 50Hz 刷新
            elapsed = time.perf_counter() - start_loop
            time.sleep(max(0, 0.02 - elapsed))

    except KeyboardInterrupt:
        print("\n\n[System] 用户停止程序")
    finally:
        compensator.stop_drivers()


if __name__ == "__main__":
    main()