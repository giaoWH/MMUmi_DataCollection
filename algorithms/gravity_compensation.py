import numpy as np

class GravityCompensator:
    def __init__(self, mass, com_pos):
        self.mass = mass            # 负载质量 (kg)
        self.com = np.array(com_pos)# 重心位置 (m)
        self.g_val = 9.81           # 重力加速度
        self.bias = np.zeros(3)     # 零偏
        self.is_calibrated = False

    def calibrate_bias(self, f_data_list, q_data_list):
        """
        静态校准 Bias。
        f_data_list: N x 3 array (Fx, Fy, Fz)
        q_data_list: N x 4 array (w, x, y, z)
        """
        if len(f_data_list) < 10:
            print("[Algo] 校准数据不足")
            return False

        f_avg = np.mean(f_data_list, axis=0)
        
        # 计算这一段时间内理论重力分量的均值
        g_forces = []
        for q in q_data_list:
            g_forces.append(self._compute_gravity_in_body_frame(q))
        g_avg = np.mean(g_forces, axis=0)
        
        # Bias = 实测均值 - 理论均值
        self.bias = f_avg - g_avg
        self.is_calibrated = True
        print(f"[Algo] 校准完成, Bias: {self.bias}")
        return True

    def process(self, f_raw, quat):
        """
        计算补偿后的力。
        Returns: (f_pure, f_gravity_component)
        """
        if not self.is_calibrated:
            return f_raw, np.zeros(3)
        
        # 1. 计算当前姿态下的重力分量
        f_g = self._compute_gravity_in_body_frame(quat)
        
        # 2. 补偿: 纯力 = 原始力 - 重力分量 - 零偏
        f_pure = f_raw - f_g - self.bias
        
        return f_pure, f_g

    def _compute_gravity_in_body_frame(self, q):
        """
        将世界坐标系下的重力向量 [0, 0, -mg] 映射到传感器坐标系。
        q: [w, x, y, z] (Body -> World 的旋转)
        """
        w, x, y, z = q
        # 四元数转旋转矩阵 R (简化版)
        # R * v_body = v_world  =>  v_body = R.T * v_world
        
        # 预计算一些乘积
        xx, yy, zz = x*x, y*y, z*z
        xy, xz, yz = x*y, x*z, y*z
        xw, yw, zw = x*w, y*w, z*w
        
        # 我们只需要 R 的转置 (World -> Body)
        # R_T = R.transpose()
        R_T = np.array([
            [1-2*(yy+zz),   2*(xy+zw),   2*(xz-yw)],
            [2*(xy-zw),   1-2*(xx+zz),   2*(yz+xw)],
            [2*(xz+yw),   2*(yz-xw),   1-2*(xx+yy)]
        ]).T

        g_world = np.array([0.0, 0.0, -1.0]) * self.mass * self.g_val
        return R_T @ g_world