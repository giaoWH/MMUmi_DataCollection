# SDK 执行计划

更新时间：2026-03-20

## 1. 项目目标

本项目当前只保留一套新的多模态采集 SDK，目标如下：

1. 统一接入多种真实传感器与 fake 数据源
2. 在统一时间轴下完成多模态对齐
3. 将原始流、对齐结果、轨迹结果写入统一 session
4. 为 ORB-SLAM3 提供稳定的 RGB-D / RGB-D-Inertial 输入
5. 导出为主流机器人与具身智能数据格式
6. 保持主循环稳定，新增长模态时尽量只新增 adapter

---

## 2. 当前纳入范围

当前已经纳入 SDK 录制层的模态：

- 六维力传感器 FT
- 独立串口 IMU（用于 FT 重力补偿）
- RealSense RGB-D
- 电机状态
- 麦克风
- 通用 RGB 相机

当前推荐的使用入口也已经固定为配置文件驱动：

- 默认编辑 `configs/record.yaml`
- 首次安装或重新接线时可选运行 `scripts/sdk_discover_ports.py`
- 正式录制统一通过 `scripts/sdk_record.py`
- 轨迹可通过 `enable_trajectory` 决定是否在录制结束后自动解算

当前对应的接入形态：

- 真实传感器适配：已接入
- fake 数据源适配：已接入
- 统一 CLI 录制入口：已接入
- 统一 session 写盘：已接入

需要特别说明的边界：

- RealSense RGB-D 与普通 RGB 相机是两条独立视觉链路，可以同时接入和同步录制
- 当前 ORB-SLAM3 默认使用 RealSense 作为 RGB-D 输入
- 普通 RGB 相机当前不是 ORB-SLAM3 默认视觉主链
- RealSense 当前接入的是 RGB + Depth
- 项目中的独立 IMU 当前主要用于 FT 重力补偿
- RealSense D435i 的板载 IMU 未来应作为 SLAM 惯性输入，但目前还没有单独接入 SDK
- 当前 `rgbd_inertial` 模式里的惯性数据来源仍是项目中的独立 IMU，而不是 D435i 板载 IMU

---

## 3. 当前总体结论

截至 2026-03-20，项目的软件侧主目标已经完成，并形成如下标准闭环：

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
- ORB-SLAM3 软件侧接入
- 首次安装时的串口自动发现与配置写回
- 录制结束后按配置自动执行轨迹解算
- 多格式导出
- fake 模式下的闭环验证

当前不再作为软件完成度阻塞项的内容：

- RealSense 真机联调细节
- D435i 板载 IMU 接入
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
  - `legacy.py`：真实 FT / IMU / Motors / Microphone / Camera 适配
  - `realsense.py`：真实 RealSense RGB-D 适配
  - `fake.py`：fake FT / IMU / RealSense / Motors / Microphone / Camera
- `sensors/`
  - `base_sensor.py`：线程式基础传感器抽象
  - `serial_base.py`：串口传感器多进程采集基类
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

对应代码：

- `sensors/ft_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`
- `sdk/processors/gravity_compensation.py`

### 5.2 独立 IMU

状态：已完整接入

已完成内容：

- 真实串口 IMU 适配
- fake IMU 适配
- 串口多进程采集
- 当前作为 FT 重力补偿的姿态输入

当前边界：

- 当前项目里的这一套 IMU 不是 D435i 板载 IMU
- 目前 `rgbd_inertial` 软件链路使用的也是这一套独立 IMU 数据

对应代码：

- `sensors/imu_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`
- `sdk/perception/orbslam3/bundle.py`

### 5.3 RealSense

状态：已部分完整接入

已完成内容：

- 真实 RealSense RGB-D 适配
- fake RealSense RGB-D 适配
- color / depth 写入 session
- intrinsics / depth scale 元数据写入
- ORB-SLAM3 默认视觉主路径

当前边界：

- 已接入 RGB + Depth
- 未接入 D435i 板载 IMU
- 因此当前还没有形成“D435i 彩色/深度 + D435i 板载 IMU”的原生 SLAM 惯性组合

对应代码：

