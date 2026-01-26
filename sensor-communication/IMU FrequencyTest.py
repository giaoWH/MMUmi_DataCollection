import serial
import time
import sys

# ================= 配置区域 =================
SERIAL_PORT = 'COM4'  # 端口号
BAUD_RATE = 115200  # 波特率
TEST_DURATION = 3.0  # 测试时长(秒)


# ===========================================

def main():
    print(f"=== IMU 频率精准测试工具 ===")
    print(f"端口: {SERIAL_PORT} | 波特率: {BAUD_RATE}")
    print(f"即将进行 {TEST_DURATION} 秒的采样测试...")
    print("-" * 50)

    try:
        # 打开串口
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)

        # 清空缓冲区，避免积压数据影响第一次计时
        ser.reset_input_buffer()

        print("正在采集数据，请稍候", end="")

        # 初始化变量
        buffer = bytearray()
        start_time = time.time()

        # 计数器字典
        counts = {
            0x04: 0,  # 原始数据 (Acc+Gyro+Mag)
            0x16: 0,  # 四元数
            0x26: 0,  # 欧拉角 (以此代表真实采样率)
            'other': 0
        }

        # === 主循环 ===
        while (time.time() - start_time) < TEST_DURATION:
            # 打印进度条动画
            if int((time.time() - start_time) * 10) % 5 == 0:
                print(".", end="", flush=True)

            # 读取数据
            if ser.in_waiting:
                buffer.extend(ser.read(ser.in_waiting))

                # 解析循环
                while len(buffer) >= 3:
                    # 1. 检查包头
                    if buffer[0] != 0x7E or buffer[1] != 0x23:
                        buffer.pop(0)
                        continue

                    # 2. 获取长度
                    pkt_len = buffer[2]

                    # 3. 数据不够，跳出等待
                    if len(buffer) < pkt_len:
                        break

                    # 4. 提取功能字 (第4个字节，下标3)
                    func_code = buffer[3]

                    # 5. 分类计数
                    if func_code in counts:
                        counts[func_code] += 1
                    else:
                        counts['other'] += 1

                    # 6. 移除已处理的包
                    buffer = buffer[pkt_len:]

            # 稍微休眠，给 CPU 喘息
            time.sleep(0.001)

        # === 计算结果 ===
        end_time = time.time()
        actual_duration = end_time - start_time

        print("\n" + "-" * 50)
        print("【测试结果】")
        print(f"实际测试时长: {actual_duration:.3f} 秒")
        print("-" * 20)

        # 1. 各包统计
        print(f"原始数据包 (0x04): {counts[0x04]} 个")
        print(f"四元数包   (0x16): {counts[0x16]} 个")
        print(f"欧拉角包   (0x26): {counts[0x26]} 个")

        total_packets = sum(counts.values())
        print(f"总接收包数       : {total_packets} 个")
        print("-" * 20)

        # 2. 频率计算
        # 使用欧拉角包的数量作为“帧数”
        real_freq = counts[0x26] / actual_duration
        packet_freq = total_packets / actual_duration

        print(f"串口总吞吐率 (Packet Rate): {packet_freq:.1f} Hz (每秒收到的包数)")
        print(f"★ 真实采样率 (Sample Rate): {real_freq:.2f} Hz (每秒的数据帧数)")
        print("-" * 50)

        # 3. 结论判断
        if 95 <= real_freq <= 105:
            print("✅ 结论: 传感器完美工作在 100Hz")
        elif 24 <= real_freq <= 26:
            print("⚠️ 结论: 传感器工作在 25Hz (默认值)")
        else:
            print(f"⚠️ 结论: 传感器工作在 {real_freq:.0f}Hz")

        ser.close()

    except serial.SerialException as e:
        print(f"\n[错误] 无法打开串口: {e}")
    except Exception as e:
        print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()