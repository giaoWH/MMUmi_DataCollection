# 项目总览

## 1. 项目是什么

这是一个面向多模态数据采集的 SDK，当前统一围绕以下三类传感器构建：

- 六维力传感器
- IMU
- RealSense

SDK 的目标不是只完成一次采集脚本，而是提供一套可持续扩展的软件系统，用于：

1. 统一采集多模态原始数据
2. 在统一时间轴下完成对齐
3. 为 ORB-SLAM3 准备输入并写回轨迹结果
4. 将内部 session 导出为主流数据集格式
5. 在没有真机时，也能用 fake 数据跑通完整软件闭环

当前仓库已经完成“只保留一套新 SDK 系统”的收口，旧主脚本和普通相机链路已移除。

---

## 2. 当前已经能做什么

在软件层面，当前已经可以稳定跑通下面这条链路：

```text
record -> inspect -> process_trajectory -> export -> validate
```

对应含义：

- `record`
  - 录制 FT / IMU / RealSense 的统一 session
  - 支持 `real` 和 `fake` 两种数据源
  - 支持静态校准和重力补偿
- `inspect`
  - 查看 session 摘要与传感器信息
- `process_trajectory`
  - 导出 ORB-SLAM3 bundle
  - 调用外部 ORB-SLAM3 wrapper
  - 将轨迹写回 session
- `export`
  - 导出为 `csv`、`hdf5`、`rlds`、`lerobot`、`rosbag2`
- `validate`
  - 检查导出结果与 session 是否一致

因此，这个仓库现在已经不是“框架骨架”，而是一套可以在无真机条件下端到端验证的软件系统。

---

## 3. 项目目录

当前核心目录如下：

```text
UMI_DataCollection/
├── configs/
│   └── orbslam3/
├── docs/
│   ├── orbslam3-io-contract.md
│   └── project-overview.md
├── plan.md
├── scripts/
│   ├── sdk_record.py
│   ├── sdk_inspect.py
│   ├── sdk_export.py
│   ├── sdk_process_trajectory.py
│   └── sdk_validate_export.py
├── sdk/
│   ├── config.py
│   ├── constants.py
│   ├── logging.py
│   ├── core/
│   ├── exporters/
│   ├── perception/
│   ├── processors/
│   ├── sensors/
│   └── storage/
├── sensors/
│   ├── ft_sensor.py
│   ├── imu_sensor.py
│   ├── serial_base.py
│   ├── FTsensor_tool/
│   └── IMU_tool/
└── tests/
```

目录职责可以概括为：

- `sdk/`
  - 真正的核心实现
- `scripts/`
  - 面向使用者的 CLI 入口
- `configs/`
  - ORB-SLAM3 等外部程序配置示例
- `docs/`
  - 设计边界、迁移结论、使用说明
- `sensors/`
  - 保留的 FT / IMU 底层旧驱动来源和调试工具
- `tests/`
  - 软件闭环与关键能力回归测试

---

## 4. SDK 核心模块

### 4.1 `sdk/core/`

负责运行时基础抽象：

- `frame.py`
  - 定义 `FrameTime`、`SensorFrame`、`AlignedFrame`、`TrajectoryFrame`
- `clock.py`
  - 统一 host/device 时间捕获
- `aligner.py`
  - 多传感器缓冲对齐
  - 输出 `present_sensors`、`dropped_sensors`、`age_stats` 等诊断信息
- `registry.py`
  - 统一管理传感器注册、启动、停止
- `session.py`
  - 创建 session 基础信息

### 4.2 `sdk/sensors/`

负责统一传感器适配层：

- `base.py`
  - `SensorAdapter` 抽象接口
- `legacy.py`
  - FT / IMU 旧驱动适配
- `realsense.py`
  - RealSense RGB-D 适配
- `fake.py`
  - fake FT / IMU / RealSense 数据源

这层的作用是把“具体硬件输入”与“SDK 主流程”解耦。后续新增模态时，优先新增 adapter，而不是改主循环。

### 4.3 `sdk/processors/`

负责采集过程中的派生处理：

