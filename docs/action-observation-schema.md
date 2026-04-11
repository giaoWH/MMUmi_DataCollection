# UMI Data Collection Action / Observation Schema

## 1. 项目理解与本文范围

这个项目不是“直接录机器人 action”的数据集脚本，而是一套统一多模态采集 SDK。当前主链路是：

```text
record -> aligned session -> process_trajectory -> export(LeRobot / RLDS / HDF5 ...)
```

其中：

- `scripts/sdk_record.py` 负责录制多模态原始流，并按统一时间轴生成 `aligned/frames.jsonl`
- `scripts/sdk_process_trajectory.py` 负责离线运行 ORB-SLAM3，把手持演示中的 RealSense 轨迹写入 `trajectory/frames.jsonl`
- `sdk/exporters/lerobot_exporter.py` 负责把“当前 observation + 下一时刻 EE / gripper 变化”转换成训练使用的 action

因此，当前项目里的 `action schema` 是一个“离线构造出的机器人动作表示”，而不是录制阶段就直接由机械臂控制器输出的 command log。

本文默认以当前仓库实现为准，并同时说明：

- session 原始存储 schema
- LeRobot 导出后的训练 schema
- 当前 `configs/record.yaml` 中的默认频率与启停状态

## 2. 当前默认采集配置摘要

以 `configs/record.yaml` 为准，当前默认配置是：

- 对齐频率：`align_rate_hz = 30`
- RGB：`camera` 启用，`640x480 @ 30Hz`
- Depth：`realsense.depth` 启用，`640x480 @ 30Hz`
- RealSense RGB：`realsense.color` 当前默认关闭
- FT：启用，`torque` 也启用
- Motors：启用，但当前仅 `motor_2` 开启，`motor_1` 关闭
- GelSight：启用，`320x240 @ 25Hz`
- Microphone：启用，`48kHz`, `chunk=1024`
- 独立 IMU：启用，但它主要服务于 FT 重力补偿，不属于本文要求的 observation 主字段

## 3. Action Schema

### 3.1 action 维度

当前 LeRobot 导出的 action 是固定 8 维向量：

```text
action = [
  ee_delta_x,
  ee_delta_y,
  ee_delta_z,
  ee_delta_qw,
  ee_delta_qx,
  ee_delta_qy,
  ee_delta_qz,
  gripper_delta
]
```

对应可读字段同时保留为：

- `action.ee_delta.position.[0|1|2]`
- `action.ee_delta.quaternion.[0|1|2|3]`
- `action.gripper.position_delta`

### 3.2 每个维度的物理意义

| 维度 | 字段 | 物理意义 |
| --- | --- | --- |
| 0 | `ee_delta_x` | 相邻两个有效轨迹时刻之间的末端执行器 x 平移增量 |
| 1 | `ee_delta_y` | 相邻两个有效轨迹时刻之间的末端执行器 y 平移增量 |
| 2 | `ee_delta_z` | 相邻两个有效轨迹时刻之间的末端执行器 z 平移增量 |
| 3 | `ee_delta_qw` | 当前姿态到下一姿态的相对四元数实部 |
| 4 | `ee_delta_qx` | 当前姿态到下一姿态的相对四元数 x 分量 |
| 5 | `ee_delta_qy` | 当前姿态到下一姿态的相对四元数 y 分量 |
| 6 | `ee_delta_qz` | 当前姿态到下一姿态的相对四元数 z 分量 |
| 7 | `gripper_delta` | 相邻两个有效时刻之间 gripper 开合位置增量 |

补充说明：

- 平移单位沿用 ORB-SLAM3 轨迹的米制位姿，等价于 `position=[x,y,z]` 的差分
- 姿态不是欧拉角差，而是单位化后的相对四元数 `q_next * inverse(q_current)`
- 当前实现没有在 SDK 内再做“机器人基坐标系”重映射，姿态和平移仍处于 ORB-SLAM3 轨迹语义下的坐标系
- ORB-SLAM3 wrapper 当前写入的 metadata 中，坐标系约定是 `coordinate_frame=orbslam3_world`，`pose_reference=imu_body`

### 3.3 hand-held demonstration 到 robot action 的映射

当前映射链路如下：

1. 人手持 RealSense 做 demonstration，SDK 同步录制多模态数据。
2. 录制结束后，在 PC 端对该 session 运行 `scripts/sdk_process_trajectory.py`。
3. ORB-SLAM3 根据 RealSense bundle 生成轨迹，输出每个轨迹点的 `position + quaternion`。
4. 导出 LeRobot 时，以相邻两个 `aligned` step 为单位，分别查找这两个 step 对应的轨迹帧。
5. 轨迹匹配依赖 `aligned.frames.realsense.device_time_ns` 与 `trajectory.time.host_time_ns/device_time` 的时间对应关系。
6. 机器人末端 action 定义为：
   - `Δp = p(t+1) - p(t)`
   - `Δq = q(t+1) * inverse(q(t))`
