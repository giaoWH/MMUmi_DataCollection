# 项目总览

## 1. 项目是什么

这是一个面向多模态机器人数据采集的统一 SDK。项目当前的重点，不再是做一个一次性的采集脚本，而是维护一套长期可扩展的软件系统，用于：

1. 统一接入多种真实传感器与 fake 数据源
2. 在统一时间轴下记录和对齐多模态数据
3. 将原始流、对齐结果、轨迹结果写入统一 session
4. 为 ORB-SLAM3 提供 RGB-D / RGB-D-Inertial 软件输入
5. 将内部 session 导出为多种数据集格式

当前项目已经完成收口，只保留一套新的 SDK 主系统。

当前推荐使用方式已经切换到“配置文件优先”：

1. 编辑 `configs/record.yaml`
2. 首次安装时按需运行 `scripts/sdk_discover_ports.py`
3. 运行 `scripts/sdk_record.py`
4. 按配置决定是否在录制结束后自动执行轨迹解算

---

## 2. 当前纳入的传感器

当前已经进入 SDK 录制层的模态：

- 六维力传感器 FT
- 独立串口 IMU
- RealSense RGB-D
- 电机状态
- 麦克风
- 通用 RGB 相机

这里的 RealSense 与普通 RGB 相机是两套独立设备：

- RealSense 负责 RGB-D 链路
- `camera` 指普通 RGB 相机
- 两者可以同时接入 SDK 并同步录制

### 重要边界：这里有两套不同用途的 IMU

这是当前最容易混淆、也最需要写清楚的一点：

- 项目中的“独立串口 IMU”
  - 当前主要用于 FT 重力补偿
  - 也是当前 `rgbd_inertial` 软件链路里实际使用的惯性来源
- RealSense D435i 自带的“板载 IMU”
  - 未来应主要服务于 SLAM
  - 当前还没有单独接入 SDK

因此，当前项目真实状态应理解为：

- FT 的重力补偿：依赖独立 IMU
- RealSense：当前只接入 RGB + Depth
- `rgbd_inertial`：当前软件上仍是 “RealSense RGB-D + 独立 IMU”
- 还不是 “D435i RGB-D + D435i 板载 IMU” 的原生惯性组合

---

## 3. 当前已经能做什么

在软件层面，当前已经形成如下完整闭环：

```text
record -> inspect -> process_trajectory -> export -> validate
```

各环节含义如下：

### `record`

负责统一录制多模态 session。

当前已经支持：

- 默认从 `configs/record.yaml` 读取配置
- FT / IMU / RealSense / Motors / Microphone / Camera 的统一注册
- `real` / `fake` 两种数据源
- 录制前 ready 等待
- FT / Motors 零点校准
- FT 静态校准与重力补偿
- 首次安装时通过 `sdk_discover_ports.py` 自动发现 FT / IMU / Motors 串口
- 可按配置决定是否在录制结束后自动执行 ORB-SLAM3 轨迹解算

### `inspect`

负责读取 session 摘要、传感器信息和统计信息。

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

负责校验导出结果与内部 session 是否一致。

因此，当前仓库已经不是“框架骨架”状态，而是一套已经能在软件侧跑通闭环的系统。

---

## 4. 当前总体结论

截至 2026-03-20，软件侧可以认为已经完成的部分包括：

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
- fake 模式下的闭环验证

仍然属于下一阶段联调任务，而不是“SDK 框架未完成”的事项包括：

- RealSense 真机联调细节
- D435i 板载 IMU 接入
- 用 D435i 板载 IMU 替换当前 SLAM 惯性输入
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
    -> inspect / process_trajectory / export / validate
