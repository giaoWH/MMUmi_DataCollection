import pyaudio
import wave
import time
import sys
import csv

# ================= 用户配置 =================
CHUNK = 1024                # 每次读取的音频帧数
FORMAT = pyaudio.paInt16    # 16位深度
CHANNELS = 1                # 单声道
RATE = 44100                # 采样率 44.1kHz
# ============================================

def main():
    timestamp_start = time.strftime("%Y%m%d_%H%M%S")
    wav_filename = f"mic_audio_{timestamp_start}.wav"
    csv_filename = f"mic_timestamps_{timestamp_start}.csv"

    p = pyaudio.PyAudio()

    try:
        # 打开音频流
        stream = p.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)
    except Exception as e:
        print(f"无法打开麦克风设备: {e}")
        p.terminate()
        return

    frames = []

    print(f"音频数据将保存至: {wav_filename}")
    print(f"时间戳将保存至: {csv_filename}")
    print("按 Ctrl+C 停止采集...")

    try:
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csv_file:
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(["Timestamp", "Chunk_Index"])

            chunk_idx = 0
            while True:
                # 读取音频块
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)

                # === 时间戳 ===
                # 这里记录的是读取到这块数据的主机时间
                ts = time.time()
                local_t = time.localtime(ts)
                ms = int((ts % 1) * 1000)
                timestamp_str = time.strftime("%H:%M:%S", local_t) + f".{ms:03d}"

                # === 存入 CSV ===
                csv_writer.writerow([timestamp_str, chunk_idx])

                # === 实时屏幕输出 ===
                # 根据当前采集块数计算大体时长
                current_duration = (chunk_idx * CHUNK) / RATE
                sys.stdout.write(f"\r[{timestamp_str}] 已采集 {chunk_idx} 个音频块 (约 {current_duration:.2f} 秒)...   ")
                sys.stdout.flush()
                
                chunk_idx += 1

    except KeyboardInterrupt:
        print("\n\n正在停止麦克风采集...")
    finally:
        # 停止并关闭音频流
        if 'stream' in locals() and stream.is_active():
            stream.stop_stream()
            stream.close()
        p.terminate()

        # 将所有的音频帧写入 WAV 文件
        print(f"正在生成 {wav_filename} ...")
        with wave.open(wav_filename, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
        
        print("采集与保存全部结束。")

if __name__ == "__main__":
    main()