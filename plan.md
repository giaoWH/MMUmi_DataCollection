# SDK 执行计划

更新时间：2026-03-21

## 1. 项目目标

本项目当前只保留一套新的多模态采集 SDK，目标如下：

1. 统一接入多种真实传感器与 fake 数据源
2. 在统一时间轴下完成多模态对齐
3. 将原始流、对齐结果、轨迹结果写入统一 session
4. 为 ORB-SLAM3 提供稳定的 `rgbd_inertial`、`stereo`、`stereo_inertial` 输入
5. 导出为主流机器人与具身智能数据格式
6. 保持主循环稳定，新增长模态时尽量只新增 adapter

---

## 2. 当前纳入范围

当前已经纳入 SDK 录制层的模态：

- 六维力传感器 FT
- 独立串口 IMU
- RealSense D435i 多流数据
- 电机状态
- 麦克风
- 通用 RGB 相机
- GelSight Mini 视触觉传感器

当前推荐的使用入口已经固定为配置文件驱动：

- 默认编辑 `configs/record.yaml`
- 首次安装或重新接线时可选运行 `scripts/sdk_discover_ports.py`
- 正式录制统一通过 `scripts/sdk_record.py`
- 轨迹可通过 `enable_trajectory` 决定是否在录制结束后自动解算

需要特别说明的边界：

- RealSense、普通 RGB 相机与 GelSight 是三条独立视觉/视触觉链路，可以同时接入和同步录制
- 当前 ORB-SLAM3 默认使用 RealSense 作为输入来源
- 普通 RGB 相机和 GelSight 当前都不是 ORB-SLAM3 默认视觉主链
- `realsense` 作为一个设备接入，但设备内部可按配置录制 `color`、`depth`、`ir1`、`ir2`、`imu_samples`
- RealSense 可选派生结果包括 `aligned_depth_to_color` 与 `pointcloud`
- 项目中的独立 IMU 当前主要用于 FT 重力补偿
- `rgbd_inertial` / `stereo_inertial` 当前使用 D435i 板载 IMU 作为 SLAM 惯性输入

---

## 3. 当前总体结论

截至 2026-03-21，项目的软件侧主目标已经完成，并形成如下标准闭环：

```text
record -> inspect -> process_trajectory -> export -> validate
```

当前可以认为已经完成的软件能力：

- 统一多传感器接入
- 统一 session schema
- 统一时间对齐
- 纳秒级时间戳模型与采集时序记录
- 串口类传感器多进程采集
- FT / Motors 零点校准与 ready 等待
- FT 静态校准与重力补偿
- RealSense 多流录制与低层 bring-up
- ORB-SLAM3 软件侧接入
- 录制结束后按配置自动执行轨迹解算
- 多格式导出
- fake 模式下的闭环验证

当前不再作为软件完成度阻塞项的内容：

- RealSense 真机联调细节
- 真实 ORB-SLAM3 程序联调
- ROS 2 真环境下的 rosbag2 验证

---

## 4. 当前架构

### 4.1 运行时主链路

标准主链路为：

```text
sensor adapters
    -> SensorRegistry
    -> BufferedFrameAligner
    -> SessionWriter
    -> inspect / process_trajectory / export / validate
```

主入口：

- `scripts/sdk_discover_ports.py`
- `scripts/sdk_record.py`
- `scripts/sdk_inspect.py`
- `scripts/sdk_process_trajectory.py`
- `scripts/orbslam3_wrapper.py`
- `scripts/sdk_export.py`
- `scripts/sdk_validate_export.py`

### 4.2 核心模块职责

- `sdk/core/`
  - `frame.py`：统一数据模型与纳秒级时间字段
  - `clock.py`：统一 host/device 时间捕获
  - `aligner.py`：基于统一时间模型的多传感器缓冲对齐
  - `registry.py`：统一注册、启动、停止、ready 等待
  - `session.py`：创建 session 元信息
- `sdk/sensors/`
  - `base.py`：adapter 抽象接口
  - `legacy.py`：真实 FT / IMU / Motors / Microphone / Camera / GelSight 适配
  - `realsense.py`：真实 RealSense 多流适配
  - `fake.py`：fake FT / IMU / RealSense / Motors / Microphone / Camera / GelSight
- `sensors/`
  - `common/base_sensor.py`：线程式基础传感器抽象
  - `common/serial_base.py`：串口传感器多进程采集基类
  - `common/camera_base.py`：相机类设备通用采集基类
  - `realsense_sensor.py`：底层 D435i 采集封装
  - `realsense/realsense_communication.py`：RealSense 真机 bring-up / 连通性测试脚本
- `sdk/processors/`
  - `gravity_compensation.py`：静态校准与重力补偿
