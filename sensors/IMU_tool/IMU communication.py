import serial
import struct
import math
import signal
import csv
import time
import os
import sys

# ================= 配置区域 =================
SERIAL_PORT = 'COM4'  # 您的端口
BAUD_RATE = 115200  # 波特率
CSV_FILENAME = f"IMU_Sync_{time.strftime('%Y%m%d_%H%M%S')}.csv"
# ===========================================

is_running = True


def signal_handler(_sig, _frame):
    global is_running
    is_running = False


def get_timestamp_str():
    """生成 HH:MM:SS.mmm 时间戳"""
    t = time.time()
    local_t = time.localtime(t)
    ms = int((t % 1) * 1000)
    return time.strftime("%H:%M:%S", local_t) + f".{ms:03d}"


class IMUState:
    def __init__(self):
        self.acc = [0.0, 0.0, 0.0]
        self.gyro = [0.0, 0.0, 0.0]
        self.mag = [0.0, 0.0, 0.0]
        self.euler = [0.0, 0.0, 0.0]
        self.quat = [1.0, 0.0, 0.0, 0.0]

        self.packet_count = 0
        self.frame_ready = False  # 新增：帧就绪标志


class IMUProtocolParser:
    def __init__(self, state_obj):
        self.buffer = bytearray()
        self.state = state_obj

    def add_data(self, data: bytes):
        self.buffer.extend(data)
        self.process_buffer()

    def process_buffer(self):
        while len(self.buffer) >= 3:
            if self.buffer[0] != 0x7E or self.buffer[1] != 0x23:
                self.buffer.pop(0)
                continue

            packet_len = self.buffer[2]
            if len(self.buffer) < packet_len:
                break

            packet = self.buffer[:packet_len]
            self.parse_packet(packet)
            self.buffer = self.buffer[packet_len:]

    def verify_checksum(self, packet):
        if len(packet) < 4: return False
        return (sum(packet[:-1]) & 0xFF) == packet[-1]

    def parse_packet(self, packet):
        if not self.verify_checksum(packet): return

        self.state.packet_count += 1
        func_code = packet[3]

        try:
            if func_code == 0x04:  # 原始数据 (Acc, Gyro, Mag)
                v = struct.unpack('<hhhhhhhhh', packet[4:22])
                self.state.acc = [x * (16.0 / 32767.0) for x in v[0:3]]
                self.state.gyro = [x * (2000.0 / 32767.0) * (math.pi / 180.0) for x in v[3:6]]
                self.state.mag = [x * (800.0 / 32767.0) for x in v[6:9]]

            elif func_code == 0x16:  # 四元数
                self.state.quat = struct.unpack('<ffff', packet[4:20])

            elif func_code == 0x26:  # 欧拉角
                rpy = struct.unpack('<fff', packet[4:16])
                self.state.euler = [math.degrees(x) for x in rpy]

                # 【关键修改】
                # 通常欧拉角是每一帧最后发送的包。
                # 当收到欧拉角时，意味着这一时刻的所有数据（Raw, Quat, Euler）都已更新完毕。
                # 此时置起“帧就绪”标志，通知主循环进行保存。
                self.state.frame_ready = True

        except:
            pass


def main():
    global is_running
    signal.signal(signal.SIGINT, signal_handler)

    state = IMUState()
    parser = IMUProtocolParser(state)

    try:
        f = open(CSV_FILENAME, 'w', newline='', encoding='utf-8')
        writer = csv.writer(f)
        writer.writerow(
            ["Time_HMS", "Roll", "Pitch", "Yaw", "Ax", "Ay", "Az", "Gx", "Gy", "Gz", "Q0", "Q1", "Q2", "Q3"])
    except Exception as e:
        print(f"创建文件失败: {e}")
        return

    print(f"=== 帧同步记录模式 (稳定100Hz) ===")
    print(f"端口: {SERIAL_PORT}")
    print(f"文件: {CSV_FILENAME}")
    print("说明: 仅在收到完整一帧数据后保存，消除重复行。")
    print("-" * 100)

    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0)

        while is_running:
            # 1. 串口读取
            if ser.in_waiting:
                data = ser.read(ser.in_waiting)
                parser.add_data(data)

            # 2. 检查帧是否就绪
            if state.frame_ready:
                # 清除标志，防止重复保存
                state.frame_ready = False

                ts_str = get_timestamp_str()

                # --- 写入 CSV ---
                writer.writerow([
                    ts_str,
                    *[f"{x:.3f}" for x in state.euler],
                    *[f"{x:.3f}" for x in state.acc],
                    *[f"{x:.3f}" for x in state.gyro],
                    *[f"{x:.3f}" for x in state.quat]
                ])

                # --- 刷新屏幕 ---
                euler_str = f"RPY:{state.euler[0]:6.2f} {state.euler[1]:6.2f} {state.euler[2]:6.2f}"
                acc_str = f"Acc:{state.acc[0]:5.2f} {state.acc[1]:5.2f} {state.acc[2]:5.2f}"

                line = f"\r[{ts_str}] {euler_str} | {acc_str}    "
                sys.stdout.write(line)
                sys.stdout.flush()

    except Exception as e:
        print(f"\nError: {e}")
    finally:
        if 'ser' in locals() and ser.is_open: ser.close()
        if 'f' in locals(): f.close()
        print(f"\n\n已保存至: {CSV_FILENAME}")


if __name__ == "__main__":
    main()