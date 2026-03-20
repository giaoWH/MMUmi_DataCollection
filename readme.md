UMI Data Collection SDK

这是一个面向多模态机器人数据采集的统一 SDK。项目当前已经从早期的单脚本、单设备调试方式，收口到一套可持续扩展的软件系统，用于：

1. 统一接入多种真实传感器与 fake 数据源
2. 在统一时间轴下记录多模态原始数据
3. 将原始流、对齐结果、轨迹结果写入标准 session
4. 为 ORB-SLAM3 提供 RGB-D / RGB-D-Inertial 输入
5. 将 session 导出为常见机器人与具身智能数据格式

当前系统已经具备完整的软件闭环：

```text
record -> inspect -> process_trajectory -> export -> validate
```

当前推荐使用方式也已经从“长串 CLI 参数”转成“以配置文件为主”：

1. 编辑 `configs/record.yaml`
2. 首次安装时按需运行 `python scripts/sdk_discover_ports.py`
3. 运行 `python scripts/sdk_record.py`
4. 按配置决定是否在录制结束后自动解算轨迹

## 当前支持的传感器

当前已经接入 SDK 录制层的模态包括：

- 六维力传感器 FT
- 独立串口 IMU
- RealSense RGB-D
- 电机状态
- 麦克风
- 通用 RGB 相机
- GelSight Mini 视触觉传感器

其中视觉链路需要明确区分：

- `realsense` 指 D435i 这类 RGB-D 设备
- `camera` 指独立的普通 RGB 相机
- `gelsight` 指 GelSight Mini 这类视触觉传感器
- `camera` 与 `gelsight` 虽然底层都可以表现为 RGB 相机，但在 SDK 中被视作不同模态
- 三者是并列的独立模态，可以同时启用并同步录制

其中有一个很重要的边界需要明确：

- 项目里的“独立串口 IMU”当前主要用于 FT 重力补偿
- RealSense D435i 自带的板载 IMU 未来应当主要服务于 SLAM
- 当前项目还没有把 D435i 板载 IMU 单独接入 SDK
- 因此当前 `rgbd_inertial` 软件链路里的惯性数据来源，仍然是项目中的独立 IMU，而不是 D435i 板载 IMU

换句话说：

- `FT + 独立 IMU`：当前已经打通，用于重力补偿
- `D435i RGB + Depth`：当前已经打通，用于视觉主路径
- `D435i 板载 IMU`：当前还未接入

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

RealSense RGB-D、普通 RGB 相机和 GelSight 是三条独立的视觉/视触觉接入链路，可以同时接入并同步录制。
当前 ORB-SLAM3 默认使用 RealSense 作为 RGB-D 输入；普通 RGB 相机和 GelSight 当前仍主要作为独立录制模态，而不是 ORB-SLAM3 默认输入主链。

## 当前已经具备的能力

### 1. 统一传感器接入

当前已经支持：

- 真实传感器适配
- fake 数据源适配
- 统一 registry 管理启动、停止与状态
- 新增模态时尽量只新增 adapter，不修改核心主循环
- 串口类传感器（FT / IMU / Motors）使用多进程采集，降低纯 Python 解析与主进程之间的 GIL 竞争
- 基于 USB / UVC 的图像类设备通过统一 `camera_base` 复用采集逻辑，便于继续扩展 GelSight 或其他相机形态设备

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

这意味着当前 SDK 已经不再只是“记录一个 host_time 浮点秒”，而是会把采集时序本身写入 session。

### 3. 校准与 ready 等待

当前已经接入“正式录制前等待设备 ready”的机制：

- 所有传感器先启动
- registry 在正式写盘前等待需要校准的传感器 ready
- 不需要校准的传感器默认立即 ready
- FT 会先进行零点校准
- Motors 会先进行零点校准

这样可以避免：

- FT 在静止基线阶段就开始写正式数据
- Motors 在零点漂移阶段污染正式录制
- 多个传感器在不同起点进入采集主循环

### 4. FT 重力补偿

当前 FT 处理链已经支持：

- 零点校准
- 静态校准
- 重力补偿

这里依赖的是“独立串口 IMU”的姿态输入，而不是 D435i 板载 IMU。

### 4.1 当前时间戳实现状态

当前软件侧已经完成的时间戳改造包括：

- `FrameTime` 纳秒级字段落地
- `SessionWriter / SessionReader` 保存并读取纳秒级时间信息
- HDF5 / rosbag2 导出保留纳秒级时间字段
- 串口类传感器记录 `capture / arrival / read window`

因此，当前系统已经具备更适合多模态精细对齐的时间基础设施。

### 5. ORB-SLAM3 软件接入

当前已经完成的软件侧能力：

