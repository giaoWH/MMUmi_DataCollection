import serial
import threading
import time
import struct
import numpy as np


class SerialSensorDriver:
    """
    通用串口传感器驱动基类
    负责：线程管理、串口连接、数据锁保护
    [修改] 增加了时间戳支持
    """

    def __init__(self, port, baudrate, data_length):
        self.port = port
        self.baudrate = baudrate
        self.data_length = data_length
        self.running = False
        self.latest_data = np.zeros(data_length)
        # [新增] 记录数据到达的系统时间
        self.latest_timestamp = 0.0
        self.lock = threading.Lock()
        self.thread = None
        self._ser = None
        self.simulation_mode = False  # 如果串口打开失败，自动进入模拟模式

    def start(self):
        try:
            self._ser = serial.Serial(self.port, self.baudrate, timeout=1)
            self.running = True
            print(f"[Driver] 成功连接设备: {self.port} @ {self.baudrate}")
        except serial.SerialException as e:
            print(f"[Driver] 警告: 无法打开串口 {self.port} ({e})")
            print(f"[Driver] -> 自动切换到【模拟仿真模式】生成虚拟数据")
            self.simulation_mode = True
            self.running = True

        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        if self._ser and self._ser.is_open:
            self._ser.close()
        print(f"[Driver] 设备 {self.port} 已停止")

    def get_latest_data(self):
        """
        线程安全地获取最新一帧数据
        [修改] 返回元组 (data, timestamp)
        """
        with self.lock:
            return self.latest_data.copy(), self.latest_timestamp

    def _worker(self):
        """后台接收循环"""
        while self.running:
            if self.simulation_mode:
                # 模拟模式：生成符合物理规律的噪声数据
                sim_data = self._simulate_physically_correct_data()
                with self.lock:
                    self.latest_data = sim_data
                    # [新增] 模拟模式也要更新时间戳
                    self.latest_timestamp = time.time()
                time.sleep(0.01)  # 模拟 100Hz
                continue

            # 真实硬件模式
            try:
                # 示例：寻找帧头 (根据实际协议修改，例如 0xAA 0x55)
                # 这里假设简单协议：直接读取固定字节数（不推荐，建议加校验）
                if self._ser.in_waiting >= 20:  # 假设一帧至少20字节
                    # 在读取IO之前/瞬间记录“到达时间”
                    arrival_time = time.time()

                    raw_data = self._ser.read(self._ser.in_waiting)
                    parsed_data = self._parse_protocol(raw_data)

                    if parsed_data is not None and len(parsed_data) == self.data_length:
                        with self.lock:
                            self.latest_data = parsed_data
                            # 使用刚才记录的到达时间，而不是当前时间
                            self.latest_timestamp = arrival_time
                else:
                    time.sleep(0.001)  # 防止空转
            except Exception as e:
                print(f"[Driver] 读取错误: {e}")
                time.sleep(1)

    def _parse_protocol(self, raw_bytes):
        """[虚函数] 由子类实现具体的协议解析"""
        raise NotImplementedError

    def _simulate_physically_correct_data(self):
        """[虚函数] 由子类实现符合特定坐标系的模拟数据"""
        raise NotImplementedError


