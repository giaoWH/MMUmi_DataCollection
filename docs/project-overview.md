# 项目总览

## 1. 项目是什么

这是一个面向多模态机器人数据采集的统一 SDK。项目当前的重点，不再是做一个一次性的采集脚本，而是维护一套长期可扩展的软件系统，用于：

1. 统一接入多种真实传感器与 fake 数据源
2. 在统一时间轴下记录和对齐多模态数据
3. 将原始流、对齐结果、轨迹结果写入统一 session
4. 为 ORB-SLAM3 提供 RGB-D-Inertial / Stereo / Stereo-Inertial 软件输入
5. 将内部 session 导出为多种数据集格式

当前项目已经完成收口，只保留一套新的 SDK 主系统。

当前推荐使用方式已经切换到“配置文件优先”：

1. 编辑 `configs/record.yaml`
2. 首次安装时按需运行 `scripts/sdk_discover_ports.py`
3. 运行 `scripts/sdk_record.py`
4. 按配置决定是否在录制结束后自动执行轨迹解算

录制配置当前还支持一部分模态内的细粒度裁剪：

- `ft.enable_torque`
  - 关闭后只保存 `force[3]`
- `motors.enable_motor_1`
  - 关闭后不保存 `motor_1` 的 `position / velocity / torque`
- `motors.enable_motor_2`
  - 关闭后不保存 `motor_2` 的 `position / velocity / torque`
- `microphone.device_index`
  - 设为 `null` 时会自动选择第一个可录音输入设备；显式指定索引时会强制绑定该设备

对于具身学习数据整理，当前推荐的跨设备工作流是：

```text
record on Pi -> copy session to PC -> inspect -> annotate -> export -> validate
```

---

## 2. 当前纳入的传感器

当前已经进入 SDK 录制层的模态：

- 六维力传感器 FT
- 独立串口 IMU
- RealSense D435i 多流数据
- 电机状态
- 麦克风
- 通用 RGB 相机
- GelSight Mini 视触觉传感器

这里的 RealSense 与普通 RGB 相机是两套独立设备：

- RealSense 负责 RGB-D / 双红外 / 板载 IMU 链路
- `camera` 指普通 RGB 相机
- `gelsight` 指 GelSight Mini 这类视触觉传感器
- `camera` 与 `gelsight` 虽然底层都可以表现为 RGB 设备，但在 SDK 中作为不同模态管理
- 三者可以同时接入 SDK 并同步录制

### 重要边界：这里有两套不同用途的 IMU

- 项目中的“独立串口 IMU”
  - 当前主要用于 FT 重力补偿
- RealSense D435i 自带的“板载 IMU”
  - 当前已经可以录入 session
  - 当前主要服务于 ORB-SLAM3 的惯性输入

因此，当前项目真实状态应理解为：

- FT 的重力补偿：依赖独立 IMU
- RealSense：可以按配置录制 `color + depth + ir1 + ir2 + imu_samples`
- `rgbd_inertial` / `stereo_inertial`：当前软件上使用 RealSense 板载 IMU

---

## 3. 当前已经能做什么

在软件层面，当前已经形成如下完整闭环：

```text
record -> inspect -> process_trajectory -> export -> validate
```

在 PC 侧数据整理链路上，当前又新增了一条以 session 为真源的标注工作流：

```text
record on Pi -> copy session to PC -> inspect -> annotate -> export -> validate
```

各环节含义如下：

### `record`

负责统一录制多模态 session。

当前已经支持：

- 默认从 `configs/record.yaml` 读取配置
- FT / IMU / RealSense / Motors / Microphone / Camera / GelSight 的统一注册
- `real` / `fake` 两种数据源
- 录制前 ready 等待
- FT / Motors 零点校准
- FT 静态校准与重力补偿
- 首次安装时通过 `sdk_discover_ports.py` 自动发现 FT / IMU / Motors 串口
- 可按配置决定是否在录制结束后自动执行 ORB-SLAM3 轨迹解算

