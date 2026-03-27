# ORB-SLAM3 软件接入约定

## 1. 目的

本文档定义新 SDK 与 ORB-SLAM3 外部程序之间的最小软件契约。

目标是让后续接真实 ORB-SLAM3 程序时，只需要替换：

- 实际命令模板
- 词典文件路径
- 相机配置文件路径

而不需要再修改 SDK 的轨迹解析逻辑。

---

## 2. 当前支持的输入模式

当前 SDK 软件侧已支持：

- `rgbd_inertial`
- `stereo`
- `stereo_inertial`

当前 ORB-SLAM3 软件链默认以 RealSense 作为输入来源，因此当前推荐优先使用：

- `rgbd_inertial`
- `stereo`
- `stereo_inertial`

---

## 3. bundle 输出目录约定

`scripts/sdk_process_trajectory.py` 会先把 session 导出为 ORB-SLAM3 可消费的 bundle。

无论 session 内部采用逐帧 artifact，还是实验版的媒体化存储格式，bundle 的对外目录契约都保持不变。也就是说：

- ORB-SLAM3 仍然只看到标准的 `rgb/`、`depth/`、`imu.csv`
- session 内部若使用 MP4，解码工作由 SDK 的 `SessionReader` 与 bundle exporter 完成
- ORB-SLAM3 wrapper 不需要理解 `mp4_frame` 或 `mp4_audio`

`rgbd_inertial` 典型目录结构如下：

```text
orbslam3_rgbd_inertial/
  bundle_manifest.json
  rgb/
    000000.png
    000001.png
  depth/
    000000.png
    000001.png
  rgbd_associations.txt
  rgb_timestamps.txt
  depth_timestamps.txt
  imu.csv
```

其中：

- `rgb/`：彩色图像
- `depth/`：深度图
- `rgbd_associations.txt`：RGB-D 对齐表
- `imu.csv`：IMU 时间序列
- `bundle_manifest.json`：bundle 摘要

在媒体化存储实验格式下还需注意：

- `rgb/` 内的彩色图像可能来自 `streams/realsense/color.mp4`
- 这些 MP4 当前默认采用 `H.264/yuv420p` 编码，以优先保证设备端可播放性
- `depth/` 内的深度图仍来自无损数组 artifact
- 因此 `rgbd_inertial` 仍坚持“彩色可压缩、深度不压缩”的策略

`stereo` / `stereo_inertial` 典型目录结构如下：

```text
orbslam3_stereo_inertial/
  bundle_manifest.json
  left/
    000000.png
    000001.png
  right/
    000000.png
    000001.png
  stereo_associations.txt
  left_timestamps.txt
  right_timestamps.txt
  imu.csv
```

其中：

- `left/`：左目图像，当前来自 `ir1`
- `right/`：右目图像，当前来自 `ir2`
- `stereo_associations.txt`：左右目对齐表
- `imu.csv`：仅在 inertial 模式下存在

---

## 4. 命令模板变量

命令模板当前支持以下占位符：

- `{session_dir}`
- `{output_jsonl}`
- `{bundle_dir}`
- `{bundle_manifest}`
- `{rgb_dir}`
- `{depth_dir}`
- `{left_dir}`
- `{right_dir}`
- `{association_file}`
- `{stereo_association_file}`
- `{rgb_timestamps_file}`
- `{depth_timestamps_file}`
- `{left_timestamps_file}`
- `{right_timestamps_file}`
- `{imu_file}`

推荐做法是让真实 ORB-SLAM3 wrapper 脚本只依赖这些变量，而不是自己再反查 session 内部结构。

---

## 5. 轨迹输出 JSONL 约定

ORB-SLAM3 外部程序或 wrapper 脚本最终必须输出 JSONL。

每一行表示一帧轨迹，最小合法格式如下：

```json
{
  "timestamp": 12.5,
  "position": [1.0, 2.0, 3.0],
  "quaternion": [1.0, 0.0, 0.0, 0.0]
}
```

可选字段：

```json
{
  "frame_id": 123,
  "device_time": 12.48,
  "aligned_time": 12.50,
  "tracking_state": "OK",
  "metadata": {
    "key": "value"
  }
}
```

字段要求：

- `timestamp`
  - 必填
  - 浮点数
- `position`
  - 必填
  - 长度必须为 3
  - 顺序为 `[x, y, z]`
- `quaternion`
  - 必填
  - 长度必须为 4
  - 顺序为 `[qw, qx, qy, qz]`

当前 `sdk/perception/orbslam3/command_runner.py` 已对上述字段做基本校验。

