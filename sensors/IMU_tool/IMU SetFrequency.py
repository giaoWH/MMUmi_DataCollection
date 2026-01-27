import serial
import time
import sys

# ================= 配置区域 =================
SERIAL_PORT = 'COM4'  # 端口号
BAUD_RATE = 115200  # 波特率


# ===========================================

def calculate_checksum(data_list):
    """计算校验和：累加所有字节，取低8位"""
    return sum(data_list) & 0xFF


def send_frequency_cmd(ser, target_freq):
    """发送频率设置指令"""
    # 指令格式: 头1(7E) 头2(23) 长度(07) 功能(60) 参数1(频率) 参数2(5F) 校验
    # 例如设置100Hz (0x64): 7E 23 07 60 64 5F CB

    if not (10 <= target_freq <= 100):
        print(f"❌ 错误: 频率必须在 10 到 100 Hz 之间。您输入的是 {target_freq}。")
        return False

    cmd = [0x7E, 0x23, 0x07, 0x60, int(target_freq), 0x5F]
    checksum = calculate_checksum(cmd)
    cmd.append(checksum)

    bytes_data = bytearray(cmd)
    ser.write(bytes_data)

    print(f"-> 发送指令 HEX: {bytes_data.hex().upper()}")
    print("-> 等待传感器生效...")
    time.sleep(0.5)  # 给传感器一点时间处理
    return True


def verify_real_frequency(ser):
    """通过统计欧拉角包的数量来验证真实频率"""
    print(f"-> 正在采样验证 (持续 2 秒)... ", end="", flush=True)

    ser.reset_input_buffer()  # 清空旧数据
    start_time = time.time()
    packet_count = 0
    buffer = bytearray()

    # 我们只统计 0x26 (欧拉角包)，因为它代表了一帧完整的解算数据
    TARGET_FUNC_CODE = 0x26

    while time.time() - start_time < 2.0:
        if ser.in_waiting:
            buffer.extend(ser.read(ser.in_waiting))

            while len(buffer) >= 4:
                # 检查包头
                if buffer[0] == 0x7E and buffer[1] == 0x23:
                    pkt_len = buffer[2]
                    if len(buffer) < pkt_len:
                        break  # 数据不够

                    # 检查功能字
                    if buffer[3] == TARGET_FUNC_CODE:
                        packet_count += 1

                    buffer = buffer[pkt_len:]
                else:
                    buffer.pop(0)

    print("完成")
    return packet_count / 2.0


def main():
    print(f"=== IMU 频率设置助手 ===")
    print(f"端口: {SERIAL_PORT}")
    print("-" * 40)

    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    except Exception as e:
        print(f"❌ 无法打开串口 {SERIAL_PORT}: {e}")
        return

    while True:
        print("\n" + "=" * 40)
        user_input = input("请输入目标频率 (10-100)，输入 q 退出: ").strip()

        if user_input.lower() == 'q':
            break

        if not user_input.isdigit():
            print("❌ 请输入有效的整数！")
            continue

        freq = int(user_input)

        # 1. 发送设置指令
        if send_frequency_cmd(ser, freq):

            # 2. 验证实际频率
            real_freq = verify_real_frequency(ser)

            # 3. 显示结果
            print(f"\n📈 目标频率: {freq} Hz")
            print(f"📊 实测频率: {real_freq:.2f} Hz")

            if abs(real_freq - freq) <= 5:  # 允许 5Hz 的误差
                print("✅ 设置成功！")
            else:
                print("⚠️ 设置似乎未生效，请检查：")
                print("   1. 传感器是否支持该频率？")
                print("   2. TX/RX 线是否接好？")
                print("   3. 某些模块需要断电重启才能完全生效。")

    ser.close()
    print("程序已退出。")


if __name__ == "__main__":
    main()