### `inspect`

当前负责输出 session 基础摘要；若 session 已带标注，也会输出 annotation summary。面向人工查看的时间字段默认按墙上时间展示。

### `annotate`

负责在 PC 上对已有 session 执行人工标注。

当前已经支持：

- `session` / `span` / `keyframe` 三类标注
- 项目级 schema 配置与 session 内 `schema.snapshot.json`
- 本地 Web 标注入口 `scripts/sdk_annotate.py`
- 多路图像流同步回看
- 基于 `aligned.sequence_id` 的统一时间轴
- `Synced Signals` 按传感器分组展示，并围绕当前进度条位置显示局部时间窗口
- FT / IMU / Motors 标量曲线查看
- FT 仅显示真实 6 自由度：`force[3] + torque[3]`
- Motors 仅显示真实 6 自由度：`motor_1` / `motor_2` 的 `position`、`velocity`、`torque`
- 麦克风保留原始 `audio` 表示，同时支持整段 session 音频回放与音频摘要特征查看
- 页面时间统一按墙上时间展示
- 右上角显示当前 session duration

标注结果当前直接写回 session 的 `annotations/` 目录。

### `process_trajectory`

负责：

- 从 session 导出 ORB-SLAM3 所需 bundle
- 调用外部 ORB-SLAM3 命令
- 将轨迹结果回写到 session

这个环节既可以独立手动执行，也可以通过 `record.yaml` 中的轨迹配置在录制结束后自动执行。

### `export`

负责将 session 导出为多种数据格式：

- `csv`
- `hdf5`
- `rlds`
- `lerobot`
- `rosbag2`

### `validate`

当前负责执行导出产物的结构级 / 数量级一致性校验，而不是逐字段、逐 payload 的深度比对。

因此，当前仓库已经不是“框架骨架”状态，而是一套已经能在软件侧跑通闭环的系统。

---

## 4. 当前总体结论

截至 2026-03-21，软件侧可以认为已经完成的部分包括：

- 统一多传感器接入
- 统一时间对齐
- 纳秒级时间戳模型与采集时序记录
- 统一 session schema
- 串口类传感器多进程采集
- ready / 校准等待
- FT 重力补偿
- ORB-SLAM3 软件接入
- 首次安装时的串口自动发现与配置写回
- 录制结束后按配置自动执行轨迹解算
- 多格式导出
- session 内置标注与 LeRobot 标注映射
- fake 模式下的闭环验证

仍然属于下一阶段联调任务，而不是“SDK 框架未完成”的事项包括：

- RealSense 真机联调细节
- 真实 ORB-SLAM3 可执行程序联调
- ROS 2 真环境下的 rosbag2 导出验证

---

## 5. 当前架构

### 5.1 运行时主链路

当前标准主链路如下：

```text
sensor adapters
    -> SensorRegistry
    -> BufferedFrameAligner
    -> SessionWriter
    -> inspect / annotate / process_trajectory / export / validate
```

### 5.2 核心目录

```text
UMI_DataCollection/
├── configs/
│   ├── annotation_schema.yaml
│   ├── orbslam3/
│   └── record.yaml
├── docs/
│   ├── orbslam3-io-contract.md
│   └── project-overview.md
├── plan.md
├── scripts/
│   ├── sdk_annotate.py
│   ├── sdk_discover_ports.py
│   ├── sdk_record.py
│   ├── sdk_inspect.py
│   ├── sdk_process_trajectory.py
│   ├── orbslam3_wrapper.py
│   ├── sdk_export.py
│   └── sdk_validate_export.py
├── sdk/
│   ├── annotations/
│   ├── core/
│   ├── sensors/
│   ├── processors/
│   ├── perception/
│   ├── storage/
│   └── exporters/
├── sensors/
│   ├── common/
│   │   ├── base_sensor.py
│   │   ├── serial_base.py
│   │   └── camera_base.py
│   ├── realsense/
│   │   └── realsense_communication.py
│   ├── realsense_sensor.py
│   ├── ft_sensor.py
│   ├── imu_sensor.py
│   ├── motors_sensor.py
│   ├── microphone_sensor.py
│   ├── camera_sensor.py
│   ├── gelsight_sensor.py
│   ├── FTsensor_tool/
│   ├── IMU_tool/
│   ├── motors/
│   ├── microphone/
│   ├── camera/
│   └── gelsight/
└── tests/
```