当前 `imu.csv` 的表头为：

```text
timestamp,sample_type,x,y,z
```

其中：

- `sample_type` 取值为 `accel` 或 `gyro`
- IMU 数据当前来自 RealSense payload 中的 `imu_samples`

---

## 6. 推荐接入方式

推荐不要直接让 SDK 调用原始 ORB-SLAM3 二进制，而是增加一个轻量 wrapper 脚本：

1. 读取 SDK 导出的 bundle
2. 调用真实 ORB-SLAM3
3. 把 ORB-SLAM3 的原始轨迹格式转换为本文档约定的 JSONL
4. 输出到 `{output_jsonl}`

这样可以把“算法原始输出格式差异”封装在 wrapper 里，而不是污染 SDK。

### 6.1 本地 `ThirdParty/ORB_SLAM3` 已编译时的推荐方式

当前仓库已经提供 `scripts/orbslam3_wrapper.py`，第一版仅实现：

- `stereo_inertial`

推荐配置方式：

- `trajectory.mode = stereo_inertial`
- `trajectory.command = null`，由 `scripts/sdk_process_trajectory.py` 自动使用仓库内置 wrapper
- 在 PC 端 `conda activate umi_sdk` 后运行 `python scripts/sdk_process_trajectory.py <session_dir> --config configs/record.yaml`

wrapper 运行时依赖以下环境变量：

- `ORB_SLAM3_VOCAB`
- `ORB_SLAM3_SETTINGS`

其中：

- `ORB_SLAM3_VOCAB` 推荐指向 `ThirdParty/ORB_SLAM3/Vocabulary/ORBvoc.txt`
- `ORB_SLAM3_SETTINGS` 第一版推荐直接复用 `ThirdParty/ORB_SLAM3/Examples/Stereo-Inertial/RealSense_D435i.yaml`

wrapper 会自动完成以下工作：

1. 从 `bundle_manifest.json` 推导 `stereo_associations.txt` 与 `imu.csv`
2. 调用 `ThirdParty/ORB_SLAM3/Examples/Stereo-Inertial/sdk_stereo_inertial_offline`
3. 自动补齐 `LD_LIBRARY_PATH` 中的 ORB-SLAM3 / DBoW2 / g2o 库目录
4. 把 `SaveTrajectoryEuRoC()` 生成的最终轨迹转换成 SDK 约定的 JSONL

第一版的边界如下：

- 只支持 `stereo_inertial`
- 只消费 SDK 当前导出的 `left/`、`right/`、`stereo_associations.txt`、`imu.csv`
- 轨迹来源为 ORB-SLAM3 最终保存结果，不保留逐帧在线 tracking state
- 输出 `tracking_state` 固定为 `OK`
- 同一 session 重跑轨迹时会覆盖旧的 `trajectory/frames.jsonl`

---

## 7. 坐标系约定

在软件侧，SDK 与 ORB-SLAM3 的轨迹交换统一使用以下约定：

- `position = [x, y, z]`
- `quaternion = [qw, qx, qy, qz]`
- `timestamp` 使用秒为单位的浮点时间
- 未显式声明时，轨迹坐标系默认视为 ORB-SLAM3 输出坐标系本身

当前阶段不在 SDK 内部强行做坐标系重映射。若真实 wrapper 需要从 ORB-SLAM3 原始输出转换到项目约定坐标系，应在 wrapper 中完成，并把转换信息写入 `metadata`。

推荐写入的 `metadata` 字段：

```json
{
  "coordinate_frame": "orbslam3_world",
  "camera_frame": "rgbd_color_optical_frame",
  "transform_note": "raw_orbslam3_output"
}
```

## 8. 外参与标定接入点

当前 SDK 软件侧约定如下：

- 词典路径通过环境变量或命令模板传入
- 相机配置文件路径通过环境变量或命令模板传入
- 传感器外参不在 SDK 运行时硬编码
- 若 wrapper 使用了外参文件，应在输出轨迹 `metadata` 中记录其路径或标识

推荐字段：

```json
{
  "settings_path": "/abs/path/to/rgbd_inertial.yaml",
  "extrinsics_id": "rgbd_sensor_factory_or_user_calibrated"
}
```

## 9. 当前未覆盖范围

以下内容仍依赖后续外部联调：

- 真机标定文件格式标准化
- ORB-SLAM3 原始日志解析
- `rgbd_inertial` / `stereo` 模式的仓库内置 wrapper 仍待补齐
- 本地 `ThirdParty/ORB_SLAM3` settings 与真实采集数据的长期联调