- 从 session 导出 ORB-SLAM3 bundle
- 调用外部 ORB-SLAM3 命令
- 接收 JSONL 轨迹结果
- 将轨迹写回 session

当前推荐模式：

- `rgbd`
- `rgbd_inertial`

但需要再次强调：

- 当前 `rgbd_inertial` 里的惯性来源仍是独立 IMU
- 还不是“D435i 原生 RGB-D + 板载 IMU”组合

### 6. 多格式导出

当前已具备：

- `csv`
- `hdf5`
- `rlds`
- `lerobot`
- `rosbag2` 代码路径

其中：

- `csv` / `hdf5` / `rlds` / `lerobot` 已完成软件侧验证
- `rosbag2` 的真实验证还依赖本机 ROS 2 环境

## 目录结构

```text
UMI_DataCollection/
├── configs/
│   ├── orbslam3/
│   └── record.yaml
├── docs/
│   ├── orbslam3-io-contract.md
│   └── project-overview.md
├── plan.md
├── scripts/
│   ├── sdk_discover_ports.py
│   ├── sdk_record.py
│   ├── sdk_inspect.py
│   ├── sdk_export.py
│   ├── sdk_process_trajectory.py
│   └── sdk_validate_export.py
├── sdk/
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
conda create -n umi_sdk python=3.10
conda activate umi_sdk
pip install numpy pyyaml
```

本文档默认后续命令都在 `conda activate umi_sdk` 之后执行。

按需安装的依赖：

- RealSense 采集：`pip install pyrealsense2`
- 图像处理：`pip install opencv-python`
- 麦克风：需要 `pyaudio`
- HDF5 导出：`pip install h5py`
- LeRobot 导出：`pip install pyarrow`
- ROS Bag 2 导出：需要真实 ROS 2 环境

## 快速开始

### 1. 推荐：先编辑配置文件

录制入口现在会默认自动读取：

- `configs/record.yaml`

也就是说，通常只需要先把这个文件里的传感器开关、串口、输出目录和时长改好，然后直接运行：

```bash
python scripts/sdk_record.py
```

如果你想使用另一份配置文件，也可以显式指定：

```bash
python scripts/sdk_record.py --config configs/record.yaml
```

CLI 参数仍然保留，但更适合只做少量临时覆盖。

如果你希望把 ORB-SLAM3 轨迹也作为一种可选模态纳入录制配置，可以在 `record.yaml` 中设置：

- `enable_trajectory: true`
- `trajectory.command`

这样 `python scripts/sdk_record.py` 会在采集结束后自动执行轨迹解算并把结果写回当前 session；如果 `enable_trajectory: false`，则只录制原始数据，不会自动解算轨迹。

一个最小配置思路如下：

- `enable_ft / enable_imu / enable_realsense` 控制默认主链路
- `enable_camera` 控制独立普通 RGB 相机
- `enable_gelsight` 控制独立视触觉模态 GelSight
- `enable_motors / enable_microphone` 控制可选扩展模态
- `enable_trajectory` 控制录制结束后是否自动执行 ORB-SLAM3

### 2. 首次安装时可选：自动发现串口

如果你第一次装设备，不想手动去试 `FT / IMU / Motors` 的串口号，可以运行：

```bash
python scripts/sdk_discover_ports.py
```

这个脚本会：

- 读取 `configs/record.yaml`
- 按 `record.yaml` 里已启用的串口传感器顺序引导你逐个插上设备
- 检测新出现的串口
- 把识别出的 `port` 自动写回 `record.yaml`

说明：

- 这是首次安装或重新接线时的可选工具，不是每次采集前都要运行
- 当前只会自动发现串口类传感器：`FT`、`IMU`、`Motors`
- `RealSense`、`Microphone`、`Camera`、`GelSight` 不走这个串口发现流程
- 写回前会自动生成一个 `record.yaml.bak` 备份

### 3. 最短 fake 闭环

先跑默认 fake 录制：

```bash
python scripts/sdk_record.py --sensor-source fake --duration 2
```

这会生成包含：

- FT
- IMU
- RealSense

的 fake session。

如果你要连独立普通 RGB 相机链路也一起验证，可以额外打开 `--enable-camera`，这不会替代 RealSense，而是并行多录一个 `camera` stream。

如果你想连视触觉链路一起验证，也可以再额外打开 `--enable-gelsight`，并行多录一个 `gelsight` stream。

### 4. 验证新增传感器接入路径

如果想连 motors / microphone / camera / gelsight 的 SDK 接入路径一起验证：

```bash
python scripts/sdk_record.py \
  --sensor-source fake \
  --duration 2 \
  --enable-motors \
  --enable-microphone \
  --enable-camera \
  --enable-gelsight
```