- `sdk/storage/`
  - `schema.py`、`session_writer.py`、`session_reader.py`
- `sdk/perception/orbslam3/`
  - bundle 导出、命令执行、轨迹写回、session 后处理
- `sdk/exporters/`
  - `csv` / `hdf5` / `rlds` / `lerobot` / `rosbag2`

---

## 5. 传感器接入现状

### 5.1 FT

状态：已完整接入

已完成内容：

- 真实串口 FT 适配
- fake FT 适配
- 串口多进程采集
- 3 秒零点校准
- 校准完成前 ready 等待
- 重力补偿输入

### 5.2 独立 IMU

状态：已完整接入

已完成内容：

- 真实串口 IMU 适配
- fake IMU 适配
- 串口多进程采集
- 当前作为 FT 重力补偿的姿态输入

当前边界：

- 当前项目里的这一套 IMU 不是 D435i 板载 IMU
- 当前它主要服务于 FT 重力补偿，而不是 ORB-SLAM3 惯性输入

### 5.3 RealSense

状态：已接入多流录制与轨迹输入

已完成内容：

- 真实 RealSense 多流适配
- fake RealSense 多流适配
- `color / depth / ir1 / ir2 / imu_samples` 写入 session
- `aligned_depth_to_color / pointcloud` 按配置派生
- intrinsics / depth scale / stream 配置元数据
- 作为默认视觉主路径参与 ORB-SLAM3 软件链路
- 底层 bring-up 脚本 `sensors/realsense/realsense_communication.py`

当前边界：

- `aligned_depth_to_color` 与 `pointcloud` 属于可选派生结果，不是必须启用
- 当前 `stereo_inertial` 已接入仓库内置 wrapper 与本地 C++ runner
- `rgbd_inertial` / `stereo` 仍主要保留为外部命令模板接入路径

### 5.4 Motors

状态：已接入 SDK

### 5.5 Microphone

状态：已接入 SDK

### 5.6 Camera

状态：已接入 SDK 录制层

当前边界：

- 可与 RealSense 同时接入和同步录制
- 当前不作为 ORB-SLAM3 默认输入链路

### 5.7 GelSight

状态：已接入 SDK 录制层

当前边界：

- 可与 RealSense、普通 RGB 相机同时接入和同步录制
- 当前不作为 ORB-SLAM3 默认输入链路

---

## 6. 采集流程现状

### 6.1 当前录制入口

状态：已完成

主入口：

- `scripts/sdk_record.py`

默认配置入口：

- `configs/record.yaml`

首次安装可选辅助入口：

- `scripts/sdk_discover_ports.py`

当前建议工作流：

1. 编辑 `configs/record.yaml`
2. 首次安装时按需运行 `scripts/sdk_discover_ports.py`
3. 运行 `scripts/sdk_record.py`
4. 如果 `enable_trajectory=true`，则在录制结束后自动执行 ORB-SLAM3 后处理

当前配置职责划分：

- `realsense` 负责选择录哪些流，以及各流 profile
- `trajectory.mode` 负责选择离线 SLAM 消费哪一组已录好的流
- `trajectory.output_mode` 取决于所用 wrapper；当前仓库内置的 `stereo_inertial` wrapper 推荐使用 `jsonl_file`

这里还需要明确：

- `sdk_record.py` 的 CLI 当前主要直接覆盖传感器启停、端口、分辨率、帧率等常用录制参数
- `trajectory.mode` / `trajectory.command` / `trajectory.output_mode` 当前属于 `record.yaml` 配置字段，而不是 `sdk_record.py` 的独立 CLI 参数
- `enable_trajectory` 可通过 CLI 开关控制，但启用后仍需要由配置文件提供 `trajectory.command`

### 6.2 ready / 校准等待

状态：已完成

当前行为：

- 所有传感器先启动
- registry 在正式录制前等待传感器 ready
- 无需校准的传感器默认立即 ready
- FT / Motors 会在零点校准完成后置 ready
- 若串口打开失败，不会一直假装 running，避免等待卡死

### 6.3 重力补偿

状态：已完成

当前行为：

- 仅在 FT + 独立 IMU 同时存在时启用
- 先收集静态段样本
- 在 aligned 记录中写入补偿结果

当前边界：

- 当前明确依赖独立 IMU
- RealSense D435i 板载 IMU 不参与 FT 重力补偿

### 6.4 当前依赖边界