### 5.3 模块职责

#### `sdk/core/`

负责运行时基础抽象：

- `frame.py`
  - `FrameTime`
  - `SensorFrame`
  - `AlignedFrame`
  - `TrajectoryFrame`
  - 秒级字段与纳秒级字段共存
- `clock.py`
  - host/device 时间捕获
  - capture / arrival / read window 时间记录
- `aligner.py`
  - 多传感器缓冲对齐
  - 优先基于单调纳秒时间进行帧龄计算
- `registry.py`
  - 统一注册、启动、停止、ready 等待
- `session.py`
  - session 基础信息创建

#### `sdk/sensors/`

负责统一传感器适配层：

- `base.py`
  - `SensorAdapter` 抽象接口
- `legacy.py`
  - FT / 独立 IMU / Motors / Microphone / Camera / GelSight 真实适配
- `realsense.py`
  - RealSense 多流真实适配
- `fake.py`
  - fake FT / IMU / RealSense / Motors / Microphone / Camera / GelSight

#### `sensors/`

负责底层设备实现：

- `common/base_sensor.py`
  - 通用线程式传感器抽象
- `common/serial_base.py`
  - 串口传感器多进程采集基类
  - 维护最新帧、运行状态与时间窗口信息
- `common/camera_base.py`
  - 基于 OpenCV `VideoCapture` 的通用图像采集基类
  - 供普通 RGB 相机、GelSight 这类图像设备复用

#### `sdk/processors/`

负责采集中的派生处理：

- `gravity_compensation.py`
  - FT 静态校准
  - 重力补偿

#### `sdk/storage/`

负责 session 的持久化与读取：

- `schema.py`
- `session_writer.py`
- `session_reader.py`

#### `sdk/annotations/`

负责 session 内置标注：

- annotation schema 校验
- `annotations/` 目录读写
- session / span / keyframe CRUD
- 本地 Web 标注服务

#### `sdk/perception/orbslam3/`

负责 ORB-SLAM3 软件接入：

- bundle 导出
- 命令执行
- wrapper 编排与 EuRoC 轨迹格式转换
- 轨迹写回
- session 级轨迹后处理复用

#### `sdk/exporters/`

负责多格式导出：

- `csv`
- `hdf5`
- `rlds`
- `lerobot`
- `rosbag2`

---

## 6. 各传感器接入现状

### 6.1 FT

状态：已完整接入

已完成内容：

- 真实串口 FT 适配
- fake FT 适配
- 串口多进程采集
- 3 秒零点校准
- ready 等待
- FT 重力补偿输入
- 真实落盘 payload 仅保留 `force` 与 `torque` 两组共 6 自由度；旧 session 中的 `force_torque` 仅作为兼容读取路径
- `record.yaml` 中可通过 `ft.enable_torque` 关闭 `torque[3]` 落盘，仅保留 `force[3]`

对应代码：

- `sensors/ft_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`
- `sdk/processors/gravity_compensation.py`

### 6.2 独立 IMU

状态：已完整接入

已完成内容：

- 真实串口 IMU 适配
- fake IMU 适配
- 串口多进程采集
- 当前作为 FT 重力补偿姿态输入

当前边界：

- 这不是 D435i 板载 IMU
- 当前主要用于 FT 重力补偿

对应代码：

- `sensors/imu_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`

### 6.3 RealSense

状态：已接入多流录制与轨迹输入

已完成内容：

