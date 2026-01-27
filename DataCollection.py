import time
import csv
import signal
import sys
import numpy as np

# 模块化导入
from sensors import FTSensor, IMUSensor, CameraSensor
from algorithms.gravity_compensation import GravityCompensator

# --- 配置参数 ---
CONFIG = {
    "ft_port": "COM3",
    "imu_port": "COM4",
    "cam_id": 0,
    "freq": 60,              # 采集主频
    "mass": 0.25,            # 负载质量 (kg)
    "com": [-0.008, 0.004, 0.094], # 重心 (m)
    "filename": f"dataset_{time.strftime('%Y%m%d_%H%M%S')}.csv"
}

is_running = True
def signal_handler(sig, frame):
    global is_running
    is_running = False

def main():
    signal.signal(signal.SIGINT, signal_handler)
    
    # 1. 实例化传感器
    # 即使后续要加 ORB-SLAM3，也只需在这里加一个 sensor 实例
    ft = FTSensor(CONFIG["ft_port"])
    imu = IMUSensor(CONFIG["imu_port"])
    cam = CameraSensor(CONFIG["cam_id"])
    
    all_sensors = [ft, imu, cam]
    
    # 2. 实例化算法
    algo = GravityCompensator(CONFIG["mass"], CONFIG["com"])
    
    # 3. 启动硬件
    print("=== 系统启动中 ===")
    for s in all_sensors:
        s.start()
    
    time.sleep(2) # 等待传感器稳定
    
    # 4. 执行静态校准
    print("\n[请保持静止] 正在校准传感器零偏 (3秒)...")
    calib_f, calib_q = [], []
    t_start = time.time()
    while time.time() - t_start < 3.0:
        f_d, _, _ = ft.get_data()
        i_d, _, _ = imu.get_data()
        if f_d is not None and i_d is not None:
            calib_f.append(f_d[:3])     # Fx,Fy,Fz
            calib_q.append(i_d[6:])     # Qw,Qx,Qy,Qz
        time.sleep(0.01)
    
    if not algo.calibrate_bias(calib_f, calib_q):
        print("校准失败，正在退出...")
        for s in all_sensors: s.stop()
        return

    # 5. 主采集循环
    print(f"\n=== 开始采集 ({CONFIG['freq']}Hz) ===")
    print(f"数据保存至: {CONFIG['filename']}")
    print("按 Ctrl+C 停止...")
    
    headers = [
        "SysTime", 
        "FT_Time", "Fx_Raw", "Fy_Raw", "Fz_Raw", "Tx", "Ty", "Tz", "Fx_Pure", "Fy_Pure", "Fz_Pure",
        "IMU_Time", "Ax", "Ay", "Az", "Gx", "Gy", "Gz", "Qw", "Qx", "Qy", "Qz",
        "Cam_Time", "Frame_ID"
    ]
    
    with open(CONFIG["filename"], 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        
        loop_interval = 1.0 / CONFIG["freq"]
        
        while is_running:
            loop_start = time.perf_counter()
            current_sys_time = time.time()
            
            # --- 快照数据 (Snapshot) ---
            # 这一步极快，只是从内存读变量，不涉及IO等待
            ft_data, ft_ts, _ = ft.get_data()
            imu_data, imu_ts, _ = imu.get_data()
            cam_frame_id, cam_ts, _ = cam.get_data()
            
            # 数据判空与默认值填充
            if ft_data is None: ft_data = np.zeros(6)
            if imu_data is None: imu_data = np.zeros(10)
            if cam_frame_id is None: cam_frame_id = -1
            
            # --- 算法计算 ---
            # 主循环只做计算，不做繁重的IO
            f_raw = ft_data[:3]
            quat = imu_data[6:]
            f_pure, f_grav = algo.process(f_raw, quat)
            
            # --- 写入文件 ---
            row = [
                f"{current_sys_time:.4f}",
                # FT
                f"{ft_ts:.4f}", 
                *[f"{x:.3f}" for x in ft_data],     # Raw Force/Torque
                *[f"{x:.3f}" for x in f_pure],      # Pure Force
                # IMU
                f"{imu_ts:.4f}",
                *[f"{x:.3f}" for x in imu_data],    # Acc/Gyro/Quat
                # Camera
                f"{cam_ts:.4f}", cam_frame_id
            ]
            writer.writerow(row)
            
            # --- 终端监控 ---
            print(f"\r[Run] Video:{cam_frame_id}帧 | Fz_Pure:{f_pure[2]:6.2f}N | Fz_Grav:{f_grav[2]:6.2f}N", end="")
            
            # --- 控频 ---
            elapsed = time.perf_counter() - loop_start
            sleep_time = max(0, loop_interval - elapsed)
            time.sleep(sleep_time)

    # 6. 清理
    print("\n\n正在停止所有传感器...")
    for s in all_sensors:
        s.stop()
    print("完成。")

if __name__ == "__main__":
    main()