- 基础 Python 侧运行依赖至少包括 `numpy`、`pyyaml`、`pyserial`
- RealSense 真机采集需要 `pyrealsense2`；图像 artifact 读取与 ORB-SLAM3 bundle 导出需要 `opencv-python`；麦克风真机采集需要 `pyaudio`；HDF5 导出需要 `h5py`
- `sdk_export.py` / `sdk_validate_export.py` 当前会经由导出模块导入链加载 LeRobot exporter，因此运行这两个入口时通常也建议安装 `pyarrow`
- 当前真实传感器路径会通过 `sensors` 包根入口导入多种传感器模块，依赖隔离尚未完全按模态拆开；这里按当前实现行为描述环境准备要求

---

## 7. Session 与数据落盘现状

状态：已完成

当前 session 结构：

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

- 实际会创建哪些 `streams/<sensor>/`，取决于本次录制启用了哪些传感器
- 多维数组 payload 会落为 `png` 或 `npy`
- 标量与小向量直接保存在 `frames.jsonl`
- RealSense 的图像、红外、深度和点云会按 payload 类型分别落盘
- `trajectory/frames.jsonl` 只有在启用自动轨迹解算，或之后手动执行 `sdk_process_trajectory.py` 后才会写入实际轨迹结果

---

## 8. ORB-SLAM3 接入现状

状态：已完成软件侧接入

已完成内容：

- bundle 导出
- 外部命令调用
- JSONL 轨迹写回
- `rgbd_inertial` / `stereo` / `stereo_inertial` 模式支持
- `record.yaml` 中 `trajectory` 段配置复用
- 录制结束后按 `enable_trajectory` 自动后处理

当前边界：

- 默认视觉数据来自 RealSense
- `rgbd_inertial` 与 `stereo_inertial` 当前都使用 D435i 板载 IMU
- 普通 camera 与 GelSight 都是独立录制模态，可与 RealSense 同时存在，但当前没有作为 ORB-SLAM3 默认输入链路
- 当前仓库已内置 `stereo_inertial` wrapper、样例配置与本地 ORB-SLAM3 C++ runner
- 第一版内置 wrapper 仅覆盖 `stereo_inertial`
- `rgbd_inertial` / `stereo` 仍保留为外部命令模板接入路径

---

## 9. 导出能力现状

状态：已完成

已完成内容：

- `csv`
- `hdf5`
- `rlds`
- `lerobot`
- `rosbag2` 代码路径
- 导出一致性校验

当前边界：

- `rosbag2` 的真实验证仍依赖本机 ROS 2 环境
- `scripts/sdk_validate_export.py` 当前主要执行导出产物的结构级 / 数量级一致性校验
- `rosbag2` 在 `validate` 环节当前只检查输出目录存在且非空，不代表已经完成更强的语义一致性验证

---

## 10. 测试与验证现状

状态：已完成软件侧基础验证

已覆盖内容：

- 对齐逻辑测试
- 重力补偿测试
- session writer / reader 测试
- ORB-SLAM3 bundle / command / CLI 测试
- RealSense 工具函数测试
- 配置文件加载与 CLI 覆盖测试
- 串口自动发现测试
- fake 端到端测试

---

## 11. 当前验收口径

当前软件系统已达到以下验收标准：

- `scripts/sdk_record.py` 默认读取 `configs/record.yaml`
- `scripts/sdk_record.py` 能生成规范 session
- `scripts/sdk_record.py` 能在统一入口下按配置启用 FT / IMU / RealSense / Motors / Microphone / Camera / GelSight
- `scripts/sdk_record.py --sensor-source fake` 能在无真机条件下生成多模态 session
- RealSense 已支持多流录制与 `trajectory.mode` 三种模式
- `scripts/sdk_process_trajectory.py` 能导出 ORB-SLAM3 bundle 并写回轨迹
- `enable_trajectory=true` 时录制结束后会自动执行同一套轨迹写回逻辑
- `scripts/sdk_export.py` 能导出多种格式
- `scripts/sdk_validate_export.py` 能执行导出产物的结构级 / 数量级一致性校验
- 新增模态时不需要改动核心主循环

---

## 12. 当前仍未完成或待联调事项

以下事项仍需后续继续推进，但不再说明 SDK 框架本身未完成：

1. RealSense 真机联调细节验证
2. 真实 ORB-SLAM3 可执行程序联调
3. ROS 2 环境下的 rosbag2 真实导出验证
4. 新增模态在真实硬件条件下的长期稳定性验证

---

## 13. 一句话判断

**当前项目已经是一套可运行的统一多模态采集 SDK。FT / 独立 IMU / RealSense / Motors / Microphone / Camera / GelSight 都已进入录制体系；采集默认由 `configs/record.yaml` 驱动，RealSense 已支持多流录制，离线轨迹已支持 `rgbd_inertial`、`stereo`、`stereo_inertial` 三种模式。**
