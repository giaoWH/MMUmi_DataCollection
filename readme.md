UMI Data Collection SDK

这是一个面向多模态机器人数据采集的统一 SDK。项目当前已经从早期的单脚本、单设备调试方式，收口到一套配置驱动的软件系统，用于：

1. 统一接入多种真实传感器与 fake 数据源
2. 在统一时间轴下记录多模态原始数据
3. 将原始流、对齐结果、轨迹结果写入按 task 批次分组的标准 session
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
3. 在采集端运行 `conda activate umi_sdk`
4. 运行 `python scripts/sdk_record.py`
5. 将 `sessions/<task>/<index>` 拷贝到 PC
6. 在 PC 上运行 `conda activate umi_sdk`
7. 运行 `python scripts/sdk_process_trajectory.py <session_dir> --config configs/record.yaml`
8. 在 PC 上运行 `python scripts/sdk_annotate.py <session_dir>`
9. 在 PC 上运行 `python scripts/sdk_export.py <session_dir> --format lerobot`

## Session 存储格式（实验版 v2）

当前实验分支已经引入一版新的 session 存储格式，用于减少视觉帧碎文件数量，并保持上层标注、导出、轨迹处理接口基本不变。

当前约定如下：

- `camera` 的彩色图像写入 `streams/camera/media.mp4`
- `gelsight` 的图像写入 `streams/gelsight/media.mp4`
- `realsense.color` 写入 `streams/realsense/color.mp4`
- `microphone` 的音频写入 `streams/camera/media.mp4` 的主音轨
- `realsense.depth` 继续按逐帧 `npy` artifact 保存
- `aligned/frames.jsonl` 与 `trajectory/frames.jsonl` 结构保持不变
- 每个 sensor 仍保留 `streams/<sensor>/frames.jsonl`，但其 payload 已从“原始数组 / PNG 路径”改成“媒体帧 / 音频片段索引引用”

新的 payload 引用类型包括：

- `mp4_frame`
  - 表示某一路 MP4 中的一帧图像
- `mp4_audio`
  - 表示主视频音轨中的一段 PCM 音频
- `npy`
  - 继续用于 `realsense.depth` 这类无损数组

当前实验版的明确边界：

- 不兼容旧版逐帧 session
- 不把 `realsense.depth` 编进 MP4
- 不给 `gelsight` 或 `realsense color` 复制音轨
- `camera` 是唯一主视频与主音轨容器

当前实验分支的默认媒体编码策略是：

- 视频编码使用标准 `H.264`
- 视频像素格式默认使用 `yuv420p`
- 视频编码参数默认是 `profile=high`、`preset=veryfast`、`crf=20`
- 主视频容器默认启用 `+faststart`
- 音频当前仍优先使用 `ALAC`

这样做的原因是：

- 让树莓派和常见播放器更容易直接打开录制出来的 MP4
- 同时尽量保留音频 chunk 的精确回读语义，避免因为有损音频编码引入额外延迟或切片偏差

如果需要覆盖默认值，可以在 `record.yaml` 的 `media_encoding` 下配置：

- `video_codec`
- `video_pixel_format`
- `video_profile`
- `video_preset`
- `video_crf`
- `audio_codec`
- `movflags`

上层兼容策略是：

- `SessionReader` 继续返回和旧版一致的 `SensorFrame` 视图
- 标注工具、导出器、ORB-SLAM3 bundle 仍通过 Reader 读取 payload，而不直接操作 MP4
- 因此上层业务代码仍然按 `frame_id` / `aligned sequence_id` 工作

录制配置当前还支持一部分“模态内细粒度开关”：

- `ft.enable_torque`
  - 关闭后只保存 `force[3]`
- `motors.enable_motor_1`
  - 关闭后不保存 `motor_1` 的 `position / velocity / torque`
- `motors.enable_motor_2`
  - 关闭后不保存 `motor_2` 的 `position / velocity / torque`
- `microphone.device_index`
  - 设为 `null` 时会自动选择第一个可录音输入设备；显式指定索引时会强制绑定该设备
- `camera.flip_horizontal` / `camera.flip_vertical`
  - 控制普通 RGB 相机在进入 session 前是否做水平 / 垂直翻转