7. gripper action 定义为：
   - `Δg = motor_2.position(t+1) - motor_2.position(t)`

这意味着当前仓库里的“robot action”本质上是：

- 手持演示设备在相邻两个有效时刻之间的位姿变化
- 加上 gripper 通道的相邻位置变化

需要明确的边界：

- 当前 SDK 还没有实现“手持相机坐标系 -> 真实机器人控制坐标系”的额外外参或运动学映射
- 因此这里的 `robot action` 更准确地说，是“用于模仿学习的数据集 action 表示”
- 若后续接真实机器人控制器，通常还需要在下游增加坐标系标定、比例缩放、工作空间裁剪和 gripper 方向约定

### 3.4 gripper command 的定义方式

当前 gripper command 的来源不是独立 binary open/close label，而是连续位置增量：

- 当前值：`observation.motors.motor_2.position`
- action 定义：`next_motor_2.position - current_motor_2.position`
- 导出字段：`action.gripper.position_delta`

当前实现约定：

- gripper 通道默认绑定到 `motor_2`
- `motor_1` 即使存在，也不会自动参与 action 构造
- `gripper_delta > 0` 仅表示“下一时刻位置数值更大”
- “更大”究竟代表张开还是闭合，取决于电机硬件的零位、安装方向和控制约定，SDK 当前不强制解释其语义

如果未来要做跨机器人复用，建议再额外定义：

- gripper 绝对开合归一化区间
- 正方向是 open 还是 close
- 是否裁成 `[-1, 1]` 或 `[0, 1]`

### 3.5 action 输出格式

当前 action 主要以 LeRobot 导出为训练格式，输出位置如下：

- 主表：`exports/lerobot/data/chunk-000/file-000.parquet`
- 主字段：`action`
- 可读冗余字段：
  - `action.ee_delta.position.*`
  - `action.ee_delta.quaternion.*`
  - `action.gripper.position_delta`

其中：

- `action` 是长度固定为 8 的 dense vector
- `dtype` 为 `float32`
- feature 名顺序固定为：
  - `ee_delta_x`
  - `ee_delta_y`
  - `ee_delta_z`
  - `ee_delta_qw`
  - `ee_delta_qx`
  - `ee_delta_qy`
  - `ee_delta_qz`
  - `gripper_delta`

action 样本只有在以下条件同时满足时才会导出：

- 当前 step 与下一 step 都能匹配到 trajectory
- 当前与下一 trajectory 的 `tracking_state == OK`
- 当前与下一 step 都能读到 `motor_2.position`

否则该 action row 会被跳过，而不是填默认值。

## 4. Observation Schema

### 4.1 RGB

当前项目中的 RGB observation 有两类来源：

1. 普通 RGB 相机：`camera.color`
2. RealSense 彩色流：`realsense.color`

当前默认配置下：

- `camera.color` 开启
- `realsense.color` 关闭

session 原始字段：

- `streams/camera/<task_slug>_rgb_001.jsonl -> payload.color`
- `streams/realsense/<task_slug>_rgbd_001.jsonl -> payload.color`

LeRobot 导出字段：

- `observation.images.camera`
- `observation.images.realsense_color`

物理意义：

- 普通 RGB 相机通常是场景或操作视角
- RealSense RGB 是与 depth/IMU 同设备的彩色图像

### 4.2 depth

当前 depth observation 由 `realsense.depth` 提供。

session 原始字段：

- `streams/realsense/<task_slug>_rgbd_001.jsonl -> payload.depth`
- 可选派生字段：`payload.aligned_depth_to_color`

LeRobot 导出字段：

- `observation.images.realsense_depth`
- `observation.images.realsense_aligned_depth_to_color`

物理意义：

- `depth` 是 RealSense 深度图，反映每个像素到传感器的距离
- `aligned_depth_to_color` 是可选派生结果，用于与彩色图对齐

### 4.3 force/torque

当前 force/torque observation 来自 FT 传感器。

session 原始字段：

- `payload.force = [Fx, Fy, Fz]`
- `payload.torque = [Tx, Ty, Tz]`，仅当 `ft.enable_torque=true` 时存在

LeRobot 导出字段：

- `observation.ft.force.[0|1|2]`
- `observation.ft.torque.[0|1|2]`

物理意义：

- `force` 单位是 `N`
- `torque` 单位是 `Nm`
- 均为传感器自身坐标系下的六维接触/作用力信号

补充：

- 若启用了重力补偿，aligned record metadata 中还会有：
  - `observation.gravity_compensation.pure_force.*`
  - `observation.gravity_compensation.gravity_force.*`
- 这部分是附加低维观测，不替代原始 FT 读数

### 4.4 tactile