- `sdk/sensors/realsense.py`
- `sdk/sensors/fake.py`
- `sdk/perception/orbslam3/bundle.py`

### 5.4 Motors

状态：已接入 SDK

已完成内容：

- 真实电机状态串口适配
- fake Motors 适配
- 串口多进程采集
- 3 秒零点校准
- 校准完成前 ready 等待
- `motor_state`、`motor_1`、`motor_2` payload 结构

对应代码：

- `sensors/motors_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`

### 5.5 Microphone

状态：已接入 SDK

已完成内容：

- 真实 microphone 适配
- fake microphone 适配
- `audio` payload 写入 session
- 支持 `channels / rate / chunk / device_index` 配置

对应代码：

- `sensors/microphone_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`

### 5.6 Camera

状态：已接入 SDK 录制层

已完成内容：

- 真实 RGB camera 适配
- fake camera 适配
- `color` payload 写入 session
- 支持 `device_index / width / height / fps` 配置

当前边界：

- 已接入录制层
- 可与 RealSense 同时接入和同步录制
- 当前不作为 ORB-SLAM3 默认输入链路
- `sensors/__init__.py` 仍不对外暴露 `CameraSensor`，以保持 legacy 包入口最小化

对应代码：

- `sensors/camera_sensor.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/fake.py`
- `sensors/__init__.py`

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

当前支持：

- `--sensor-source real`
- `--sensor-source fake`
- `--disable-ft`
- `--disable-imu`
- `--disable-realsense`
- `--enable-motors`
- `--enable-microphone`
- `--enable-camera`

当前建议工作流：

1. 编辑 `configs/record.yaml`
2. 首次安装时按需运行 `scripts/sdk_discover_ports.py`
3. 运行 `scripts/sdk_record.py`
4. 如果 `enable_trajectory=true`，则在录制结束后自动执行 ORB-SLAM3 后处理

真实配置项已支持：

- FT：`--ft-port`
- IMU：`--imu-port`
- RealSense：`--realsense-width` / `--realsense-height` / `--realsense-fps`
- Motors：`--motors-port`
- Microphone：`--microphone-device-index` / `--microphone-channels` / `--microphone-rate` / `--microphone-chunk`
- Camera：`--camera-device-index` / `--camera-width` / `--camera-height` / `--camera-fps`

### 6.2 ready / 校准等待

状态：已完成

当前行为：

- 所有传感器先启动
- registry 在正式录制前等待传感器 ready
- 无需校准的传感器默认立即 ready
- FT / Motors 会在零点校准完成后置 ready
- 若串口打开失败，不会一直假装 running，避免等待卡死

对应代码：

- `sdk/core/registry.py`
- `sdk/sensors/base.py`
- `sensors/base_sensor.py`
- `sensors/ft_sensor.py`
- `sensors/motors_sensor.py`
- `sensors/serial_base.py`

### 6.3 重力补偿

状态：已完成

当前行为：

- 仅在 FT + 独立 IMU 同时存在时启用
- 先收集静态段样本
- 在 aligned 记录中写入补偿结果

当前边界：

- 当前明确依赖独立 IMU
- RealSense D435i 板载 IMU 不参与 FT 重力补偿

对应代码：

- `sdk/processors/gravity_compensation.py`
- `scripts/sdk_record.py`

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
  aligned/
  trajectory/
  exports/
  logs/