- `realsense.serial_number` / `camera.device_index` / `gelsight.device_index`
  - 可通过 `python scripts/sdk_discover_ports.py` 自动发现并写回配置
- `startup_discard_sec`
  - 传感器 ready 后继续丢弃开头一小段采集数据；默认 `0.5s`
  - 这段数据不会进入 session，也不计入 `duration_sec`
- `realsense.frame_queue_size` / `camera.frame_queue_size` / `gelsight.frame_queue_size`
  - 控制视觉链路的本地缓存队列深度，用于降低主循环瞬时抖动带来的帧覆盖风险

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

当前录制入口还支持一个更明确的“开头数据丢弃窗口”：

- `startup_discard_sec > 0` 时，传感器 ready 后不会立刻开始写 session
- 这段时间主循环只会持续消费各路缓存，避免把设备启动瞬态写进正式数据
- `duration_sec` 的计时会从丢弃窗口结束后才开始

### 4. FT 重力补偿

当前 FT 处理链已经支持：

- 零点校准
- 静态校准
- 重力补偿
- `record.yaml` 中通过 `ft.enable_torque` 控制是否落盘 `torque[3]`

这里依赖的仍然是“独立串口 IMU”的姿态输入，而不是 D435i 板载 IMU。

当前 FT 落盘规则：

- 默认保存 `force[3] + torque[3]`
- `ft.enable_torque: false` 时，仅保存 `force[3]`

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
- `frame_queue_size`
- `color / depth / infrared` 三组独立分辨率与帧率
- `imu.accel_fps / imu.gyro_fps / imu.max_samples_per_frame`
- `derived.enable_aligned_depth_to_color / enable_pointcloud / pointcloud_colored`

当前普通 RGB 相机 / GelSight 也支持对应的 `frame_queue_size` 配置，用于在主循环偶发抖动时保留更多待写入帧。

### 5.2 采集缓冲与写盘

当前录制链路已经补上了两类关键缓冲：

- `camera` / `realsense` / `gelsight` 使用进程内队列缓存，主循环会批量 drain，而不再只拿 latest frame
- `ft` / `imu` / `motors` 使用跨进程队列缓存，主循环同样会批量 drain，降低串口 burst 时的覆盖风险
- session writer 默认启用异步写盘，并并行写入图像 / 数组 artifact，降低 `cv2.imwrite` / `np.save` 对主循环的阻塞

录制结束后，session 会额外保存：

- `notes.sensor_runtime_status`
- `notes.writer_diagnostics`
- `notes.startup_discard`

这些字段可用于排查“设备没出帧”“主循环没及时消费”以及“写盘吞吐不足”等问题。

### 5.1 Motors 录制补充

当前 `motors` 配置支持：

- `enable_motor_1`
- `enable_motor_2`

对应行为：

- `enable_motor_1: false` 时，不保存 `motor_1.position / velocity / torque`
- `enable_motor_2: false` 时，不保存 `motor_2.position / velocity / torque`
- 若 `enable_motors: true` 但 `enable_motor_1` 与 `enable_motor_2` 同时为 `false`，录制时会跳过 motors 传感器注册

### 6. ORB-SLAM3 软件接入

当前已经完成的软件侧能力：

- 从 session 导出 ORB-SLAM3 bundle
- 调用外部 ORB-SLAM3 命令
- 接收 JSONL 轨迹结果
- 将轨迹写回 session

当前官方支持的模式：

- `stereo_inertial`

其中：

- `stereo_inertial` 使用 `ir1 + ir2 + imu_samples`

当前 `trajectory` 配置的职责边界也已经明确：

- `realsense` 负责选择录哪些流
- `trajectory.mode` 当前固定为 `stereo_inertial`
- `trajectory.command` 为空时默认使用仓库内置 wrapper
- `trajectory.env` 负责提供 `ORB_SLAM3_VOCAB`、`ORB_SLAM3_SETTINGS`，可选 `ORB_SLAM3_RUNNER`

这里还需要补充一个使用边界：

- 上述 `trajectory` 配置当前属于 PC 端离线处理使用的 `record.yaml` 字段，而不是 `scripts/sdk_record.py` 的独立 CLI 参数
- `scripts/sdk_record.py` 的 CLI 当前主要直接覆盖传感器启停、端口、分辨率、帧率等常用录制参数
- `enable_trajectory` 已废弃，录制结束后不会自动解算轨迹