当前 tactile observation 对应 GelSight Mini。

session 原始字段：

- `streams/gelsight/<task_slug>_visuotactile_001.jsonl -> payload.image`

LeRobot 导出字段：

- `observation.images.gelsight`

物理意义：

- 这是视触觉图像，不是传统 taxel 矩阵
- 它本质上是接触表面形变的 RGB 观测
- 在当前 SDK 中被单独标注为 `visuotactile` 模态，用来区别于普通 RGB

### 4.5 motor states

当前 motor state observation 来自 `motors` 传感器。

session 原始字段：

- `payload.motor_1.position / velocity / torque`
- `payload.motor_2.position / velocity / torque`

当前默认配置下：

- `motor_1` 关闭
- `motor_2` 开启

LeRobot 导出字段：

- `observation.motors.motor_1.position`
- `observation.motors.motor_1.velocity`
- `observation.motors.motor_1.torque`
- `observation.motors.motor_2.position`
- `observation.motors.motor_2.velocity`
- `observation.motors.motor_2.torque`

物理意义：

- `position`：电机当前位置
- `velocity`：电机速度
- `torque`：电机扭矩或扭矩代理量

当前 action 构造只使用 `motor_2.position`。

### 4.6 relative EE state

当前仓库里与 EE state 最直接对应的是离线轨迹：

- session 轨迹文件：`trajectory/frames.jsonl`
- LeRobot 导出字段：
  - `observation.trajectory.position.[0|1|2]`
  - `observation.trajectory.quaternion.[0|1|2|3]`
  - `observation.trajectory.tracking_state`

物理意义：

- 它描述的是当前 step 对应的手持设备/末端参考体在轨迹坐标系中的 pose
- 现阶段 metadata 中的参考体语义是 `imu_body`
- 现阶段坐标系语义是 `orbslam3_world`

需要单独说明：

- 当前仓库没有单独命名为 `observation.relative_ee_state` 的字段
- 当前 observation 里保存的是“当前 EE pose”
- 相对 EE 变化量被放在 `action.ee_delta.*` 中
- 如果下游确实需要“relative EE state”，通常做法是把当前 `observation.trajectory.*` 再相对首帧、目标帧或机器人基座做一次归一化

### 4.7 optional audio

当前 optional audio 来自 `microphone`。

session 原始字段：

- `streams/microphone/<task_slug>_audio_001.jsonl -> payload.audio`

LeRobot 导出字段：

- `observation.audios.microphone`

物理意义：

- 每个音频 observation 是一个 chunk，而不是完整 session
- 原始数据类型为 `int16`
- 单声道时 shape 为 `[chunk]`
- 多声道时 shape 为 `[chunk, channels]`

当前默认配置下：

- `channels = 1`
- `rate = 48000`
- `chunk = 1024`

## 5. 频率

当前项目同时存在“原生采样频率”和“统一 step 频率”两层时间尺度。

### 5.1 统一 step 频率

- `aligned` step 频率由 `align_rate_hz` 控制
- 当前默认值：`30Hz`
- `aligned/frames.jsonl` 与 LeRobot row 都以这个 step 频率为主时间网格

### 5.2 各模态原生频率

按当前 `configs/record.yaml` 默认值：

| 模态 | 默认频率 | 备注 |
| --- | --- | --- |
| RGB camera | `30Hz` | `camera.fps` |
| GelSight | `25Hz` | `gelsight.fps` |
| RealSense depth | `30Hz` | `realsense.depth.fps` |
| RealSense color | `30Hz` | 但当前默认关闭 |
| RealSense IR | `30Hz` | `ir1/ir2` |
| Microphone | `48000Hz` | 按 `chunk=1024` 切片写盘 |
| FT | 硬件串口原生频率 | 当前配置文件未显式设定 Hz |
| Motors | 硬件串口原生频率 | 当前配置文件未显式设定 Hz |

理解方式：

- 原始流按各自原生频率采集
- `aligned` 层再把这些异步原始流投影到统一的 `30Hz` 时间网格
- 训练时用到的 observation/action step，来自这个对齐后的统一时间网格

## 6. 时间对齐方式

### 6.1 传感器时间模型

每个 `SensorFrame` 都尽量保留：

- `host_time_ns`
- `monotonic_time_ns`
- `device_time_ns`

### 6.2 对齐策略

当前 `BufferedFrameAligner` 的默认策略是 `nearest`：

- 在每个对齐时刻，从每个传感器 buffer 中选取“离该时刻最近”的帧
- 优先使用 `monotonic_time_ns` 做对齐
- 若缺失，则回退到 `host_time_ns`

对齐时间网格由录制开始时刻起，按固定间隔推进：

```text
aligned_time[k] = start_time + k / align_rate_hz
```

### 6.3 最大帧龄限制

