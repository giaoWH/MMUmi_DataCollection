import serial
import time
import struct
import csv
import sys

# ================= 用户配置 =================
SERIAL_PORT = '/dev/ttyACM1'
BAUD_RATE = 115200
CMD_START_100HZ = bytes.fromhex("09 10 01 9A 00 01 02 02 00 CD CA")
CMD_STOP = b'\xFF' * 55
FRAME_HEADER = b'\x20\x4E'
FRAME_LEN = 16

# 滤波参数配置：一阶低通滤波，FILTER_ALPHA越小/滤波越强/延迟越大
FILTER_ALPHA = 0.5
CALIBRATION_DURATION = 3.0


def main():
    # 1. 准备 CSV 文件
    # 生成文件名：sensor_data_年月日_时分秒.csv
    timestamp_start = time.strftime("%Y%m%d_%H%M%S")
    csv_filename = f"sensor_data_{timestamp_start}.csv"

    # 打开文件准备写入
    # newline='' 是为了防止在 Windows 下产生多余的空行
    csv_file = open(csv_filename, 'w', newline='', encoding='utf-8')
    csv_writer = csv.writer(csv_file)

    # 写入表头
    header = ["Timestamp", "Fx", "Fy", "Fz", "Tx", "Ty", "Tz"]
    csv_writer.writerow(header)
    print(f"数据将保存至: {csv_filename}")
    print("按 Ctrl+C 停止采集并关闭文件...")

    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    except Exception as e:
        print(f"无法打开串口: {e}")
        csv_file.close()
        return

    ser.write(CMD_START_100HZ)
    time.sleep(0.1)
    ser.reset_input_buffer()

    buffer = b''
    DT = 0.01
    virtual_time = time.time()
    last_filtered_values = None
    calibration_start_time = time.time()
    calibration_sum = [0.0] * 6
    calibration_count = 0
    baseline_values = None
    calibration_finished = False

    try:
        while True:
            if ser.in_waiting:
                buffer += ser.read(ser.in_waiting)

            while len(buffer) >= FRAME_LEN:
                header_index = buffer.find(FRAME_HEADER)
                if header_index == -1:
                    buffer = buffer[-1:]
                    break
                if header_index > 0:
                    buffer = buffer[header_index:]
                if len(buffer) < FRAME_LEN:
                    break

                # 提取数据
                payload = buffer[2:14]
                buffer = buffer[FRAME_LEN:]

                try:
                    raw_values = struct.unpack('<6h', payload)

                    # 原始物理量计算
                    current_raw_list = [
                        raw_values[0] / 100.0,  # Fx
                        raw_values[1] / 100.0,  # Fy
                        raw_values[2] / 100.0,  # Fz
                        raw_values[3] / 1000.0,  # Tx
                        raw_values[4] / 1000.0,  # Ty
                        raw_values[5] / 1000.0  # Tz
                    ]

                    current_real_time = time.time()

                    # === 初始静态校准 ===
                    if not calibration_finished:
                        calibration_count += 1
                        for i, value in enumerate(current_raw_list):
                            calibration_sum[i] += value

                        elapsed = current_real_time - calibration_start_time
                        if elapsed < CALIBRATION_DURATION:
                            progress = min(elapsed / CALIBRATION_DURATION * 100.0, 100.0)
                            line = (
                                f"\r正在校准零点... {elapsed:4.2f}/{CALIBRATION_DURATION:.2f}s "
                                f"({progress:5.1f}%)"
                            )
                            sys.stdout.write(line)
                            sys.stdout.flush()
                            continue

                        baseline_values = [
                            value_sum / calibration_count for value_sum in calibration_sum
                        ]
                        calibration_finished = True
                        last_filtered_values = None

                        print("\n零点校准完成，基线为:")
                        print(
                            "  "
                            f"F(N): {baseline_values[0]:7.3f} {baseline_values[1]:7.3f} {baseline_values[2]:7.3f}"
                        )
                        print(
                            "  "
                            f"T(Nm): {baseline_values[3]:7.4f} {baseline_values[4]:7.4f} {baseline_values[5]:7.4f}"
                        )
                        print("开始记录去基线后的交互力数据...")

                    compensated_raw_list = [
                        current_raw_list[i] - baseline_values[i] for i in range(6)
                    ]

                    # === 滤波 ===
                    if last_filtered_values is None:
                        last_filtered_values = compensated_raw_list
                        filtered_values = compensated_raw_list
                    else:
                        filtered_values = []
                        for i in range(6):
                            val = (FILTER_ALPHA * compensated_raw_list[i]) + \
                                  ((1 - FILTER_ALPHA) * last_filtered_values[i])
                            filtered_values.append(val)
                        last_filtered_values = filtered_values

                    # === 时间戳 ===
                    virtual_time += DT
                    if abs(current_real_time - virtual_time) > 0.1:
                        virtual_time = current_real_time

                    local_t = time.localtime(virtual_time)
                    ms = int((virtual_time % 1) * 1000)
                    timestamp_str = time.strftime("%H:%M:%S", local_t) + f".{ms:03d}"

                    # === 存入 CSV ===
                    # 组合一行数据：[时间, Fx, Fy, Fz, Tx, Ty, Tz]
                    # 使用 list comprehension 保留2位或3位小数精度，或者直接存浮点数
                    # 这里直接存浮点数，方便后续 Excel 处理
                    row_data = [timestamp_str] + [f"{x:.3f}" for x in filtered_values]

                    csv_writer.writerow(row_data)

                    # === 新增：实时屏幕输出 ===
                    # 格式化力 (Force) 和 力矩 (Torque) 字符串
                    f_str = f"F(N): {filtered_values[0]:7.2f} {filtered_values[1]:7.2f} {filtered_values[2]:7.2f}"
                    t_str = f"T(Nm): {filtered_values[3]:7.3f} {filtered_values[4]:7.3f} {filtered_values[5]:7.3f}"

                    # 拼接整行，\r 使光标回到行首，实现原地刷新
                    # 后面增加一些空格以防残留旧数据的字符
                    line = f"\r[{timestamp_str}] {f_str} | {t_str}    "
                    sys.stdout.write(line)
                    sys.stdout.flush()

                except struct.error:
                    pass

    except KeyboardInterrupt:
        print("\n正在停止...")
        ser.write(CMD_STOP)
        ser.close()
        csv_file.close()  # 这一步非常重要，确保数据完全写入
        print(f"采集结束，文件已保存为: {csv_filename}")


if __name__ == "__main__":
    main()