- 真实 RealSense 多流适配
- fake RealSense 多流适配
- `color / depth / ir1 / ir2 / imu_samples` 写盘
- `aligned_depth_to_color / pointcloud` 按配置派生
- intrinsics / depth scale / stream 配置元数据
- 作为默认视觉主路径参与 ORB-SLAM3 软件链路
- 低层 bring-up 脚本 `sensors/realsense/realsense_communication.py`

当前边界：

- `aligned_depth_to_color` 与 `pointcloud` 属于可选派生结果，不是必须启用
- 当前 `stereo_inertial` 已接入仓库内置 wrapper 与本地 C++ runner
- `rgbd_inertial` / `stereo` 仍主要保留为外部命令模板接入路径

对应代码：

- `sensors/realsense_sensor.py`
- `sensors/realsense/realsense_communication.py`
- `sdk/sensors/realsense.py`
- `sdk/sensors/fake.py`
- `sdk/perception/orbslam3/bundle.py`

### 6.4 Motors

状态：已接入 SDK

已完成内容：

- 真实电机状态串口适配
- fake Motors 适配
- 串口多进程采集
- 3 秒零点校准
- ready 等待
- 真实落盘 payload 仅保留 `motor_1`、`motor_2` 两组共 6 自由度
- 旧 session 中的 `motor_state` 仅作为兼容读取路径
- `record.yaml` 中可通过 `enable_motor_1 / enable_motor_2` 分别控制是否落盘对应电机状态
- 若 `enable_motors: true` 但两个 motor 开关都为 `false`，录制时会跳过 motors 传感器注册

对应代码：

- `sensors/motors_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`

### 6.5 Microphone

状态：已接入 SDK

已完成内容：

- 真实 microphone 适配
- fake microphone 适配
- `audio` payload 写盘
- 支持 `channels / rate / chunk / device_index`
- 采集侧使用队列缓存并在录制循环中批量落盘，避免完整 session 音频被后续 chunk 覆盖
- 标注端支持整段 session 音频回放，方便确认录音有效性
- `device_index: null` 时自动选择第一个可录音输入设备

对应代码：

- `sensors/microphone_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`

### 6.6 Camera

状态：已接入 SDK 录制层

已完成内容：

- 真实 camera 适配
- fake camera 适配
- `color` payload 写盘
- 支持 `device_index / width / height / fps`

当前边界：

- 已接入录制层
- 可与 RealSense 同时接入和同步录制
- 当前不作为 ORB-SLAM3 默认输入链路

对应代码：

- `sensors/camera_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`

### 6.7 GelSight

状态：已接入 SDK 录制层

已完成内容：

- 真实 GelSight 适配
- fake GelSight 适配
- `image` payload 写盘
- 支持 `device_index / width / height / fps` 配置
- 作为独立 `visuotactile` 模态录制

当前边界：

- 底层接入方式与普通 RGB 设备相似，但在 SDK 中单独作为视触觉模态管理
- 可与 RealSense、普通 RGB 相机同时接入和同步录制
- 当前不作为 ORB-SLAM3 默认输入链路

对应代码：

- `sensors/gelsight_sensor.py`
- `sensors/common/camera_base.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`

---

## 7. 当前录制流程

### 7.1 当前录制入口

当前标准录制入口：

```bash
python scripts/sdk_record.py
```

默认配置文件：

- `configs/record.yaml`

首次安装可选辅助脚本：

- `python scripts/sdk_discover_ports.py`

支持的数据源：

- `--sensor-source real`
- `--sensor-source fake`

支持的启停控制：

- `--disable-ft`
- `--disable-imu`
- `--disable-realsense`
- `--enable-motors`
- `--enable-microphone`
- `--enable-camera`
- `--enable-gelsight`
- `--enable-trajectory` / `--disable-trajectory`

当前更推荐把大部分配置放进 `record.yaml`，CLI 只用于临时覆盖。

支持的主要配置项：