- `gravity_compensation.py`
  - 静态校准
  - 重力补偿
  - 为对齐结果生成补偿后的力数据

### 4.4 `sdk/storage/`

负责 session 存储与读取：

- `schema.py`
  - session schema
- `session_writer.py`
  - 写入原始流、对齐结果、轨迹结果
- `session_reader.py`
  - 读取 session 与 artifact

当前 session 目录布局固定为：

```text
session_xxx/
  meta.json
  manifest.json
  streams/
    ft/
    imu/
    realsense/
  aligned/
  trajectory/
  exports/
  logs/
```

### 4.5 `sdk/perception/orbslam3/`

负责 ORB-SLAM3 的软件接入：

- `bundle.py`
  - 从 session 导出 ORB-SLAM3 输入 bundle
- `command_runner.py`
  - 调用外部命令并解析 JSONL 轨迹
- `pipeline.py`
  - 串联 bundle 导出、命令执行、轨迹写回

当前推荐模式：

- `rgbd`
- `rgbd_inertial`

### 4.6 `sdk/exporters/`

负责导出与校验：

- `csv_exporter.py`
- `hdf5_exporter.py`
- `rlds_exporter.py`
- `lerobot_exporter.py`
- `rosbag2_exporter.py`
- `validation.py`

其中：

- `csv`、`hdf5`、`rlds`、`lerobot` 已经完成软件侧验证
- `rosbag2` 已有代码路径，但真实验证依赖 ROS 环境

---

## 5. CLI 入口

项目的标准入口都在 `scripts/`：

- `scripts/sdk_record.py`
  - 录制 session
  - 支持 `real / fake`
  - 支持静态校准和重力补偿
- `scripts/sdk_inspect.py`
  - 输出 session 摘要
- `scripts/sdk_process_trajectory.py`
  - 处理 ORB-SLAM3 轨迹
- `scripts/sdk_export.py`
  - 导出目标格式
- `scripts/sdk_validate_export.py`
  - 校验导出结果

如果你只想快速确认软件系统是否正常，最短路径是先跑 `sdk_record.py` 的 fake 模式。

---

## 6. 当前推荐工作流

### 6.1 无真机验证

推荐顺序：

1. `python scripts/sdk_record.py --sensor-source fake --duration 1`
2. `python scripts/sdk_inspect.py <session_dir>`
3. `python scripts/sdk_process_trajectory.py <session_dir> ...`
4. `python scripts/sdk_export.py <session_dir> --format csv`
5. `python scripts/sdk_validate_export.py <session_dir> --format csv`

这条路径的意义是：

- 不需要真机
- 不需要真实 ORB-SLAM3
- 先验证架构、schema、导出链路和 CLI 边界都正确

### 6.2 接真机前要替换什么

后续接入真机时，主要需要替换的是：

- FT / IMU / RealSense 的真实配置
- ORB-SLAM3 的真实命令模板
- ORB-SLAM3 词典与相机配置文件路径

原则上不需要再改：

- session schema
- 核心对齐逻辑
- 导出框架
- CLI 主入口结构

---

## 7. 重要文档

- [plan.md](/home/windiff/Code/UMI_DataCollection/plan.md)
  - 当前阶段目标与完成状态
- [docs/orbslam3-io-contract.md](/home/windiff/Code/UMI_DataCollection/docs/orbslam3-io-contract.md)
  - ORB-SLAM3 输入输出约定

---

## 8. 当前边界

### 软件侧已经完成

- 统一时间与对齐
- 统一 session schema
- FT / IMU / RealSense 接入框架
- fake 全流程
- 静态校准与重力补偿
- ORB-SLAM3 软件接入边界
- 多格式导出
- 导出一致性校验
- 旧链路清理

### 仍依赖外部环境

- RealSense 真机联调
- 真实 ORB-SLAM3 程序联调
- ROS Bag 2 真实环境验证

这些属于下一阶段联调任务，不再属于当前仓库的软件开发缺口。

---

## 9. 一句话总结

当前仓库已经是一套单一的新 SDK 系统，重点不再是继续补框架，而是使用现有主链路去接真机、接真实 ORB-SLAM3，并完成外部联调验证。
