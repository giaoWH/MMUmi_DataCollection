import cv2
import time
import datetime
from .base_sensor import BaseSensor

class CameraSensor(BaseSensor):
    def __init__(self, camera_idx=0, width=640, height=480, fps=30):
        super().__init__("Camera")
        self.camera_idx = camera_idx
        self.width = width
        self.height = height
        self.fps = fps
        self.video_filename = ""
        
    def _worker(self):
        # 1. 打开相机
        cap = cv2.VideoCapture(self.camera_idx, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))

        if not cap.isOpened():
            print(f"[Camera] 无法打开设备 {self.camera_idx}")
            return

        # 2. 准备录制文件
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        self.video_filename = f"video_{timestamp}.mp4"
        writer = cv2.VideoWriter(
            self.video_filename,
            cv2.VideoWriter_fourcc(*'mp4v'),
            self.fps,
            (self.width, self.height)
        )
        print(f"[Camera] 录制开始: {self.video_filename}")

        frame_id = 0
        while self.running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue
            
            # 捕获时刻
            capture_time = time.time()
            
            # 写入视频 (耗时操作在子线程完成)
            writer.write(frame)
            frame_id += 1
            
            # 更新状态 (主线程只关心 帧号 FrameID)
            with self.lock:
                self.latest_data = frame_id 
                self.latest_timestamp = capture_time
                self.frame_count = frame_id

        cap.release()
        writer.release()
        print(f"[Camera] 视频已保存")

    def _close_hardware(self):
        pass