import cv2
import threading
import time
import datetime
import os

# --- 配置 ---
CAMERA_INDEX = 0          # 你的摄像头索引
WIDTH = 640               # 宽
HEIGHT = 480              # 高
FPS = 15                  # 帧率
FILENAME = f"test_video_{datetime.datetime.now().strftime('%H%M%S')}.mp4"

# 全局控制变量
is_running = True       # 程序是否运行
is_recording = False    # 是否正在录制
frame_count = 0         # 记录录了多少帧

def camera_thread_task():
    global is_running, is_recording, frame_count
    
    # 1. 打开摄像头
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    
    # --- 关键设置：防止 WSL 花屏 ---
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FPS)
    # -----------------------------

    if not cap.isOpened():
        print("\n❌ 错误：无法打开摄像头！")
        is_running = False
        return

    # 2. 准备视频写入器
    # mp4v 是比较通用的 mp4 编码
    fourcc = cv2.VideoWriter_fourcc(*'mp4v') 
    out = cv2.VideoWriter(FILENAME, fourcc, FPS, (WIDTH, HEIGHT))

    print(f"\n✅ 摄像头就绪 ({WIDTH}x{HEIGHT} @ {FPS}fps)")
    print("后台采集线程已启动...")

    while is_running:
        ret, frame = cap.read()
        if not ret:
            print("⚠️ 无法获取画面 (丢帧)")
            time.sleep(0.1)
            continue

        # 如果处于录制状态，写入文件
        if is_recording:
            if frame is not None:
                out.write(frame)
                frame_count += 1
                # 每录30帧打印一个小点，证明活着
                if frame_count % 30 == 0:
                    print(".", end="", flush=True)

    # 清理工作
    cap.release()
    out.release()
    print("\n\n资源已释放，线程结束。")

def main():
    global is_running, is_recording

    # 启动后台摄像头线程
    t = threading.Thread(target=camera_thread_task)
    t.start()
    
    # 等待摄像头初始化
    time.sleep(2) 
    if not is_running:
        return

    # --- 第一阶段：等待开始 ---
    input(f"\n👉 按 [Enter] 开始录制视频到 '{FILENAME}' ...")
    print("🎥 正在录制 (每打印一个点代表约2秒)... ", end="", flush=True)
    is_recording = True

    # --- 第二阶段：等待结束 ---
    input("\n\n👉 按 [Enter] 结束录制并保存 ...")
    is_recording = False
    is_running = False # 通知线程退出
    
    # 等待线程真正结束
    t.join()
    
    # 检查文件
    if os.path.exists(FILENAME):
        size_mb = os.path.getsize(FILENAME) / (1024 * 1024)
        print(f"✅ 视频已保存: {FILENAME} ({size_mb:.2f} MB)")
        print(f"   共录制: {frame_count} 帧")
    else:
        print("❌ 视频保存失败")

if __name__ == "__main__":
    main()