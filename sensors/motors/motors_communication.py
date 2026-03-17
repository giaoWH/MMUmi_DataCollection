import serial
import time
import csv
import sys
import re

# ================= 用户配置 =================
SERIAL_PORT = 'COM5'  # 请根据实际情况修改
BAUD_RATE = 115200
# ============================================

def main():
    timestamp_start = time.strftime("%Y%m%d_%H%M%S")
    csv_filename = f"motor_data_{timestamp_start}.csv"

    # 正则表达式匹配字符串: M1: P:4.385 V:-0.04 T:0.33 | M2: P:4.883 V:-0.01 T:-0.06
    # 提取六个浮点数，支持正负号和小数点
    pattern = re.compile(
        r"M1:\s*P:\s*([-\d.]+)\s*V:\s*([-\d.]+)\s*T:\s*([-\d.]+)\s*\|\s*"
        r"M2:\s*P:\s*([-\d.]+)\s*V:\s*([-\d.]+)\s*T:\s*([-\d.]+)"
    )

    try:
        # 打开串口，使用 readline 时 timeout 可稍微设置大一点防阻塞
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    except Exception as e:
        print(f"无法打开串口 {SERIAL_PORT}: {e}")
        return

    ser.reset_input_buffer()

    try:
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csv_file:
            csv_writer = csv.writer(csv_file)
            header = ["Timestamp", "M1_P", "M1_V", "M1_T", "M2_P", "M2_V", "M2_T"]
            csv_writer.writerow(header)
            
            print(f"电机数据将保存至: {csv_filename}")
            print("按 Ctrl+C 停止采集...")

            while True:
                if ser.in_waiting:
                    # 读取一行并解码，忽略无法解码的字符，去除首尾空白
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue

                    match = pattern.search(line)
                    if match:
                        m1_p, m1_v, m1_t, m2_p, m2_v, m2_t = match.groups()

                        # === 时间戳 ===
                        ts = time.time()
                        local_t = time.localtime(ts)
                        ms = int((ts % 1) * 1000)
                        timestamp_str = time.strftime("%H:%M:%S", local_t) + f".{ms:03d}"

                        # === 存入 CSV ===
                        csv_writer.writerow([timestamp_str, m1_p, m1_v, m1_t, m2_p, m2_v, m2_t])

                        # === 实时屏幕输出 ===
                        display_line = (
                            f"\r[{timestamp_str}] "
                            f"M1(P:{m1_p:>6} V:{m1_v:>6} T:{m1_t:>6}) | "
                            f"M2(P:{m2_p:>6} V:{m2_v:>6} T:{m2_t:>6})    "
                        )
                        sys.stdout.write(display_line)
                        sys.stdout.flush()

    except KeyboardInterrupt:
        print("\n正在停止...")
        ser.close()
        print(f"采集结束，文件已保存为: {csv_filename}")

if __name__ == "__main__":
    main()