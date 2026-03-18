import argparse
import time
from pathlib import Path

import cv2


def main():
    parser = argparse.ArgumentParser(description="测试 RGB 相机是否可用")
    parser.add_argument("--device-index", type=int, default=0, help="相机设备编号")
    parser.add_argument("--width", type=int, default=640, help="图像宽度")
    parser.add_argument("--height", type=int, default=480, help="图像高度")
    parser.add_argument("--fps", type=int, default=30, help="目标帧率")
    parser.add_argument(
        "--save-path",
        default="camera_test_frame.jpg",
        help="退出时保存一张测试图片",
    )
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.device_index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)

    if not cap.isOpened():
        print(f"无法打开相机设备 {args.device_index}")
        return

    print(
        f"相机已打开: index={args.device_index}, "
        f"target={args.width}x{args.height}@{args.fps}fps"
    )
    print("按 Ctrl+C 停止测试并保存最后一帧图像")

    last_frame = None
    frame_count = 0
    start_time = time.time()

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("读取相机帧失败")
                time.sleep(0.05)
                continue

            last_frame = frame
            frame_count += 1
            now = time.time()
            elapsed = max(now - start_time, 1e-6)
            current_fps = frame_count / elapsed
            height, width = frame.shape[:2]
            timestamp_str = time.strftime("%H:%M:%S", time.localtime(now))
            ms = int((now % 1) * 1000)

            print(
                f"\r[{timestamp_str}.{ms:03d}] "
                f"已采集 {frame_count} 帧, 实际分辨率 {width}x{height}, "
                f"平均FPS {current_fps:.2f}",
                end="",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\n正在停止相机测试...")
    finally:
        cap.release()

        if last_frame is not None:
            save_path = Path(args.save_path)
            cv2.imwrite(str(save_path), last_frame)
            print(f"已保存测试图像到: {save_path}")
        else:
            print("未获取到有效图像帧")


if __name__ == "__main__":
    main()
