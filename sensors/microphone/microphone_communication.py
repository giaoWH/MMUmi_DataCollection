from __future__ import annotations

import argparse
import csv
import sys
import time
import pyaudio
import wave

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sensors.microphone.audio_utils import list_input_devices, open_input_stream


DEFAULT_CHUNK = 1024
DEFAULT_CHANNELS = 1
DEFAULT_RATE = 48000
FORMAT = pyaudio.paInt16

def main():
    parser = argparse.ArgumentParser(description="测试麦克风录音并记录时间戳")
    parser.add_argument("--device-index", type=int, default=None, help="输入设备索引，默认自动选择第一个可录音设备")
    parser.add_argument("--channels", type=int, default=DEFAULT_CHANNELS, help="录音声道数")
    parser.add_argument("--rate", type=int, default=DEFAULT_RATE, help="首选采样率；若设备不支持会自动回退")
    parser.add_argument("--chunk", type=int, default=DEFAULT_CHUNK, help="每次读取的音频帧数")
    parser.add_argument("--list-devices", action="store_true", help="仅列出可用输入设备")
    args = parser.parse_args()

    timestamp_start = time.strftime("%Y%m%d_%H%M%S")
    wav_filename = f"mic_audio_{timestamp_start}.wav"
    csv_filename = f"mic_timestamps_{timestamp_start}.csv"

    p = pyaudio.PyAudio()

    try:
        devices = list_input_devices(p)
        if args.list_devices:
            if not devices:
                print("未找到可用的音频输入设备")
            else:
                for info in devices:
                    print(
                        f"index={info['index']} name={info.get('name')} "
                        f"channels={int(info.get('maxInputChannels', 0) or 0)} "
                        f"default_rate={int(round(float(info.get('defaultSampleRate', 0) or 0)))}"
                    )
            p.terminate()
            return

        stream, device_info, actual_rate = open_input_stream(
            p,
            audio_format=FORMAT,
            channels=args.channels,
            preferred_rate=args.rate,
            chunk=args.chunk,
            device_index=args.device_index,
        )
    except Exception as e:
        print(f"无法打开麦克风设备: {e}")
        p.terminate()
        return

    frames = []

    print(f"音频数据将保存至: {wav_filename}")
    print(f"时间戳将保存至: {csv_filename}")
    print(
        f"输入设备: index={device_info['index']} name={device_info.get('name')} "
        f"channels={args.channels} rate={actual_rate}Hz chunk={args.chunk}"
    )
    print("按 Ctrl+C 停止采集...")

    try:
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csv_file:
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(["Timestamp", "Chunk_Index"])

            chunk_idx = 0
            while True:
                # 读取音频块
                data = stream.read(args.chunk, exception_on_overflow=False)
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
                current_duration = (chunk_idx * args.chunk) / actual_rate
                sys.stdout.write(f"\r[{timestamp_str}] 已采集 {chunk_idx} 个音频块 (约 {current_duration:.2f} 秒)...   ")
                sys.stdout.flush()
                
                chunk_idx += 1

    except KeyboardInterrupt:
        print("\n\n正在停止麦克风采集...")
    finally:
        sample_width = p.get_sample_size(FORMAT)
        # 停止并关闭音频流
        if 'stream' in locals() and stream.is_active():
            stream.stop_stream()
            stream.close()
        p.terminate()

        # 将所有的音频帧写入 WAV 文件
        print(f"正在生成 {wav_filename} ...")
        with wave.open(wav_filename, 'wb') as wf:
            wf.setnchannels(args.channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(actual_rate)
            wf.writeframes(b''.join(frames))
        
        print("采集与保存全部结束。")

if __name__ == "__main__":
    main()