class FTSensorDriver(SerialSensorDriver):
    """
    基于 P140000104-485 协议的六维力传感器驱动
    """
    # === 协议常量定义 ===
    # 开启连续上传 (100Hz)
    CMD_START = bytes.fromhex("09 10 01 9A 00 01 02 02 00 CD CA")
    # 停止上传 (发送 55 个 0xFF)
    CMD_STOP = b'\xFF' * 55
    # 帧头 (0x20 0x4E)
    FRAME_HEADER = b'\x20\x4E'
    # 帧长度 (Header 2 + Data 12 + CRC 2 = 16 bytes)
    FRAME_LEN = 16

    def __init__(self, port='COM3', baudrate=115200):
        # 输出: [Fx, Fy, Fz, Tx, Ty, Tz]
        super().__init__(port, baudrate, data_length=6)
        # 内部缓冲区，用于处理串口数据粘包/断包
        self._buffer = bytearray()

    def start(self):
        """重写启动方法：打开串口后发送开启指令"""
        super().start()  # 启动线程
        time.sleep(0.1)  # 等待串口稳定
        if self._ser and self._ser.is_open:
            try:
                # 清空缓冲区，防止处理旧数据
                self._ser.reset_input_buffer()
                # 发送开始指令
                self._ser.write(self.CMD_START)
                print(f"[FTSensor] 已发送 100Hz 开启指令: {self.port}")
            except Exception as e:
                print(f"[FTSensor] 发送开启指令失败: {e}")

    def stop(self):
        """重写停止方法：关闭前发送停止指令"""
        if self._ser and self._ser.is_open:
            try:
                self._ser.write(self.CMD_STOP)
                print(f"[FTSensor] 已发送停止指令")
                time.sleep(0.05)  # 等待指令发送完毕
            except Exception as e:
                print(f"[FTSensor] 发送停止指令失败: {e}")
        super().stop()

    def _parse_protocol(self, raw_bytes):
        """
        解析逻辑 (移植自 FT-sensor communication.py)
        输入: raw_bytes (最新读取的一段串口数据)
        输出: 最新一帧解析好的 numpy 数组，如果缓冲区数据不足则返回 None
        """
        # 1. 将新数据拼接到内部缓冲区
        self._buffer.extend(raw_bytes)

        last_valid_frame = None

        # 2. 循环处理缓冲区，直到数据不足一帧
        while len(self._buffer) >= self.FRAME_LEN:
            # 查找帧头
            header_index = self._buffer.find(self.FRAME_HEADER)

            if header_index == -1:
                # 没找到帧头，保留最后一个字节（防止把帧头 0x20 0x4E 切断），其余丢弃
                self._buffer = self._buffer[-1:]
                break

            if header_index > 0:
                # 丢弃帧头前面的垃圾数据
                self._buffer = self._buffer[header_index:]

            # 再次检查长度（丢弃垃圾数据后可能不足一帧）
            if len(self._buffer) < self.FRAME_LEN:
                break

            # === 提取数据 ===
            # Payload 索引: Header(0-1) -> Data(2-13) -> CRC(14-15)
            payload = self._buffer[2:14]

            # 从缓冲区移除已处理的这一帧
            self._buffer = self._buffer[self.FRAME_LEN:]

            try:
                # 解析: Little Endian (<), 6个 short (h)
                # 对应协议: Fx, Fy, Fz, Mx, My, Mz (注意：你的脚本里顺序也是 Fx,Fy,Fz,Tx,Ty,Tz)
                raw_ints = struct.unpack('<6h', payload)

                # 物理量换算 (参考协议 PDF 与 你的脚本)
                # 力: / 100.0, 力矩: / 1000.0
                fx = raw_ints[0] / 100.0
                fy = raw_ints[1] / 100.0
                fz = raw_ints[2] / 100.0
                tx = raw_ints[3] / 1000.0
                ty = raw_ints[4] / 1000.0
                tz = raw_ints[5] / 1000.0

                # 更新最新帧 (循环直到处理完缓冲区所有数据，只返回最新的)
                last_valid_frame = np.array([fx, fy, fz, tx, ty, tz])

            except struct.error:
                print("[FTSensor] 数据解包错误")
                continue

        return last_valid_frame

    def _simulate_physically_correct_data(self):
        """保持模拟功能以便在无硬件时测试流程"""
        # 模拟：重力作用在 Y 轴 (假设 1.5kg 负载)
        target = np.array([0.0, 14.7, 0.0, 0.0, 0.0, 0.0])
        noise = np.random.normal(0, 0.05, 6)
        return target + noise


