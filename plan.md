# SDK 执行计划

更新时间：2026-03-11

## 1. 目标

本项目当前只保留一套新的多模态采集 SDK，目标如下：

1. 时间对齐多模态信息
2. 统一传感器驱动接入
3. ORB-SLAM3 调用与轨迹位姿输出
4. 建立长期可扩展的框架
5. 生成主流格式数据集：RLDS / LeRobot / HDF5 / ROS Bag

当前纳入范围的模态：

- 六维力传感器
- IMU
- RealSense

约束说明：

- 当前验收以“软件系统闭环”完成为准
- RealSense 真机联调、真实 ORB-SLAM3 程序联调、ROS 环境验证不作为软件开发阻塞项
- 仓库内不再保留旧链路与新 SDK 两套长期系统

---

## 2. 当前结论

截至 2026-03-11，SDK 的软件侧目标已经完成。

已经形成的标准闭环为：

```text
record -> inspect -> process_trajectory -> export -> validate
```

并且满足以下收口决策：

1. 新 SDK 已加入静态校准流程
2. 新 SDK 已加入重力补偿
3. 新 SDK 已加入扁平 CSV 导出
4. 原有普通相机主流程已删除
5. 旧链路中缺失值补齐、任务级终端监控、专项任务编排统一采用新 SDK 标准

---

## 3. 完成状态

### 3.1 时间对齐与统一数据模型

状态：已完成

已完成内容：

- `FrameTime`、`SensorFrame`、`AlignedFrame`、`TrajectoryFrame`
- `BufferedFrameAligner`
- 对齐诊断字段：`present_sensors`、`dropped_sensors`、`age_stats`

对应代码：

- `sdk/core/frame.py`
- `sdk/core/clock.py`
- `sdk/core/aligner.py`

### 3.2 统一传感器接入层

状态：已完成

已完成内容：

- FT / IMU 统一适配
- RealSense RGB-D 统一适配
- fake FT / IMU / RealSense 适配
- 统一注册与启动停止管理
- RealSense 元数据与流配置字段约定

对应代码：

- `sdk/sensors/base.py`
- `sdk/sensors/legacy.py`
- `sdk/sensors/realsense.py`
- `sdk/sensors/fake.py`
- `sdk/core/registry.py`

### 3.3 Session schema 与存储

状态：已完成

已完成内容：

- `meta.json`
- `manifest.json`
- `streams/*/frames.jsonl`
- `aligned/frames.jsonl`
- `trajectory/frames.jsonl`
- `logs/`

对应代码：

- `sdk/constants.py`
- `sdk/core/session.py`
- `sdk/storage/schema.py`
- `sdk/storage/session_writer.py`
- `sdk/storage/session_reader.py`

### 3.4 采集处理能力

状态：已完成

已完成内容：

- 静态校准
- 重力补偿
- 重力补偿结果写入 `aligned` 记录

对应代码：

- `sdk/processors/gravity_compensation.py`
- `scripts/sdk_record.py`

### 3.5 ORB-SLAM3 软件接入

状态：已完成

已完成内容：

- bundle 导出
- 命令模板变量约定
- JSONL 轨迹输出约定
- session 轨迹写回
- 配置文件和环境变量接入
- 坐标系与外参接入点文档

对应代码与文档：

- `sdk/perception/orbslam3/base.py`
- `sdk/perception/orbslam3/bundle.py`
- `sdk/perception/orbslam3/command_runner.py`
- `sdk/perception/orbslam3/pipeline.py`
- `scripts/sdk_process_trajectory.py`
- `docs/orbslam3-io-contract.md`
- `configs/orbslam3/rgbd_inertial.example.json`

### 3.6 导出能力

状态：已完成

已完成内容：

- `csv`
- `hdf5`
- `rlds`
- `lerobot`
- `rosbag2` 导出代码路径
- 导出一致性校验器

对应代码：

- `sdk/exporters/csv_exporter.py`
- `sdk/exporters/hdf5_exporter.py`
- `sdk/exporters/rlds_exporter.py`
- `sdk/exporters/lerobot_exporter.py`
- `sdk/exporters/rosbag2_exporter.py`
- `sdk/exporters/validation.py`
- `scripts/sdk_export.py`
- `scripts/sdk_validate_export.py`

### 3.7 工具链与测试

状态：已完成

已完成内容：

- `scripts/sdk_record.py`
- `scripts/sdk_inspect.py`
- `scripts/sdk_export.py`
- `scripts/sdk_process_trajectory.py`
- `scripts/sdk_validate_export.py`
- fake 端到端回归测试
- 对齐、重力补偿、导出校验测试

对应代码：

- `tests/test_aligner.py`
- `tests/test_gravity_compensation.py`
- `tests/test_session_writer.py`
- `tests/test_export_validation.py`
- `tests/test_sdk_e2e.py`
- `tests/test_legacy_cleanup.py`

### 3.8 旧链路收口

状态：已完成

已完成内容：

- 删除 `DataCollection.py`
- 删除旧重力补偿文件
- 删除普通相机链路
- 删除普通相机测试工具
- 不再对外暴露 `CameraSensor`

对应变更：

- `sensors/__init__.py`

---

## 4. 当前验收口径

当前软件系统已达到以下验收标准：

- `scripts/sdk_record.py` 能生成规范 session
- `scripts/sdk_record.py --sensor-source fake` 能在无真机条件下生成 FT / IMU / RealSense session
- `scripts/sdk_inspect.py` 能读取 session 摘要
- `scripts/sdk_process_trajectory.py` 能导出 ORB-SLAM3 bundle 并写回轨迹
- `scripts/sdk_export.py` 能导出多种格式
- `scripts/sdk_validate_export.py` 能校验导出结果
- 新增模态时不需要改动核心主循环

---

## 5. 不阻塞软件完成度的外部事项

以下事项仍需你后续接入外部环境后验证，但不再属于“SDK 软件尚未完成”：

1. RealSense 真机联调
2. 真实 ORB-SLAM3 可执行程序联调
3. ROS Bag 2 在本机 ROS 环境中的真实导出验证

这些项目应作为下一阶段测试与联调清单，而不是当前代码仓库的开发待办。

---

## 6. 一句话判断

**SDK 软件系统已经完成收口，仓库内只保留一套新 SDK。下一步不再是补框架，而是接入真机与外部程序进行联调测试。**