- FT：`--ft-port`
- IMU：`--imu-port`
- 通用录制行为：`--duration` / `--startup-discard-sec` / `--align-rate`
- RealSense：更推荐通过 `record.yaml` 的 `realsense` 段配置多流参数
- Motors：`--motors-port`
- Microphone：`--microphone-device-index` / `--microphone-channels` / `--microphone-rate` / `--microphone-chunk`
- Camera：`--camera-device-index` / `--camera-width` / `--camera-height` / `--camera-fps`
- GelSight：`--gelsight-device-index` / `--gelsight-width` / `--gelsight-height` / `--gelsight-fps`

更推荐写在 `record.yaml` 里的细粒度配置包括：

- `ft.enable_torque`
- `motors.enable_motor_1`
- `motors.enable_motor_2`
- `microphone.device_index`
- `startup_discard_sec`
- `realsense.frame_queue_size`
- `camera.frame_queue_size`
- `gelsight.frame_queue_size`

当前还支持：

- `enable_trajectory` 可以通过 CLI 开关控制
- `trajectory.mode` / `trajectory.command` / `trajectory.output_mode` 当前属于 `record.yaml` 配置字段

也就是说：

- `sdk_record.py` 的 CLI 当前主要直接覆盖传感器启停、端口、分辨率、帧率等常用录制参数
- 轨迹已经被纳入同一份录制配置里，但它是“录制结束后才执行的后处理模态”，不是录制期间实时采集的原始流
- 启用 `enable_trajectory` 后，仍需要由配置文件提供 `trajectory.command`
- 当前仓库内置的 `stereo_inertial` wrapper 推荐配合 `trajectory.output_mode=jsonl_file` 使用

### 7.2 ready / 校准等待

当前行为：

- 所有传感器先启动
- registry 在正式录制前等待传感器 ready
- 无需校准的传感器默认立即 ready
- FT / Motors 在零点校准完成前不会进入正式录制
- 串口打开失败时不会一直维持假 running 状态，避免主流程死等
- 若 `startup_discard_sec > 0`，ready / 校准完成后还会额外丢弃一小段启动阶段数据
- 这段启动阶段数据不会进入 session，也不计入 `duration_sec`

对应代码：

- `sdk/core/registry.py`
- `sdk/sensors/base.py`
- `sensors/common/base_sensor.py`
- `sensors/ft_sensor.py`
- `sensors/motors_sensor.py`
- `sensors/common/serial_base.py`
- `scripts/sdk_record.py`

### 7.2.1 启动阶段数据丢弃

当前推荐默认开启：

- `startup_discard_sec: 0.5`

对应行为：

- 传感器 ready 后，主循环先持续 drain 各路缓存
- 这一阶段不会创建正式时间轴上的 session 数据
- 正式录制时长会从丢弃窗口结束后才开始计时

这个能力主要用于避免：

- RealSense / Camera 刚启动时首几帧不稳定
- FT / IMU / Motors 在 ready 后的极短时间内仍存在同步瞬态
- 这些启动瞬态污染正式数据集开头

### 7.3 FT 重力补偿

当前行为：

- 仅在 FT + 独立 IMU 同时存在时启用
- 先收集静态样本
- 在 aligned 记录中写入补偿结果

当前边界：

- 当前明确依赖独立 IMU
- D435i 板载 IMU 当前不参与 FT 重力补偿

### 7.4 当前依赖边界

- 基础 Python 侧运行依赖至少包括 `numpy`、`pyyaml`、`pyserial`
- RealSense 真机采集需要 `pyrealsense2`；图像 artifact 读取与 ORB-SLAM3 bundle 导出需要 `opencv-python`；麦克风真机采集需要 `pyaudio`；HDF5 导出需要 `h5py`
- `sdk_export.py` / `sdk_validate_export.py` 当前会经由导出模块导入链加载 LeRobot exporter，因此运行这两个入口时通常也建议安装 `pyarrow`
- 当前真实传感器路径会通过 `sensors` 包根入口导入多种传感器模块，依赖隔离尚未完全按模态拆开；这里按当前实现行为描述运行环境准备要求

### 7.5 采集队列与异步写盘

