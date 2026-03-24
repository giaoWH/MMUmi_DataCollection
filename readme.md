UMI Data Collection SDK

这是一个面向多模态机器人数据采集的统一 SDK。项目当前已经从早期的单脚本、单设备调试方式，收口到一套配置驱动的软件系统，用于：

1. 统一接入多种真实传感器与 fake 数据源
2. 在统一时间轴下记录多模态原始数据
3. 将原始流、对齐结果、轨迹结果写入标准 session
4. 为 ORB-SLAM3 提供 RGB-D-Inertial / Stereo / Stereo-Inertial 输入
5. 将 session 导出为常见机器人与具身智能数据格式

当前系统已经具备完整的软件闭环：

```text
record -> inspect -> process_trajectory -> export -> validate
```

对于“树莓派采集、PC 标注、PC 导出”的具身学习数据整理场景，当前推荐工作流已经扩展为：

```text
record on Pi -> copy session to PC -> inspect -> annotate -> export -> validate
```

其中当前闭环里的两个能力边界需要明确：

- `inspect` 当前输出 session 基础摘要，时间字段默认按墙上时间展示
- `validate` 当前主要执行导出产物的结构级 / 数量级一致性校验，不做逐字段、逐 payload 的深度比对

当前推荐使用方式已经切换到“配置文件优先”：

1. 编辑 `configs/record.yaml`
2. 首次安装时按需运行 `python scripts/sdk_discover_ports.py`
3. 运行 `python scripts/sdk_record.py`
4. 按配置决定是否在录制结束后自动解算轨迹
5. 将 `sessions/session_xxx` 拷贝到 Linux / Windows PC
6. 在 PC 上运行 `python scripts/sdk_annotate.py <session_dir>`
7. 在 PC 上运行 `python scripts/sdk_export.py <session_dir> --format lerobot`

## 当前支持的传感器

当前已经接入 SDK 录制层的模态包括：

- 六维力传感器 FT
- 独立串口 IMU
- RealSense D435i 多流数据
- 电机状态
- 麦克风
- 通用 RGB 相机
- GelSight Mini 视触觉传感器

其中视觉链路需要明确区分：

- `realsense` 指 D435i 这类 RGB-D / 双红外 / 板载 IMU 设备
- `camera` 指独立的普通 RGB 相机
- `gelsight` 指 GelSight Mini 这类视触觉传感器
- `camera` 与 `gelsight` 虽然底层都可以表现为 RGB 相机，但在 SDK 中被视作不同模态
- 三者是并列的独立模态，可以同时启用并同步录制

当前 RealSense 这条链路的状态是：

- `realsense` 仍然作为一个设备接入 SDK
- 设备内部可以按需录制 `color`、`depth`、`ir1`、`ir2`、`imu_samples`
- 可选派生结果包括 `aligned_depth_to_color` 与 `pointcloud`
- RealSense 板载 IMU 现在已经进入 session，并作为 ORB-SLAM3 惯性输入来源
- 项目里的“独立串口 IMU”仍然保留，当前主要用于 FT 重力补偿

换句话说：

- `FT + 独立 IMU`：当前已经打通，用于重力补偿
- `D435i color/depth/ir/board IMU`：当前已经可以按配置录制
- `aligned_depth_to_color` / `pointcloud`：当前可以按配置派生并落盘

## 当前架构结论

当前仓库已经完成“只保留一套新 SDK 系统”的收口。现在的主结构是：

- `sdk/`
  - 核心运行时、传感器适配、存储、导出、SLAM 软件接入
- `scripts/`
  - 面向使用者的标准 CLI 入口
- `sensors/`
  - 底层旧驱动来源与硬件 bring-up / 调试脚本
- `tests/`
  - 关键能力回归测试

RealSense、普通 RGB 相机和 GelSight 是三条独立的视觉/视触觉接入链路，可以同时接入并同步录制。当前 ORB-SLAM3 默认使用 RealSense 作为输入来源；普通 RGB 相机和 GelSight 当前仍主要作为独立录制模态，而不是 ORB-SLAM3 默认输入主链。

## 当前已经具备的能力

### 1. 统一传感器接入

当前已经支持：

- 真实传感器适配
- fake 数据源适配
- 统一 registry 管理启动、停止与状态
- 新增模态时尽量只新增 adapter，不修改核心主循环
- 串口类传感器（FT / IMU / Motors）使用多进程采集，降低纯 Python 解析与主进程之间的 GIL 竞争
- 基于 USB / UVC 的图像类设备通过统一 `camera_base` 复用采集逻辑，便于继续扩展 GelSight 或其他相机形态设备
- RealSense 底层 bring-up 工具位于 `sensors/realsense/realsense_communication.py`

### 2. 多模态时间对齐

