import struct
import numpy as np
from .common.serial_base import SerialBaseSensor

class IMUSensor(SerialBaseSensor):
    def __init__(self, port='COM4', baudrate=115200):
        # 输出: [Ax, Ay, Az, Gx, Gy, Gz, Qw, Qx, Qy, Qz] (共10维)
        super().__init__("IMU", port, baudrate, data_length=10)
        
        # 临时变量，用于跨包组装
        self._temp_acc = np.zeros(3)
        self._temp_gyro = np.zeros(3)
        self._temp_quat = np.array([1.0, 0.0, 0.0, 0.0])

    def _parse_protocol(self, buffer):
        complete_frame = None
        
        while len(buffer) >= 3:
            if buffer[0] != 0x7E or buffer[1] != 0x23:
                buffer.pop(0) # 移出无效字节
                continue
            
            pkt_len = buffer[2]
            if len(buffer) < pkt_len:
                break # 数据不够，等待下次
            
            # 提取一个完整包
            packet = buffer[:pkt_len]
            buffer = buffer[pkt_len:]

            # 校验
            if (sum(packet[:-1]) & 0xFF) != packet[-1]:
                continue

            func = packet[3]
            
            # 0x04: 原始数据 (Acc, Gyro)
            if func == 0x04: 
                v = struct.unpack('<hhhhhhhhh', packet[4:22])
                # 根据你之前的代码：Acc单位 g转m/s^2, 且取反; Gyro单位 deg/s 转 rad/s
                # 注意：这里请根据你实际使用的 IMU 坐标系确认是否需要 * -1.0
                self._temp_acc = np.array(v[0:3]) * (16.0 / 32767.0) * 9.81 * -1.0
                self._temp_gyro = np.array(v[3:6]) * (2000.0 / 32767.0) * (np.pi / 180.0)
            
            # 0x16: 四元数
            elif func == 0x16:
                self._temp_quat = np.array(struct.unpack('<ffff', packet[4:20]))
            
            # 0x26: 欧拉角 (视作帧尾同步信号)
            elif func == 0x26:
                # 收到这个包，说明这一时刻的所有数据已齐备
                # 组装完整帧：Acc(3) + Gyro(3) + Quat(4)
                complete_frame = np.concatenate([
                    self._temp_acc, self._temp_gyro, self._temp_quat
                ])
                
        return complete_frame, buffer