```

### 5.2 核心目录

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
│   ├── ft_sensor.py
│   ├── imu_sensor.py
│   ├── motors_sensor.py
│   ├── microphone_sensor.py
│   ├── camera_sensor.py
│   ├── serial_base.py
│   ├── FTsensor_tool/
│   ├── IMU_tool/
│   ├── motors/
│   ├── microphone/
│   └── camera/
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
  - FT / 独立 IMU / Motors / Microphone / Camera 真实适配
- `realsense.py`
  - RealSense RGB-D 真实适配
- `fake.py`
  - fake FT / IMU / RealSense / Motors / Microphone / Camera

#### `sensors/`

负责底层设备实现：

- `base_sensor.py`
  - 通用线程式传感器抽象
- `serial_base.py`
  - 串口传感器多进程采集基类
  - 维护最新帧、运行状态与时间窗口信息

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

#### `sdk/perception/orbslam3/`

负责 ORB-SLAM3 软件接入：

- bundle 导出
- 命令执行
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
- 当前 `rgbd_inertial` 软件链路里仍在使用这套 IMU 数据

对应代码：

- `sensors/imu_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`
- `sdk/perception/orbslam3/bundle.py`

### 6.3 RealSense

状态：已接入 RGB-D

已完成内容：

- 真实 RealSense RGB-D 适配
- fake RealSense RGB-D 适配
- color / depth 写盘
- intrinsics / depth scale 元数据
- 作为默认视觉主路径参与 ORB-SLAM3 软件链路

当前边界：

- 当前只接入 RGB + Depth
- 尚未接入 D435i 板载 IMU
- 当前它是 ORB-SLAM3 默认使用的 RGB-D 输入链路
- 因此当前还没有形成“D435i RGB-D + D435i IMU”的原生惯性 SLAM 组合

对应代码：

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
- `motor_state`、`motor_1`、`motor_2` payload

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
- `CameraSensor` 仍不从 `sensors/__init__.py` 暴露，以保持 legacy 包入口最小化

对应代码：

- `sensors/camera_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`
- `sensors/__init__.py`

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

当前更推荐把大部分配置放进 `record.yaml`，CLI 只用于临时覆盖。

支持的主要配置项：

- FT：`--ft-port`
- IMU：`--imu-port`
- RealSense：`--realsense-width` / `--realsense-height` / `--realsense-fps`
- Motors：`--motors-port`
- Microphone：`--microphone-device-index` / `--microphone-channels` / `--microphone-rate` / `--microphone-chunk`
- Camera：`--camera-device-index` / `--camera-width` / `--camera-height` / `--camera-fps`

当前还支持：

- `enable_trajectory`
- `trajectory.command`

也就是说，轨迹已经被纳入同一份录制配置里，但它是“录制结束后才执行的后处理模态”，不是录制期间实时采集的原始流。

### 7.2 ready / 校准等待

当前行为：

- 所有传感器先启动
- registry 在正式录制前等待传感器 ready
- 无需校准的传感器默认立即 ready
- FT / Motors 在零点校准完成前不会进入正式录制
- 串口打开失败时不会一直维持假 running 状态，避免主流程死等

对应代码：

- `sdk/core/registry.py`
- `sdk/sensors/base.py`
- `sensors/base_sensor.py`
- `sensors/ft_sensor.py`
- `sensors/motors_sensor.py`
- `sensors/serial_base.py`

### 7.3 FT 重力补偿

当前行为：

- 仅在 FT + 独立 IMU 同时存在时启用
- 先收集静态样本
- 在 aligned 记录中写入补偿结果

当前边界：

- 当前明确依赖独立 IMU
- D435i 板载 IMU 当前不参与 FT 重力补偿

---

## 8. Session 与数据落盘

当前 session 结构为：

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
  aligned/
  trajectory/
  exports/
  logs/
```

说明：

- 实际创建哪些 `streams/<sensor>/`，取决于本次录制启用了哪些传感器
- 多维数组 payload 会落为 `png` 或 `npy`
- 标量和小向量会直接写入 `frames.jsonl`
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
- `rgbd` / `rgbd_inertial` 模式支持

当前边界：

- 默认视觉数据来自 RealSense
- 理想的 `rgbd_inertial` 惯性来源应为 D435i 板载 IMU
- 当前 `rgbd_inertial` 惯性来源仍是独立 IMU
- 普通 camera 是独立录制模态，可与 RealSense 同时存在，但当前不作为 ORB-SLAM3 默认输入链路

对应代码与文档：

- `sdk/perception/orbslam3/bundle.py`
- `sdk/perception/orbslam3/command_runner.py`
- `sdk/perception/orbslam3/pipeline.py`
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

- `rosbag2` 的真实验证仍依赖 ROS 2 环境

---

## 11. 测试状态

当前已经覆盖的软件侧测试包括：

- 对齐逻辑测试
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
- `scripts/sdk_record.py` 能按配置启用 FT / IMU / RealSense / Motors / Microphone / Camera
- `scripts/sdk_record.py --sensor-source fake` 能在无真机条件下生成多模态 session
- Motors / Microphone / Camera 已进入 SDK 主录制入口
- `scripts/sdk_discover_ports.py` 能在首次安装时按启用顺序引导识别 FT / IMU / Motors 串口并写回配置
- 需要校准的传感器会在 ready / 零点校准后再进入正式录制
- `scripts/sdk_inspect.py` 能读取 session 摘要
- `scripts/sdk_process_trajectory.py` 能导出 ORB-SLAM3 bundle 并写回轨迹
- `enable_trajectory=true` 时能在录制结束后自动执行同一套轨迹处理链
- `scripts/sdk_export.py` 能导出多种格式
- `scripts/sdk_validate_export.py` 能校验导出结果
- 新增模态时不需要改动核心主循环

---

## 13. 当前仍待推进的事项

以下事项属于下一阶段联调或增强任务：

1. RealSense 真机联调细节验证
2. D435i 板载 IMU 接入
3. 用 D435i 板载 IMU 替换当前 SLAM 惯性输入
4. 真实 ORB-SLAM3 可执行程序联调
5. ROS 2 环境下的 rosbag2 真实导出验证
6. 新增模态在真实硬件条件下的长期稳定性验证

---

## 14. 一句话总结

当前仓库已经是一套可运行的统一多模态采集 SDK。FT / 独立 IMU / RealSense / Motors / Microphone / Camera 都已进入录制体系，采集默认由 `configs/record.yaml` 驱动，首次安装可选自动发现串口，并能在录制结束后按配置决定是否自动解算 ORB-SLAM3 轨迹。