系统内置统一时间模型与对齐能力：

- `FrameTime`
- `SensorFrame`
- `AlignedFrame`
- `BufferedFrameAligner`

可以在对齐结果中看到：

- `present_sensors`
- `missing_sensors`
- `dropped_sensors`
- `age_stats`

当前时间模型还具备以下特性：

- `FrameTime` 同时保留秒级字段与纳秒级字段
- 采集侧显式区分 `host_capture_time`、`host_arrival_time`、`host_read_start/end`
- 对齐器优先基于单调纳秒时间计算帧龄与最近帧
- 录制侧会按目标 `align_rate_hz` 维护对齐时间格，主循环单次变慢时会补齐多个 `aligned` 序号，尽量贴近配置频率

### 3. 校准与 ready 等待

当前已经接入“正式录制前等待设备 ready”的机制：

- 所有传感器先启动
- registry 在正式写盘前等待需要校准的传感器 ready
- 不需要校准的传感器默认立即 ready
- FT 会先进行零点校准
- Motors 会先进行零点校准

### 4. FT 重力补偿

当前 FT 处理链已经支持：

- 零点校准
- 静态校准
- 重力补偿

这里依赖的仍然是“独立串口 IMU”的姿态输入，而不是 D435i 板载 IMU。

### 5. RealSense 多流录制

当前 RealSense 适配已经支持以下原始流：

- `color`
- `depth`
- `ir1`
- `ir2`
- `imu_samples`

并支持以下按需派生结果：

- `aligned_depth_to_color`
- `pointcloud`

`configs/record.yaml` 中的 `realsense` 配置已经支持：

- `enable_color / enable_depth / enable_ir1 / enable_ir2 / enable_imu`
- `color / depth / infrared` 三组独立分辨率与帧率
- `imu.accel_fps / imu.gyro_fps / imu.max_samples_per_frame`
- `derived.enable_aligned_depth_to_color / enable_pointcloud / pointcloud_colored`

### 6. ORB-SLAM3 软件接入

当前已经完成的软件侧能力：

- 从 session 导出 ORB-SLAM3 bundle
- 调用外部 ORB-SLAM3 命令
- 接收 JSONL 轨迹结果
- 将轨迹写回 session

当前支持的模式：

- `rgbd_inertial`
- `stereo`
- `stereo_inertial`

其中：

- `rgbd_inertial` 使用 `color + depth/aligned_depth_to_color + imu_samples`
- `stereo` 使用 `ir1 + ir2`
- `stereo_inertial` 使用 `ir1 + ir2 + imu_samples`

当前 `trajectory` 配置的职责边界也已经明确：

- `realsense` 负责选择录哪些流
- `trajectory.mode` 负责选择离线 SLAM 消费哪一组已录好的流
- `trajectory.output_mode` 取决于所用 wrapper；当前仓库内置的 `stereo_inertial` wrapper 推荐使用 `jsonl_file`

这里还需要补充一个使用边界：

- 上述 `trajectory.mode` / `trajectory.command` / `trajectory.output_mode` 当前属于 `record.yaml` 配置字段，而不是 `scripts/sdk_record.py` 的独立 CLI 参数
- `scripts/sdk_record.py` 的 CLI 当前主要直接覆盖传感器启停、端口、分辨率、帧率等常用录制参数
- `enable_trajectory` 可通过 CLI 开关控制，但启用后仍需要由配置文件提供 `trajectory.command`

当前仓库已经内置一条本地可用的 `stereo_inertial` 接入链：

- Python wrapper：`scripts/orbslam3_wrapper.py`
- C++ 离线 runner：`ThirdParty/ORB_SLAM3/Examples/Stereo-Inertial/sdk_stereo_inertial_offline`
- 推荐样例配置：`configs/orbslam3/stereo_inertial.example.json`
- 推荐 settings：`ThirdParty/ORB_SLAM3/Examples/Stereo-Inertial/RealSense_D435i.yaml`

这条内置链路当前的边界如下：

- 第一版只实现 `stereo_inertial`
- wrapper 从 `bundle_manifest.json` 推导 `stereo_associations.txt` 与 `imu.csv`
- wrapper 调用本地 ORB-SLAM3 runner，并把 `SaveTrajectoryEuRoC()` 的输出转换成 SDK JSONL
- JSONL 中的 `tracking_state` 当前固定写为 `OK`
- `rgbd_inertial` 与 `stereo` 仍可继续通过外部命令模板接入

### 7. 多格式导出

当前已具备：

- `csv`
- `hdf5`
- `rlds`
- `lerobot`
- `rosbag2` 代码路径

其中：