### 5. 查看 session 摘要

```bash
python scripts/sdk_inspect.py sessions/session_<YOUR_SESSION_ID>
```

### 6. 处理轨迹

如果你没有在 `record.yaml` 里开启 `enable_trajectory`，也可以事后手动处理轨迹：

```bash
python scripts/sdk_process_trajectory.py sessions/session_<YOUR_SESSION_ID> \
  --mode rgbd_inertial \
  --command "python -c \"import json,sys; sys.stdout.write(json.dumps({'timestamp':0.0, 'position':[0,0,0], 'quaternion':[1,0,0,0]}) + '\\n')\""
```

`sdk_process_trajectory.py` 也支持直接读取 `record.yaml` 里的 `trajectory` 段：

```bash
python scripts/sdk_process_trajectory.py sessions/session_<YOUR_SESSION_ID> --config configs/record.yaml
```

### 7. 导出与校验

```bash
python scripts/sdk_export.py sessions/session_<YOUR_SESSION_ID> --format csv
python scripts/sdk_validate_export.py sessions/session_<YOUR_SESSION_ID> --format csv
```

## 录制入口说明

当前主录制入口是：

```bash
python scripts/sdk_record.py
```

当前默认会自动加载：

- `configs/record.yaml`

如果你希望切换到另一份配置文件：

```bash
python scripts/sdk_record.py --config path/to/record.yaml
```

首次安装时，如果你想自动写入串口配置，可以先执行：

```bash
python scripts/sdk_discover_ports.py
```

如果你只想临时覆盖少量配置，当前支持的主要开关有：

- `--config`
- `--sensor-source real`
- `--sensor-source fake`
- `--enable-ft`
- `--disable-ft`
- `--enable-imu`
- `--disable-imu`
- `--enable-realsense`
- `--disable-realsense`
- `--enable-motors`
- `--disable-motors`
- `--enable-microphone`
- `--disable-microphone`
- `--enable-camera`
- `--disable-camera`
- `--enable-gelsight`
- `--disable-gelsight`

当前支持的主要配置项有：

- FT
  - `--ft-port`
- IMU
  - `--imu-port`
- RealSense
  - `--realsense-width`
  - `--realsense-height`
  - `--realsense-fps`
- Motors
  - `--motors-port`
- Microphone
  - `--microphone-device-index`
  - `--microphone-channels`
  - `--microphone-rate`
  - `--microphone-chunk`
- Camera
  - `--camera-device-index`
  - `--camera-width`
  - `--camera-height`
  - `--camera-fps`
- GelSight
  - `--gelsight-device-index`
  - `--gelsight-width`
  - `--gelsight-height`
  - `--gelsight-fps`

但日常推荐仍然是直接编辑 `configs/record.yaml`，CLI 只做临时覆盖。

真机录制最基本示例：

```bash
python scripts/sdk_record.py
```

如果要把 camera / gelsight / microphone / motors 一起打开：

```bash
# edit configs/record.yaml
# then run
python scripts/sdk_record.py
```

## 当前 session 结构

典型 session 目录如下：

```text
session_xxx/
  meta.json
  manifest.json
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

- 实际会出现哪些 `streams/<sensor>/`，取决于本次录制启用了哪些传感器
- 二维和三维数组 payload 会落为 `png` 或 `npy`
- 小向量和标量会直接进入 `frames.jsonl`
- `trajectory/` 中是否有实际轨迹内容，取决于你是否启用了 `enable_trajectory`，或者之后手动执行了 `sdk_process_trajectory.py`

## 当前测试状态

当前软件侧已经覆盖的测试方向包括：

- 对齐逻辑
- 重力补偿
- session writer / reader
- 导出一致性
- fake 端到端闭环
- fake 全传感器 registry 扩展测试
- legacy `sensors` 包根入口保持最小暴露面的约束测试

## 当前还没做完的事

以下事项属于后续联调或增强方向，不代表 SDK 主体框架未完成：

1. RealSense 真机细节联调
2. D435i 板载 IMU 接入
3. 用 D435i 板载 IMU 替换当前 SLAM 惯性输入
4. 真实 ORB-SLAM3 程序联调
5. ROS 2 环境下 rosbag2 真导出验证
6. 新增模态的真实硬件长期稳定性验证

## 一句话总结

当前项目已经是一套可运行的统一多模态采集 SDK。FT / 独立 IMU / RealSense / Motors / Microphone / Camera / GelSight 都已进入录制体系，当前推荐通过 `configs/record.yaml` 驱动采集，并可在首次安装时用 `sdk_discover_ports.py` 自动写入串口，在录制结束后按配置决定是否自动解算 ORB-SLAM3 轨迹。