当前录制链路已经针对“多模态 30Hz 下主循环偶发抖动”做了两层优化：

- 视觉链路：
  - `camera` / `realsense` / `gelsight` 使用本地队列缓存
  - 主循环通过 `read_available_frames()` 一次性 drain 多帧
- 串口链路：
  - `ft` / `imu` / `motors` 使用跨进程队列缓存
  - burst 到达时不会只保留 latest packet
- 存储链路：
  - session writer 默认启用异步写盘
  - 图像与数组 artifact 会并行写入
  - JSONL 句柄会复用，降低频繁 open / close 的额外开销

录制结束后，session 会在 `meta.json` 与 `logs/sdk_record.log` 中写入以下诊断信息：

- `sensor_runtime_status`
- `writer_diagnostics`
- `startup_discard`

这些字段可用于区分：

- 设备源头没有产出数据
- 主循环未及时消费缓存
- writer 吞吐不足导致排队

### 7.6 终端操作员摘要

当前 `scripts/sdk_record.py` 在终端收尾阶段会输出结构化摘要，而不是直接打印大段 JSON：

- `Session / 输出目录 / 日志文件`
- `目标录制时长 / 启动阶段丢弃时长`
- `传感器状态` 表格：
  - `产出 / 写入 / 丢弃 / 队列 / 队列峰值`
- `写盘状态` 摘要：
  - `pending / peak / artifact_pending / artifact_peak / mean latency / max latency / error`

完整机器可读信息仍会保留在 `logs/sdk_record.log` 中，便于后续离线排查。

---

## 8. Session 与数据落盘

当前 session 结构为：

```text
session_xxx/
  meta.json
  manifest.json
  annotations/
    manifest.json
    schema.snapshot.json
    session.json
    spans.jsonl
    keyframes.jsonl
  streams/
    ft/
    imu/
    realsense/
    motors/
    microphone/
    camera/
    gelsight/
  aligned/
  trajectory/
  exports/
  logs/
```

说明：

- `annotations/` 只有在执行标注后才会出现
- 标注当前统一锚定到 `aligned.sequence_id`
- `schema.snapshot.json` 用于保证 session 拷贝到另一台 PC 后仍能使用一致的标注字段定义
- 实际创建哪些 `streams/<sensor>/`，取决于本次录制启用了哪些传感器
- 多维数组 payload 会落为 `png` 或 `npy`
- 标量和小向量会直接写入 `frames.jsonl`
- RealSense 的图像、红外、深度和点云会按 payload 类型分别落盘
- `trajectory/` 中只有在启用自动轨迹后处理，或后续手动执行 `sdk_process_trajectory.py` 时，才会出现实际轨迹帧

对应代码：

- `sdk/storage/session_writer.py`
- `sdk/storage/session_reader.py`
- `sdk/storage/schema.py`

---

## 9. ORB-SLAM3 当前接入边界

当前已经完成的软件侧内容：

- bundle 导出
- 外部命令调用
- JSONL 轨迹写回
- `rgbd_inertial` / `stereo` / `stereo_inertial` 模式支持
- 本地 `stereo_inertial` wrapper 与 ORB-SLAM3 C++ 离线 runner 接入

当前边界：

- 默认视觉数据来自 RealSense
- `rgbd_inertial` 与 `stereo_inertial` 当前都使用 RealSense 板载 IMU
- 普通 camera 与 GelSight 都是独立录制模态，可与 RealSense 同时存在，但当前不作为 ORB-SLAM3 默认输入链路
- 当前仓库内置 wrapper 第一版仅覆盖 `stereo_inertial`
- `rgbd_inertial` / `stereo` 仍保留为外部命令模板接入路径

对应代码与文档：

- `sdk/perception/orbslam3/bundle.py`
- `sdk/perception/orbslam3/command_runner.py`
- `sdk/perception/orbslam3/wrapper.py`
- `sdk/perception/orbslam3/pipeline.py`
- `scripts/orbslam3_wrapper.py`
- `configs/orbslam3/stereo_inertial.example.json`
- `ThirdParty/ORB_SLAM3/Examples/Stereo-Inertial/sdk_stereo_inertial_offline.cc`
- `docs/orbslam3-io-contract.md`