- `max_frame_age` 当前默认是 `0.2s`
- 若某模态距离当前对齐时刻的绝对帧龄超过这个阈值，该模态会被判为缺失

### 6.4 trajectory 与 aligned 的对齐

action 构造不是简单按索引对齐 trajectory，而是：

- 先从 `aligned` 记录里读取 `realsense.device_time_ns`
- 再去 `trajectory` 中查找相同时间戳的轨迹帧

因此 trajectory 是“借助 RealSense 时间戳”绑定回 aligned step 的。

## 7. 缺失处理

当前系统对缺失值分三层处理。

### 7.1 aligned 层

`aligned/frames.jsonl` 中会明确记录：

- `missing_sensors`
- `metadata.dropped_sensors`
- `age_by_sensor`

含义如下：

- `missing_sensors`：该 step 没有可用帧
- `dropped_sensors`：本来找到了最近帧，但因帧龄超过 `max_frame_age` 被丢弃

### 7.2 observation.state 层

LeRobot 导出时：

- `observation.state` 会把当前 step 能数值化的低维观测拼成定长向量
- 如果某字段在该 row 缺失，则填 `0.0`
- 但原始缺失信息不会被抹掉，仍可从原始扁平列是否存在以及 `missing_sensors` 中判断

### 7.3 action 层

action 缺失不做零填充，而是直接跳过该 row。常见跳过原因包括：

- trajectory 缺失
- `tracking_state != OK`
- `motor_2.position` 缺失

这种设计更适合训练，因为它避免把“无效动作”混入监督信号。

## 8. 存储格式

### 8.1 session 原始存储

每个 session 的核心结构是：

```text
session_xxx/
  manifest.json
  meta.json
  streams/
    <sensor_name>/
      <task_slug>_<modality>_001.jsonl
      artifacts/
      <task_slug>_<modality>_001.mp4
  aligned/
    frames.jsonl
  trajectory/
    frames.jsonl
  exports/
```

### 8.2 原始 observation 的落盘方式

当前落盘策略如下：

| 模态 | session 落盘方式 |
| --- | --- |
| `camera.color` | `streams/camera/<task_slug>_rgb_001.mp4`，对应 `*.jsonl` 中保存 `mp4_frame` 索引 |
| `gelsight.image` | `streams/gelsight/<task_slug>_visuotactile_001.mp4`，对应 `*.jsonl` 中保存 `mp4_frame` 索引 |
| `realsense.color` | `streams/realsense/<task_slug>_rgbd_001.mp4`，对应 `*.jsonl` 中保存 `mp4_frame` 索引 |
| `realsense.depth` | `npy` artifact 引用 |
| `realsense.aligned_depth_to_color` | 通常为 `npy` artifact 引用 |
| `realsense.ir1/ir2` | 一般为 `png` 或 `npy` artifact 引用，取决于 dtype |
| `microphone.audio` | 写入 `streams/camera/<task_slug>_rgb_001.mp4` 的主音轨，对应 `*.jsonl` 中保存 `mp4_audio` 切片索引 |
| FT / Motors / trajectory | 直接写入 JSON 数值字段 |

注意：

- 当前若启用 `microphone`，必须同时启用 `camera`，因为音频被复用到 `camera` 的主视频容器中
- `*.jsonl` 永远是索引层，真正的图像/音频数据可能在 MP4 或 artifact 文件里

### 8.3 LeRobot 导出格式

训练友好的导出结构为：

```text
exports/lerobot/
  data/chunk-000/file-000.parquet
  images/
  audio/
  meta/info.json
  meta/stats.json
```

其中：

- 低维 observation、action、annotation 都在 Parquet 里
- 图像 observation 会额外导出到 `images/`
- 音频 chunk 会额外导出到 `audio/`
- `meta/info.json` 记录每个 feature 的 dtype、shape 和名称顺序

## 9. 推荐对外表述

如果需要把这个项目的数据 schema 对外介绍为一句话，可以写成：

> 当前数据集以 `30Hz` 对齐 step 为核心，每个 step 包含多模态 observation；action 由离线 ORB-SLAM3 轨迹给出的相邻 EE pose 增量和 `motor_2` 的 gripper 位置增量组成，原始模态在 session 中保留完整索引与媒体引用，训练导出则统一写入 LeRobot Parquet + images/audio 结构。

## 10. 目前实现边界

为了避免文档和实现脱节，下面这些点需要明确保留：

- 当前 action 的 EE 语义来自 ORB-SLAM3 轨迹，不是机器人控制器真实反馈
- 当前没有单独落盘 `observation.relative_ee_state` 字段，只有当前 pose 和相对 action
- 当前 gripper 语义只保证“位置数值增量”，不保证“open / close”方向解释
- 当前 `motor_2` 被硬编码为 gripper 通道
- 当前 session 是 source of truth，LeRobot 是训练导出视图