在 session v2 实验格式下，ORB-SLAM3 相关链路仍保持以下行为：

- `realsense.color` 会先由 `SessionReader` 从 `streams/realsense/color.mp4` 解码
- `realsense.depth` 继续直接从 `npy` artifact 读取
- bundle 导出结果依然是离线 ORB-SLAM3 可消费的图像文件与 `imu.csv`
- ORB-SLAM3 wrapper 不需要知道 session 内部是否使用了 MP4
- 当前官方链路只导出并处理 `stereo_inertial`

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
- 同一 session 重跑轨迹时会覆盖旧的 `trajectory/frames.jsonl`

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

## 录制结束后的操作员摘要

当前 `scripts/sdk_record.py` 在终端收尾阶段会输出一段结构化摘要，便于现场操作人员快速检查：

- session id / 输出目录 / 日志文件
- 目标录制时长与启动阶段丢弃时长
- 各传感器的 `产出 / 写入 / 丢弃 / 队列 / 队列峰值`
- writer 的 `pending / peak / artifact_pending / artifact_peak / mean latency / max latency`

终端只保留面向操作员的摘要；完整 JSON 诊断仍会写入 session 内的 `logs/sdk_record.log`。

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
- 右上角提供 `PageUp` / `PageDown`，可在同级目录的多个 session 间连续翻页标注
- 翻页前必须先保存；若当前表单存在未保存修改，页面会弹出提示并拒绝切换
- 页面内提供 `Close` 按钮，可在标注完成后直接关闭本地标注服务
- `session` 表单编辑
- `span` / `keyframe` 的 `id` 可编辑
- `span` 的 `start/end sequence` 会自动联动 `start/end time`
- `keyframe` 的 `sequence` 会自动联动 `aligned time`
- `span` 创建、更新、删除
- `keyframe` 创建、更新、删除
- 按 schema 动态生成表单控件
- 带有 `other` 选项的 `enum` / `multi_enum` 字段会要求输入自定义标签；最终落盘的是手动输入内容，而不是字面值 `other`
- `Save` / `New` / `Delete` 按钮具备处理中与成功/失败反馈，便于操作员确认点击已生效

## 目录结构

```text
UMI_DataCollection/
├── configs/
│   ├── annotation_schema.yaml
│   ├── orbslam3/
│   └── record.yaml
├── docs/
│   ├── session-data-layout.md
│   ├── orbslam3-io-contract.md
│   └── project-overview.md
├── plan.md
├── scripts/
│   ├── sdk_annotate.py
│   ├── sdk_discover_cameras.py
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

其中：

- `scripts/sdk_discover_ports.py` 是统一设备发现入口，支持 `--scope all|serial|visual`
- `scripts/sdk_discover_cameras.py` 是兼容入口，内部等价于 `scripts/sdk_discover_ports.py --scope visual`

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

仓库根目录现在提供两份环境文件：

- `environment.yml`
  - 推荐给使用 Conda / Miniforge 的场景
- `requirements.txt`
  - 适合已经有 Python 3.11 环境时直接 `pip install -r requirements.txt`

推荐方式：

```bash
conda env create -f environment.yml
conda activate umi_sdk
```

如果你只想在已有环境里补 Python 依赖，也可以：

```bash
python -m pip install -r requirements.txt
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

环境文件说明：

- `environment.yml` 基于当前 `conda umi_sdk` 环境整理，适合完整复现
- `requirements.txt` 保留主要 Python 包版本，适合快速安装
- 若目标机器安装 `pyrealsense2` 或 `PyAudio` 失败，通常需要先补系统级依赖或优先使用 `conda`

当前导出入口还有一个实现边界：

- `sdk_export.py` / `sdk_validate_export.py` 当前会经由导出模块导入链加载 LeRobot exporter，因此即使不导出 LeRobot，运行这两个入口时通常也建议安装 `pyarrow`

RealSense 真机 bring-up 可直接运行：

```bash
python sensors/realsense/realsense_communication.py
```

若后续需要接真实 ORB-SLAM3，可将其手动放入 `ThirdParty/` 并通过 `trajectory.command` / `trajectory.env` 对接。本仓库当前不会强依赖该第三方目录存在。