class IMUDriver(SerialSensorDriver):
    """
    9轴 IMU 驱动
    协议：0x7E 0x23 开头，支持 Raw(0x04), Quat(0x16), Euler(0x26)
    [修改] 增加了坐标对齐功能
    """

    def __init__(self, port='COM4', baudrate=115200):
        # 核心数据: [Ax, Ay, Az, Gx, Gy, Gz]
        # 注意：这里我们使用 6 维数据长度来兼容 SerialSensorDriver 的 latest_data
        super().__init__(port, baudrate, data_length=6)

        # 内部解析缓冲
        self._buffer = bytearray()

        # 暂存当前帧的数据 (等待帧同步)
        # 在接收到 0x26 (Euler) 帧尾之前，数据暂存在这里
        self._temp_acc = np.zeros(3)
        self._temp_gyro = np.zeros(3)
        self._temp_mag = np.zeros(3)
        self._temp_quat = np.array([1.0, 0.0, 0.0, 0.0])
        self._temp_euler = np.zeros(3)

        # 对外公开的完整状态 (线程安全需注意，这里主要供调试读取)
        # 包含：acc, gyro, mag, quat, euler
        self.latest_full_state = {
            "acc": np.zeros(3),
            "gyro": np.zeros(3),
            "mag": np.zeros(3),
            "quat": np.zeros(4),
            "euler": np.zeros(3)
        }

        # [新增] IMU 到传感器的旋转矩阵，默认为单位阵
        self.R_imu_to_sensor = np.eye(3)

    def set_orientation(self, matrix):
        """[新增] 设置 IMU 安装旋转矩阵"""
        self.R_imu_to_sensor = np.array(matrix)
        print(f"[IMUDriver] 已设置安装旋转矩阵:\n{self.R_imu_to_sensor}")

    def get_full_state(self):
        """获取包含欧拉角、磁力计的完整数据 (字典副本)"""
        with self.lock:
            return self.latest_full_state.copy()

    def _parse_protocol(self, raw_bytes):
        """
        基于 IMUProtocolParser 逻辑实现的协议解析
        输入: 串口原始字节流
        输出: 最新一帧解析好的 numpy 数组 [Ax, Ay, Az, Gx, Gy, Gz]，如果数据不足则返回 None
        说明: 由基类 SerialSensorDriver 调用并根据返回值更新 self.latest_data
        """
        self._buffer.extend(raw_bytes)

        last_valid_frame = None

        # 循环处理缓冲区
        while len(self._buffer) >= 3:
            # 1. 检查帧头 0x7E 0x23
            if self._buffer[0] != 0x7E or self._buffer[1] != 0x23:
                self._buffer.pop(0)
                continue

            # 2. 检查长度 (Byte 2 是长度)
            packet_len = self._buffer[2]
            if len(self._buffer) < packet_len:
                break  # 数据不够，等待下次

            # 3. 提取完整包并解析
            packet = self._buffer[:packet_len]
            self._buffer = self._buffer[packet_len:]  # 移除已处理数据

            # 处理包，并判断是否为帧尾 (Euler包)
            if self._process_packet(packet):
                # 收到欧拉角包，认为这一时刻的所有数据(Raw+Quat+Euler)已齐备
                # 此时生成核心数据返回给基类

                # [修改] 在这里应用坐标变换 R_imu_to_sensor
                acc_aligned = self.R_imu_to_sensor @ self._temp_acc
                gyro_aligned = self.R_imu_to_sensor @ self._temp_gyro

                last_valid_frame = np.concatenate([acc_aligned, gyro_aligned])

        # 返回最后一帧完整数据 (如果没有完整帧则为 None，基类不会更新)
        return last_valid_frame

    def _process_packet(self, packet):
        """
        处理单包数据
        Return: True 表示本包是帧尾(Euler)，意味着一帧数据处理完毕；False 表示普通包
        """
        # 校验和: 从包头累加到倒数第二个字节，取低8位
        if (sum(packet[:-1]) & 0xFF) != packet[-1]:
            return False  # 校验失败

        func_code = packet[3]
        is_frame_complete = False

        try:
            if func_code == 0x04:  # 原始数据 (Acc, Gyro, Mag)
                # 9个 int16 (short)
                v = struct.unpack('<hhhhhhhhh', packet[4:22])

                # === 物理量转换 ===
                # 1. Acc: 16g量程 -> 16.0 / 32767.0
                # 重要：转换为 m/s^2 以匹配动力学核心库的要求
                # 假设传感器输出单位为 g (9.81 m/s^2)
                self._temp_acc = np.array(v[0:3]) * (16.0 / 32767.0) * 9.81 * -1.0

                # 2. Gyro: 2000dps -> (2000/32767) * (pi/180) -> rad/s
                self._temp_gyro = np.array(v[3:6]) * (2000.0 / 32767.0) * (np.pi / 180.0)

                # 3. Mag
                self._temp_mag = np.array(v[6:9]) * (800.0 / 32767.0)

            elif func_code == 0x16:  # 四元数
                # 4个 float
                self._temp_quat = np.array(struct.unpack('<ffff', packet[4:20]))

                # [核心修复] 即使没有欧拉角包，也强制更新状态，确保补偿算法能拿到数据
                with self.lock:
                    self.latest_full_state["quat"] = self._temp_quat.copy()

            elif func_code == 0x26:  # 欧拉角 (作为帧结束标志)
                # 3个 float
                rpy = struct.unpack('<fff', packet[4:16])
                # 转换为角度 (便于调试阅读)，内部计算不使用
                self._temp_euler = np.array([np.degrees(x) for x in rpy])

                # === 关键：帧同步更新 ===
                # 收到欧拉角包，认为这一时刻的所有数据(Raw+Quat+Euler)已齐备
                is_frame_complete = True

                # 仅在此处更新调试用的完整状态
                # (核心 latest_data 不在此处更新，而是通过返回 True 让 parse_protocol 处理)
                with self.lock:
                    self.latest_full_state = {
                        "acc": self._temp_acc.copy(),
                        "gyro": self._temp_gyro.copy(),
                        "mag": self._temp_mag.copy(),
                        "quat": self._temp_quat.copy(),
                        "euler": self._temp_euler.copy()
                    }

        except Exception as e:
            # 忽略解析错误，防止单帧损坏导致崩溃
            pass

        return is_frame_complete

    def _simulate_physically_correct_data(self):
        """
        模拟模式：生成符合物理规律的 IMU 数据
        场景：静止状态，Y轴向下 (重力方向)
        """
        # 理论加速度: [0, -9.81, 0] (m/s^2)
        target_acc = np.array([0.0, -9.81, 0.0])
        # 理论角速度: 0
        target_gyro = np.zeros(3)

        # 添加噪声
        noise = np.random.normal(0, 0.02, 6)
        sim_data = np.concatenate([target_acc, target_gyro]) + noise

        # 模拟模式下不填充 full_state 或仅维持默认值
        return sim_data