```

说明：

- 实际会创建哪些 `streams/<sensor>/`，取决于本次录制启用了哪些传感器
- 多维数组 payload 会落为 `png` 或 `npy`
- 标量与小向量直接保存在 `frames.jsonl`
- `trajectory/frames.jsonl` 只有在启用自动轨迹解算，或之后手动执行 `sdk_process_trajectory.py` 后才会写入实际轨迹结果

对应代码：

- `sdk/storage/session_writer.py`
- `sdk/storage/session_reader.py`
- `sdk/storage/schema.py`

---

## 8. ORB-SLAM3 接入现状

状态：已完成软件侧接入

已完成内容：

- bundle 导出
- 外部命令调用
- JSONL 轨迹写回
- `rgbd` / `rgbd_inertial` 模式支持
- `record.yaml` 中 `trajectory` 段配置复用
- 录制结束后按 `enable_trajectory` 自动后处理

当前边界：

- 默认视觉数据来自 RealSense
- 理想的 `rgbd_inertial` 惯性来源应为 D435i 板载 IMU
- 当前 `rgbd_inertial` 的惯性来源仍是独立 IMU
- 普通 camera 是独立录制模态，可与 RealSense 同时存在，但当前没有作为 ORB-SLAM3 默认输入链路

对应代码与文档：

- `sdk/perception/orbslam3/bundle.py`
- `sdk/perception/orbslam3/command_runner.py`
- `sdk/perception/orbslam3/pipeline.py`
- `docs/orbslam3-io-contract.md`

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

对应代码：

- `sdk/exporters/csv_exporter.py`
- `sdk/exporters/hdf5_exporter.py`
- `sdk/exporters/rlds_exporter.py`
- `sdk/exporters/lerobot_exporter.py`
- `sdk/exporters/rosbag2_exporter.py`
- `sdk/exporters/validation.py`

---

## 10. 测试与验证现状

状态：已完成软件侧基础验证

已覆盖内容：

- 对齐逻辑测试
- 重力补偿测试
- session writer / reader 测试
- 导出校验测试
- 配置文件加载与 CLI 覆盖测试
- 串口自动发现测试
- fake 端到端测试
- fake 全传感器 registry 扩展测试
- legacy `sensors` 包根入口保持最小暴露面的约束测试

对应代码：

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

## 11. 旧链路收口现状

状态：已完成

已完成内容：

- 删除 `DataCollection.py`
- 删除旧重力补偿文件
- 收口到统一新 SDK
- `CameraSensor` 不再从 `sensors/__init__.py` 对外暴露，以保持 legacy 包入口最小化
- ORB-SLAM3 当前默认 RGB-D 输入链路使用 RealSense，但不影响普通 camera 独立录制

说明：

- 这里的“收口”不等于 camera 不能采集
- 当前 camera 已经接入 SDK 录制层
- 这里只是不把它当成默认主视觉链路，也不在 legacy `sensors` 包入口继续暴露

---

## 12. 当前验收口径

当前软件系统已达到以下验收标准：

- `scripts/sdk_record.py` 默认读取 `configs/record.yaml`
- `scripts/sdk_record.py` 能生成规范 session
- `scripts/sdk_record.py` 能在统一入口下按配置启用 FT / IMU / RealSense / Motors / Microphone / Camera
- `scripts/sdk_record.py --sensor-source fake` 能在无真机条件下生成多模态 session
- 新增的 Motors / Microphone / Camera 已进入 SDK 主录制入口
- `scripts/sdk_discover_ports.py` 能在首次安装时为 FT / IMU / Motors 自动写回串口配置
- 需要校准的传感器会先完成 ready / 零点校准，再进入正式录制
- `scripts/sdk_inspect.py` 能读取 session 摘要
- `scripts/sdk_process_trajectory.py` 能导出 ORB-SLAM3 bundle 并写回轨迹
- `enable_trajectory=true` 时录制结束后会自动执行同一套轨迹写回逻辑
- `scripts/sdk_export.py` 能导出多种格式
- `scripts/sdk_validate_export.py` 能校验导出结果
- 新增模态时不需要改动核心主循环

---

## 13. 当前仍未完成或待联调事项

以下事项仍需后续继续推进，但不再说明 SDK 框架本身未完成：

1. RealSense 真机联调细节验证
2. D435i 板载 IMU 接入，并替换当前 SLAM 惯性输入
3. 真实 ORB-SLAM3 可执行程序联调
4. ROS 2 环境下的 rosbag2 真实导出验证
5. 新增模态在真实硬件条件下的长期稳定性验证

---

## 14. 一句话判断

**当前项目已经是一套可运行的统一多模态采集 SDK，FT / 独立 IMU / RealSense / Motors / Microphone / Camera 都已进入录制体系；采集默认由 `configs/record.yaml` 驱动，首次安装可选自动发现串口，录制结束后还能按配置决定是否自动解算轨迹。**