- `csv` / `hdf5` / `rlds` / `lerobot` 已完成软件侧基础验证
- `lerobot` 当前已经支持读取 `session/annotations/` 中的 `session` / `span` / `keyframe` 标注，并映射到 `annotation.session.*`、`annotation.span.*`、`annotation.keyframe.*`
- `rosbag2` 的真实验证还依赖本机 ROS 2 环境
- `validate` 当前主要检查导出目录、关键文件以及 step / frame 数等结构级 / 数量级一致性
- `rosbag2` 在 `validate` 环节当前只检查输出目录存在且非空，不代表已经完成更强的语义一致性验证

### 8. Session 标注

当前 SDK 已新增 session 内置标注能力，标注真源保存在每个 session 的 `annotations/` 目录中，而不是导出结果里。

当前支持的标注范围：

- `session`
- `span`
- `keyframe`

当前设计原则：

- 标注主锚点统一使用 `aligned.sequence_id`
- 标注 schema 支持项目级配置驱动
- 每个 session 会保存 `schema.snapshot.json`，保证跨机器拷贝和复现实验时的可移植性
- 当前 `LeRobot` 导出会自动消费这些标注

`annotations/` 典型结构如下：

```text
session_xxx/
  annotations/
    manifest.json
    schema.snapshot.json
    session.json
    spans.jsonl
    keyframes.jsonl
```

当前推荐的 PC 端标注入口：

```bash
python scripts/sdk_annotate.py /abs/path/to/session_xxx
```

可选指定自定义 schema：

```bash
python scripts/sdk_annotate.py /abs/path/to/session_xxx --schema configs/annotation_schema.yaml
```

本地 Web 标注器当前具备：

- 多路图像流同步回看
- 基于 `aligned.sequence_id` 的统一时间轴
- `Synced Signals` 按传感器分组展示，并围绕当前进度条位置显示局部时间窗口
- FT / IMU / Motors 标量曲线查看
- FT 仅显示真实 6 自由度：`force[3] + torque[3]`
- Motors 仅显示真实 6 自由度：`motor_1` / `motor_2` 的 `position`、`velocity`、`torque`
- 麦克风保留原始 `audio` 表示，同时提供整段 session 音频回放与音频摘要特征查看
- 页面时间统一按墙上时间展示
- 右上角显示当前 session duration
- `session` 表单编辑
- `span` 创建、更新、删除
- `keyframe` 创建、更新、删除
- 按 schema 动态生成表单控件

## 目录结构

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
│   ├── sdk_export.py
│   ├── sdk_process_trajectory.py
│   ├── orbslam3_wrapper.py
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

目录职责简述：

- `sdk/`
  - 核心实现
- `scripts/`
  - 面向使用者的标准入口
- `sdk/annotations/`
  - session 标注 schema、落盘和本地 Web 标注服务
- `sensors/`
  - 底层设备实现、单设备调试工具，以及 `common/` 下的公共采集基类
- `docs/`
  - 项目说明、边界和 I/O 契约
- `tests/`
  - 软件闭环与关键能力回归

## 安装建议

推荐使用 Conda / Miniforge 管理环境。

示例：

```bash
conda create -n umi_sdk python=3.11
conda activate umi_sdk
pip install numpy pyyaml pyserial
```

本文档默认后续命令都在 `conda activate umi_sdk` 之后执行。

按当前实现建议准备的依赖如下：

- 基础 Python 侧运行依赖：`numpy`、`pyyaml`、`pyserial`
- 当前真实传感器路径会通过 `sensors` 包根入口导入多种传感器模块，依赖隔离尚未完全按模态拆开；以下说明按当前实现行为给出，而不是按理想的“完全按需隔离”假设给出
- RealSense 采集：安装 Intel RealSense SDK / `librealsense`，并在 Python 环境里提供 `pyrealsense2`
- 图像处理、PNG artifact 读取、ORB-SLAM3 bundle 导出：`pip install opencv-python`
- 麦克风真机采集：需要 `pyaudio`
- HDF5 导出：`pip install h5py`
- ROS Bag 2 导出：需要真实 ROS 2 环境
- PC 端本地 Web 标注器不依赖额外前端技术栈，默认使用 Python 标准库启动本地 HTTP 服务并调用浏览器

当前导出入口还有一个实现边界：

- `sdk_export.py` / `sdk_validate_export.py` 当前会经由导出模块导入链加载 LeRobot exporter，因此即使不导出 LeRobot，运行这两个入口时通常也建议安装 `pyarrow`

RealSense 真机 bring-up 可直接运行：

```bash
python sensors/realsense/realsense_communication.py
```

若后续需要接真实 ORB-SLAM3，可将其手动放入 `ThirdParty/` 并通过 `trajectory.command` / `trajectory.env` 对接。本仓库当前不会强依赖该第三方目录存在。