---

## 10. 导出能力

当前已经具备：

- `csv`
- `hdf5`
- `rlds`
- `lerobot`
- `rosbag2` 代码路径

当前边界：

- `lerobot` 当前已经支持自动读取 `session/annotations/`，并把 `session` / `span` / `keyframe` 标注映射到扁平字段
- `rosbag2` 的真实验证仍依赖 ROS 2 环境
- `validate` 当前主要检查导出目录、关键文件以及 step / frame 数等结构级 / 数量级一致性
- `rosbag2` 在 `validate` 环节当前只检查输出目录存在且非空，不代表已经完成更强的语义一致性验证

---

## 11. 测试状态

当前已经覆盖的软件侧测试包括：

- 对齐逻辑测试
- annotation schema / CRUD / Web 标注服务测试
- 重力补偿测试
- session writer / reader 测试
- 导出校验测试
- 配置文件加载与 CLI 覆盖测试
- 自动串口发现测试
- fake 端到端测试
- fake 全传感器 registry 扩展测试
- legacy `sensors` 包根入口保持最小暴露面的约束测试

对应测试：

- `tests/test_aligner.py`
- `tests/test_annotations.py`
- `tests/test_gravity_compensation.py`
- `tests/test_session_writer.py`
- `tests/test_export_validation.py`
- `tests/test_record_config_loading.py`
- `tests/test_port_discovery.py`
- `tests/test_sdk_e2e.py`
- `tests/test_sensor_registry_extensions.py`
- `tests/test_legacy_cleanup.py`

---

## 12. 当前验收口径

当前软件系统已经达到的验收标准：

- `scripts/sdk_record.py` 默认读取 `configs/record.yaml`
- `scripts/sdk_record.py` 能生成规范 session
- `scripts/sdk_record.py` 能按配置启用 FT / IMU / RealSense / Motors / Microphone / Camera / GelSight
- `scripts/sdk_record.py --sensor-source fake` 能在无真机条件下生成多模态 session
- Motors / Microphone / Camera / GelSight 已进入 SDK 主录制入口
- `scripts/sdk_discover_ports.py` 能在首次安装时按启用顺序引导识别 FT / IMU / Motors 串口并写回配置
- 需要校准的传感器会在 ready / 零点校准后再进入正式录制
- `scripts/sdk_inspect.py` 能读取 session 摘要
- `scripts/sdk_annotate.py` 能在 PC 上启动本地 Web 标注器，并把结果写入 `session/annotations/`
- `scripts/sdk_process_trajectory.py` 能导出 ORB-SLAM3 bundle 并写回轨迹
- `enable_trajectory=true` 时能在录制结束后自动执行同一套轨迹处理链
- `scripts/sdk_export.py` 能导出多种格式
- `scripts/sdk_export.py --format lerobot` 能自动消费 session 标注
- `scripts/sdk_validate_export.py` 能执行导出产物的结构级 / 数量级一致性校验
- 新增模态时不需要改动核心主循环

---

## 13. 当前仍待推进的事项

以下事项属于下一阶段联调或增强任务：

1. RealSense 真机联调细节验证
2. 真实 ORB-SLAM3 可执行程序联调
3. ROS 2 环境下的 rosbag2 真实导出验证
4. 新增模态在真实硬件条件下的长期稳定性验证

---

## 14. 一句话总结

当前仓库已经是一套可运行的统一多模态采集与标注 SDK。FT / 独立 IMU / RealSense / Motors / Microphone / Camera / GelSight 都已进入录制体系，采集默认由 `configs/record.yaml` 驱动，session 拷贝到 PC 后可通过 `scripts/sdk_annotate.py` 进行人工标注，`LeRobot` 导出会自动消费这些标注。
